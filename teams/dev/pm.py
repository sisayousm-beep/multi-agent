# teams/dev/pm.py — 개발팀 PM: 요청 분석 → Claude Code 또는 Codex 선택, cwd 직렬화

import asyncio
import os

import config
from messages import make_envelope
from .agents import ClaudeCodeAgent, CodexAgent


class DevPM:
    name = "pm_dev"

    def __init__(self, q_out, claude_agent=None, codex_agent=None):
        self.q_out = q_out
        self.claude = claude_agent or ClaudeCodeAgent()
        self.codex = codex_agent or CodexAgent()
        # 정규화된 cwd → asyncio.Lock. 같은 디렉토리 작업 직렬화 (리스크 7)
        self._cwd_locks: dict[str, asyncio.Lock] = {}

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

    async def handle(self, envelope: dict) -> dict:
        task_id = envelope["task_id"]
        text = envelope["payload"]["text"]
        agent = self.select_agent(text)
        cwd = self._resolve_cwd(envelope)
        lock = self._lock_for(cwd)

        if lock.locked():
            self._status(task_id, agent.name, f"같은 디렉토리 작업 대기 중 (직렬화): {cwd}")
        async with lock:
            self._status(task_id, agent.name, f"{agent.name} 호출 중: {cwd}")

            def on_attempt(n: int):
                if n > 1:
                    self._status(task_id, agent.name,
                                 f"{agent.name} 재시도 {n}/{agent.max_retries}")

            outcome = await agent.run(text, cwd, on_attempt=on_attempt)

        if outcome["ok"]:
            return make_envelope(
                task_id, agent.name, "orchestrator", "result", "success",
                outcome["payload"],
            )
        # 실패/타임아웃은 error envelope로 오케스트레이터까지 전파 (§4 규칙)
        return make_envelope(
            task_id, agent.name, "orchestrator", "error", outcome["status"],
            outcome["payload"],
        )
