# teams/dev/pm.py — 개발팀 PM: 요청 분석 → Claude Code 또는 Codex 선택, cwd 직렬화
#
# 추가: 모델/effort 결정 흐름 (dev_settings.json의 autonomy_mode 기준)
#   manual   → 무조건 default 모델/effort (estimate_intensity 호출 안 함)
#   auto     → 강도 추정 결과로 매핑된 모델 사용, 변경 시 status 알림만
#   approval → 제안 모델이 default와 다를 때만 approval_request 송신 후 응답 대기
#              (60초 타임아웃/거부 시 default로 자동 진행 — 작업을 멈추지 않는다)

import asyncio
import os
import uuid

import config
from messages import make_envelope
from ollama_client import call_ollama
from . import settings as dev_settings
from .agents import ClaudeCodeAgent, CodexAgent

# 1차 rule-based 강도 판단 키워드
HIGH_KEYWORDS = ("리팩토링", "설계", "아키텍처", "마이그레이션", "구조 변경", "전체", "시스템")
LOW_KEYWORDS = ("오타", "주석", "이름 변경", "한 줄", "단순", "출력문")

APPROVAL_TIMEOUT = 60.0  # 승인 응답 대기 상한 (초)


async def estimate_intensity(prompt: str, llm_call=None) -> str:
    """작업 강도 추정: "low" | "medium" | "high".

    1차 rule-based 점수가 명확 구간이면 즉시 반환, 애매 구간이면 Gemma 보정.
    Gemma 파싱 실패/예외 시 "medium" (안전 기본값).
    """
    score = sum(1 for kw in HIGH_KEYWORDS if kw in prompt)
    score -= sum(1 for kw in LOW_KEYWORDS if kw in prompt)
    if len(prompt) >= 500:
        score += 1
    elif len(prompt) < 100:
        score -= 1
    if score >= 2:
        return "high"
    if score <= -2:
        return "low"

    # 2차: Gemma 보정 (orchestrator.classify_team과 동일한 단문 분류 패턴)
    if llm_call is None:
        async def llm_call(p):
            return await call_ollama(p, model=config.AGENT_MODELS["orchestrator"])
    classify_prompt = (
        "다음 개발 요청의 작업 강도를 분류하세요.\n"
        "- low: 오타/주석/한 줄 수정 같은 단순 작업\n"
        "- medium: 일반적인 기능 추가나 버그 수정\n"
        "- high: 리팩토링/설계/아키텍처/대규모 구조 변경\n"
        "반드시 low, medium, high 중 한 단어만 출력하세요.\n\n"
        f"요청: {prompt}\n답:"
    )
    try:
        answer = (await llm_call(classify_prompt)).strip().lower()
    except Exception:
        return "medium"
    for level in ("high", "low", "medium"):
        if level in answer:
            return level
    return "medium"


