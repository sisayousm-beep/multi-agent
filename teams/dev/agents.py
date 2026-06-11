# teams/dev/agents.py — Claude Code / Codex CLI 에이전트 (§7)
#
# CLI 출력은 반드시 파싱해 payload dict로 변환 (raw stdout을 그대로 넘기지 않음, §4 규칙)

import json

import config
from . import subprocess_runner

STDERR_TAIL = 2000  # 에러 payload에 담을 stderr 끝부분 길이


class CLIAgent:
    name = ""  # envelope "from" 필드에 쓰는 에이전트 이름

    def __init__(self, idle_timeout: float | None = None,
                 absolute_timeout: float | None = None,
                 max_retries: int | None = None):
        self.idle_timeout = idle_timeout if idle_timeout is not None else config.IDLE_TIMEOUT
        self.absolute_timeout = absolute_timeout if absolute_timeout is not None else config.ABSOLUTE_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else config.MAX_RETRIES

    def build_command(self, instruction: str) -> list[str]:
        raise NotImplementedError

    def parse_output(self, stdout: str) -> dict:
        # 실패 시 ValueError를 던질 것
        raise NotImplementedError

    async def run(self, instruction: str, cwd: str, on_attempt=None) -> dict:
        """CLI 실행 결과를 dict로 반환.

        성공: {"ok": True, "status": "success", "payload": {...}}
        실패: {"ok": False, "status": "timeout"|"failed", "payload": {...}}
        """
        cmd = self.build_command(instruction)
        result, attempts = await subprocess_runner.run_with_retry(
            cmd, cwd, self.idle_timeout, self.absolute_timeout,
            self.max_retries, on_attempt=on_attempt,
        )
        if result.timeout_kind is not None:
            return {"ok": False, "status": "timeout", "payload": {
                "agent": self.name,
                "reason": f"{result.timeout_kind}_timeout",
                "attempts": attempts,
                "duration": result.duration,
                "stderr_tail": result.stderr[-STDERR_TAIL:],
            }}
        if result.returncode != 0:
            return {"ok": False, "status": "failed", "payload": {
                "agent": self.name,
                "reason": "nonzero_exit",
                "returncode": result.returncode,
                "attempts": attempts,
                "stderr_tail": result.stderr[-STDERR_TAIL:],
            }}
        try:
            payload = self.parse_output(result.stdout)
        except ValueError as exc:
            return {"ok": False, "status": "failed", "payload": {
                "agent": self.name,
                "reason": "output_parse_error",
                "detail": str(exc),
                "stdout_tail": result.stdout[-STDERR_TAIL:],
            }}
        payload["agent"] = self.name
        payload["attempts"] = attempts
        return {"ok": True, "status": "success", "payload": payload}


class ClaudeCodeAgent(CLIAgent):
    name = "claude_code"

    def build_command(self, instruction: str) -> list[str]:
        return [
            config.CLAUDE_CMD, "-p", instruction,
            "--output-format", "json",
            "--allowedTools", "Read,Edit,Write,Bash",
        ]

    def parse_output(self, stdout: str) -> dict:
        # claude --output-format json: 단일 JSON 객체
        # {"type":"result","result":"...","is_error":false,"session_id":...,"total_cost_usd":...}
        data = json.loads(stdout)  # JSONDecodeError는 ValueError 하위 클래스
        return {
            "result": data.get("result"),
            "is_error": data.get("is_error", False),
            "session_id": data.get("session_id"),
            "total_cost_usd": data.get("total_cost_usd"),
        }


class CodexAgent(CLIAgent):
    name = "codex"

    def build_command(self, instruction: str) -> list[str]:
        # codex exec: 비대화형 모드, --json은 JSONL 이벤트 스트림 출력
        # 작업 디렉토리가 git repo가 아닐 수 있으므로 --skip-git-repo-check 필요
        return [
            config.CODEX_CMD, "exec", "--json",
            "--skip-git-repo-check",
            instruction,
        ]

    def parse_output(self, stdout: str) -> dict:
        # JSONL에서 마지막 agent_message를 결과로 추출
        last_message = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                last_message = item.get("text")
            elif event.get("type") == "agent_message":  # 구버전 이벤트 포맷
                last_message = event.get("message")
        if last_message is None:
            raise ValueError("codex 출력에서 agent_message 이벤트를 찾지 못함")
        return {"result": last_message}
