# orchestrator.py — 오케스트레이터: 사용자 입력을 분류해 각 팀 PM에게 envelope로 전달
#
# 5단계 추가: comfyui 경로는 mock PM 대신 실제 ComfyUIAgent + GpuArbiter로 처리한다.
# 오케스트레이터가 GPU 자원 중재자 역할을 겸한다(설계 §3·리스크 6): ComfyUI 작업 전
# Gemma 언로드, 후(성공/실패/타임아웃 무관) 재로드. 전환 중 들어온 다른 요청은
# busy 플래그로 즉시 안내 반환해 데드락 없이 직렬화한다.
# dev/personal 라우팅·분류·fallback 로직은 2~4단계 그대로 유지.

import re
import time

import config
from messages import make_envelope
from ollama_client import call_ollama
from comfyui_agent import ComfyUIAgent
from gpu_arbiter import GpuArbiter
from teams.dev.pm import DevPM
from teams.personal.pm import PersonalPM
from teams.comfyui.pm import ComfyUIPM

VALID_TEAMS = ("dev", "personal", "comfyui")
TEAM_TO_PM = {"dev": "pm_dev", "personal": "pm_assistant", "comfyui": "comfyui"}

# 승인 응답으로 해석할 입력 (대소문자 무시)
APPROVAL_YES = {"y", "yes", "예", "네", "ㅇ", "응"}
APPROVAL_NO = {"n", "no", "아니오", "아니요", "ㄴ"}

# fast-path 인사말 비교용: 구두점·공백 제거 (한글 등 단어 문자만 남김)
_NON_WORD_RE = re.compile(r"[\W_]+")