class DevPM:
    name = "pm_dev"

    def __init__(self, q_out, claude_agent=None, codex_agent=None,
                 settings=None, llm_call=None):
        self.q_out = q_out
        self.claude = claude_agent or ClaudeCodeAgent()
        self.codex = codex_agent or CodexAgent()
        # 정규화된 cwd → asyncio.Lock. 같은 디렉토리 작업 직렬화 (리스크 7)
        self._cwd_locks: dict[str, asyncio.Lock] = {}
        # 모델/effort 설정 (테스트는 settings dict 직접 주입)
        self.settings = settings if settings is not None else dev_settings.load_settings()
        self.llm_call = llm_call  # 강도 판단용 LLM 호출 (None이면 ollama_client 기본)
        self.approval_timeout = APPROVAL_TIMEOUT
        # request_id → Future(bool). 승인 응답 대기 (approval 모드)
        self._pending_approvals: dict[str, asyncio.Future] = {}

    def select_agent(self, text: str):
        # 사용자가 codex를 명시하면 Codex, 그 외는 Claude Code 기본
        return self.codex if "codex" in text.lower() else self.claude

    def _resolve_cwd(self, envelope: dict) -> str:
        # payload에 cwd가 있으면 사용, 없으면 태스크별 작업 디렉토리 생성 (§7)
        cwd = envelope["payload"].get("cwd")
        if not cwd:
            cwd = os.path.join(config.WORKSPACE_ROOT, envelope["task_id"])
        cwd = os.path.abspath(cwd)
        os.makedirs(cwd, exist_ok=True)
        return cwd

    def _lock_for(self, cwd: str) -> asyncio.Lock:
        key = os.path.normcase(cwd)
        if key not in self._cwd_locks:
            self._cwd_locks[key] = asyncio.Lock()
        return self._cwd_locks[key]

    def _status(self, task_id: str, to: str, detail: str):
        # UI 전용 status 메시지 (캐릭터 애니메이션 트리거, §4/§8)
        self.q_out.put(make_envelope(
            task_id, self.name, to, "status", "running", {"detail": detail},
        ))

    # ---- 모델/effort 설정 + 승인 흐름 ----

    def has_pending_approval(self) -> bool:
        return bool(self._pending_approvals)

    def pending_request_id(self) -> str | None:
        return next(iter(self._pending_approvals), None)

    def resolve_approval(self, request_id: str | None, approved: bool) -> bool:
        # request_id가 None이면 가장 오래된 보류 요청에 적용 (오케스트레이터 y/n 변환)
        if request_id is None:
            request_id = self.pending_request_id()
        fut = self._pending_approvals.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    async def handle_settings_update(self, envelope: dict) -> dict:
        # settings_update (user → pm_dev): 부분 dict 병합 → 검증/저장 → status 회신
        task_id = envelope["task_id"]
        payload = envelope.get("payload") or {}
        updates = {k: payload[k] for k in dev_settings.DEFAULTS if k in payload}
        merged = {**self.settings, **updates}
        try:
            await dev_settings.save_settings(merged)
        except ValueError as exc:
            return make_envelope(
                task_id, self.name, "user", "error", "failed",
                {"reason": "invalid_settings", "detail": str(exc)},
            )
        self.settings = merged
        return make_envelope(
            task_id, self.name, "user", "status", "success",
            {"detail": (f"설정 저장됨: model {merged['default_model']} / "
                        f"effort {merged['default_effort']} / "
                        f"mode {merged['autonomy_mode']}")},
        )

    async def _decide_model(self, task_id: str, text: str) -> tuple[str, str]:
        """autonomy_mode에 따라 이번 작업의 (model, effort) 결정."""
        default_model = self.settings["default_model"]
        default_effort = self.settings["default_effort"]
        mode = self.settings["autonomy_mode"]
        if mode == "manual":
            # default 고정. estimate_intensity 호출 안 함 (LLM 호출 없음)
            return default_model, default_effort

        intensity = await estimate_intensity(text, llm_call=self.llm_call)
        proposed_model, proposed_effort = dev_settings.INTENSITY_MODEL_MAP[intensity]
        if proposed_model == default_model:
            # 제안 모델 == default면 승인 요청/알림 없이 그대로 진행
            return proposed_model, proposed_effort
        reason = f"작업 강도 {intensity}"

        if mode == "auto":
            self._status(task_id, "user",
                         f"모델 변경: {default_model} → {proposed_model} ({reason})")
            return proposed_model, proposed_effort

        # approval: approval_request 송신 후 Future로 응답 대기 (request_id 매칭)
        request_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending_approvals[request_id] = fut
        self.q_out.put(make_envelope(
            task_id, self.name, "user", "approval_request", "pending",
            {"proposed_model": proposed_model, "proposed_effort": proposed_effort,
             "reason": reason, "default_model": default_model,
             "request_id": request_id},
        ))
        try:
            approved = await asyncio.wait_for(fut, timeout=self.approval_timeout)
        except asyncio.TimeoutError:
            # 타임아웃 시 default로 자동 진행 (작업을 멈추지 않는다)
            self._status(task_id, "user", "승인 응답 없음 → default 모델로 진행")
            approved = False
        finally:
            self._pending_approvals.pop(request_id, None)
        if approved:
            self._status(task_id, "user",
                         f"승인됨: {proposed_model}/{proposed_effort}로 진행")
            return proposed_model, proposed_effort
        return default_model, default_effort

    async def handle(self, envelope: dict) -> dict:
        task_id = envelope["task_id"]
        text = envelope["payload"]["text"]
        self._status(task_id, "orchestrator", "요청 분석 중")  # 규약 1: PM 작업 시작
        agent = self.select_agent(text)
        model, effort = await self._decide_model(task_id, text)
        cwd = self._resolve_cwd(envelope)
        lock = self._lock_for(cwd)

        if lock.locked():
            self._status(task_id, agent.name, f"같은 디렉토리 작업 대기 중 (직렬화): {cwd}")
        async with lock:
            self._status(task_id, agent.name, f"{agent.name} 호출 중: {cwd}")
            # 규약 1: 하위 에이전트(claude_code/codex) 작업 시작 running emit
            self.q_out.put(make_envelope(
                task_id, agent.name, self.name, "status", "running",
                {"detail": "작업 시작"},
            ))

            def on_attempt(n: int):
                if n > 1:
                    self._status(task_id, agent.name,
                                 f"{agent.name} 재시도 {n}/{agent.max_retries}")

            outcome = await agent.run(text, cwd, on_attempt=on_attempt,
                                      model=model, effort=effort)

        # 규약 1: 하위 에이전트 종료 envelope(q_out) + PM 자신의 종료 envelope(반환).
        # 실패/타임아웃도 error envelope로 오케스트레이터까지 전파 (§4 규칙)
        leaf_type = "result" if outcome["ok"] else "error"
        leaf_status = "success" if outcome["ok"] else outcome["status"]
        self.q_out.put(make_envelope(
            task_id, agent.name, self.name, leaf_type, leaf_status,
            outcome["payload"],
        ))
        return make_envelope(
            task_id, self.name, "orchestrator", leaf_type, leaf_status,
            outcome["payload"],
        )
