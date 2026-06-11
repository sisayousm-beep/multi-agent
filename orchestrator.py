# orchestrator.py — 오케스트레이터: 사용자 입력을 분류해 각 팀 PM에게 envelope로 전달

import httpx

import config
from messages import make_envelope
from teams.dev.pm import DevPM
from teams.personal.pm import PersonalPM
from teams.comfyui.pm import ComfyUIPM

VALID_TEAMS = ("dev", "personal", "comfyui")
TEAM_TO_PM = {"dev": "pm_dev", "personal": "pm_assistant", "comfyui": "comfyui"}


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
            "comfyui": ComfyUIPM(q_out),
        }
        self._pending_input: str | None = None  # 분류 실패 후 팀 선택 대기 중인 원본 요청

    async def handle(self, envelope: dict):
        # request envelope 하나를 끝까지 처리해 result/error를 q_out으로 내보낸다
        task_id = envelope["task_id"]
        user_input = str(envelope["payload"].get("text", "")).strip()

        # fallback 2단계: 직전 분류 실패 → 이번 입력을 팀 선택으로 해석
        if self._pending_input is not None:
            original = self._pending_input
            self._pending_input = None
            if user_input.lower() in VALID_TEAMS:
                await self._dispatch(task_id, user_input.lower(), original)
                return
            # 팀 이름이 아니면 보류 요청을 버리고 새 요청으로 처리

        team = await self.classify_team(user_input)
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
        try:
            result = await self.pms[team].handle(request)
        except Exception as exc:
            # PM 내부 예외도 error envelope로 변환해 전파 (§4 규칙)
            result = make_envelope(
                task_id, pm_name, "orchestrator", "error", "failed",
                {"reason": "pm_exception", "detail": repr(exc)},
            )
        self.q_out.put(result)

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
        # Ollama /api/generate 비동기 호출 (오류 시 빈 문자열 반환 → unknown 처리)
        payload = {
            "model": config.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }
        for _ in range(config.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
                    resp = await client.post(
                        f"{config.OLLAMA_BASE_URL}/api/generate", json=payload
                    )
                    resp.raise_for_status()
                    return resp.json().get("response", "")
            except httpx.HTTPError:
                continue
        return ""