class Orchestrator:
    """분류(rule-based 1차 → Ollama 2차 보정) 후 팀 PM으로 라우팅.

    분류 실패 시 사용자에게 팀 선택을 요청하고, 다음 입력이 팀 이름이면
    보류 중이던 원본 요청을 그 팀으로 라우팅한다 (fallback, 리스크 2).
    """

    def __init__(self, q_out):
        self.q_out = q_out
        self.pms = {
            "dev": DevPM(q_out),
            "personal": PersonalPM(q_out),
            "comfyui": ComfyUIPM(q_out),  # 2단계 mock (호환 유지용, comfyui 디스패치는 agent가 처리)
        }
        # 5단계: comfyui는 실제 에이전트 + GPU 중재로 처리
        self.comfyui_agent = ComfyUIAgent(q_out=q_out)
        self.arbiter = GpuArbiter(q_out)
        self._pending_input: str | None = None  # 분류 실패 후 팀 선택 대기 중인 원본 요청

    async def handle(self, envelope: dict):
        # request envelope 하나를 끝까지 처리해 result/error를 q_out으로 내보낸다
        task_id = envelope["task_id"]

        # settings_update (UI → pm_dev): 분류 없이 개발팀 PM에 직접 전달
        if envelope.get("type") == "settings_update":
            result = await self.pms["dev"].handle_settings_update(envelope)
            self.q_out.put(result)
            return

        user_input = str(envelope["payload"].get("text", "")).strip()

        # 규약 1: 작업 시작 시 running status emit (UI 활성 표시 트리거)
        self.q_out.put(make_envelope(
            task_id, "orchestrator", "user", "status", "running",
            {"detail": "요청 분석 중"},
        ))

        # 리스크 6: ComfyUI 전환(언로드~재로드) 중에는 다른 요청을 받지 않고 즉시 안내.
        # 이 시점엔 Gemma가 언로드돼 있어 분류용 Ollama 호출도 막아야 함(VRAM 경합 방지).
        # busy는 finally에서 반드시 해제되므로 데드락 없음.
        # 규약 1·5: 거절도 종결이므로 status가 아닌 error로 반환 (태스크 바가 매달리지 않게)
        if self.arbiter.busy:
            self.q_out.put(make_envelope(
                task_id, "orchestrator", "user", "error", "failed",
                {"reason": "comfyui_busy",
                 "message": "이미지 생성 중입니다. 잠시 후 다시 시도하세요."},
            ))
            return

        # 승인 보류 처리 (§5): pm_dev가 approval_request 응답을 기다리는 동안에는
        # 새 작업 분류를 받지 않는다 (분류 fallback 보류와 동시에 존재하지 않게 보장).
        # y/n(예/아니오)이면 approval_response로 변환해 대기 중인 PM에 전달,
        # 그 외 입력은 "승인 대기 중" 안내 후 무시.
        dev_pm = self.pms["dev"]
        # getattr 방어: 테스트가 stub PM을 주입할 수 있음 (test_stage5 TrapPM 등)
        if getattr(dev_pm, "has_pending_approval", lambda: False)():
            lowered = user_input.lower()
            if lowered in APPROVAL_YES or lowered in APPROVAL_NO:
                approved = lowered in APPROVAL_YES
                request_id = dev_pm.pending_request_id()
                dev_pm.resolve_approval(request_id, approved)
                self.q_out.put(make_envelope(
                    task_id, "user", "pm_dev", "approval_response", "success",
                    {"request_id": request_id, "approved": approved},
                ))
                self.q_out.put(make_envelope(
                    task_id, "orchestrator", "user", "result", "success",
                    {"result": "승인 응답 전달됨: " + ("승인" if approved else "거부")},
                ))
            else:
                self.q_out.put(make_envelope(
                    task_id, "orchestrator", "user", "error", "failed",
                    {"reason": "approval_pending",
                     "message": "모델 변경 승인 대기 중입니다. y 또는 n으로 답해주세요."},
                ))
            return

        # fallback 2단계: 직전 분류 실패 → 이번 입력을 팀 선택으로 해석
        if self._pending_input is not None:
            original = self._pending_input
            self._pending_input = None
            if user_input.lower() in VALID_TEAMS:
                await self._dispatch(task_id, user_input.lower(), original)
                return
            # 팀 이름이 아니면 보류 요청을 버리고 새 요청으로 처리

        # fast-path: 인사말이면 LLM 분류 없이 즉시 고정 응답 (성능)
        t0 = time.perf_counter()
        if _NON_WORD_RE.sub("", user_input.lower()) in config.GREETING_WORDS:
            self.q_out.put(make_envelope(
                task_id, "orchestrator", "user", "result", "success",
                {"result": config.GREETING_REPLY, "fast_path": True},
            ))
            print(f"[timing] fast-path 인사: {(time.perf_counter() - t0) * 1000:.0f}ms")
            return

        team = await self.classify_team(user_input)
        print(f"[timing] classify_team({team}): {(time.perf_counter() - t0) * 1000:.0f}ms")
        if team == "unknown":
            # fallback 1단계: 사용자에게 팀 선택 요청
            self._pending_input = user_input
            self.q_out.put(make_envelope(
                task_id, "orchestrator", "user", "error", "failed",
                {
                    "reason": "classification_failed",
                    "message": "어느 팀에 맡길지 입력하세요: dev / personal / comfyui",
                },
            ))
            return
        await self._dispatch(task_id, team, user_input)

    async def _dispatch(self, task_id: str, team: str, user_input: str):
        pm_name = TEAM_TO_PM[team]
        # UI용 status 메시지 (캐릭터 이동 애니메이션 트리거)
        self.q_out.put(make_envelope(
            task_id, "orchestrator", pm_name, "status", "running",
            {"detail": f"{team} 팀으로 전달"},
        ))
        request = make_envelope(
            task_id, "orchestrator", pm_name, "request", "pending",
            {"text": user_input},
        )
        t0 = time.perf_counter()
        if team == "comfyui":
            result = await self._dispatch_comfyui(task_id, request)
        else:
            try:
                result = await self.pms[team].handle(request)
            except Exception as exc:
                # PM 내부 예외도 error envelope로 변환해 전파 (§4 규칙)
                result = make_envelope(
                    task_id, pm_name, "orchestrator", "error", "failed",
                    {"reason": "pm_exception", "detail": repr(exc)},
                )
        print(f"[timing] dispatch {team}: {(time.perf_counter() - t0) * 1000:.0f}ms")
        self.q_out.put(result)
        # 규약 4: 최종 result/error를 orchestrator가 user로 반환한 시점이 태스크 종결
        self.q_out.put(make_envelope(
            task_id, "orchestrator", "user", result["type"], result["status"],
            result["payload"],
        ))

    async def _dispatch_comfyui(self, task_id: str, request: dict) -> dict:
        # 리스크 3: ComfyUI 미실행이면 GPU 전환 없이 즉시 error 반환 (Gemma 헛스왑 방지).
        if not await self.comfyui_agent.ensure_available():
            return make_envelope(
                task_id, "comfyui", "orchestrator", "error", "failed",
                {"reason": "comfyui_unavailable",
                 "message": f"ComfyUI가 실행 중이 아님 ({config.COMFYUI_BASE_URL})"},
            )
        # 리스크 6: 언로드 → ComfyUI 작업 → 재로드. 재로드는 어떤 경우에도 finally에서 수행.
        self.arbiter.busy = True
        try:
            await self.arbiter.prepare(task_id)
            try:
                return await self.comfyui_agent.handle(request)
            except Exception as exc:
                return make_envelope(
                    task_id, "comfyui", "orchestrator", "error", "failed",
                    {"reason": "comfyui_exception", "detail": repr(exc)},
                )
        finally:
            await self.arbiter.restore(task_id)
            self.arbiter.busy = False

    async def classify_team(self, user_input: str) -> str:
        # 1차: 규칙 기반 키워드 매칭
        lowered = user_input.lower()
        for team, keywords in config.TEAM_KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                return team

        # 2차: Ollama Gemma 보정
        prompt = (
            "다음 사용자 요청을 세 팀 중 하나로 분류하세요.\n"
            "- dev: 코드 작성, 개발, 버그 수정\n"
            "- personal: 일정, 할일, 메모, 정보 정리\n"
            "- comfyui: 이미지 생성, 그림\n"
            "반드시 dev, personal, comfyui 중 한 단어만 출력하세요.\n\n"
            f"요청: {user_input}\n답:"
        )
        answer = (await self._call_ollama(prompt)).strip().lower()
        for team in VALID_TEAMS:
            if team in answer:
                return team

        # 여전히 모호하면 unknown → fallback
        return "unknown"

    async def _call_ollama(self, prompt: str) -> str:
        # 공용 클라이언트에 위임 (오류 시 빈 문자열 반환 → unknown 처리)
        return await call_ollama(prompt, model=config.AGENT_MODELS["orchestrator"])
