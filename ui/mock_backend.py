# ui/mock_backend.py — 백엔드 없이 UI 단독 테스트용 mock (요구사항 5)
#
# AgentRuntime과 같은 인터페이스(q_in/q_out, start/stop)를 제공하고,
# 워커 스레드가 가짜 envelope 시나리오를 q_out에 주기적으로 넣는다.
# q_in으로 사용자 입력이 들어오면 개발팀 흐름으로 즉석 응답한다.
#
# 실행:
#   python -m ui.main --mock          # UI와 함께
#   python -m ui.mock_backend         # 콘솔에서 envelope만 확인

import itertools
import queue
import threading
import time

from messages import make_envelope, new_task_id

STEP_DELAY = 1.0  # 시나리오 envelope 간 간격 (초)


def _dev_flow(task_id, text="버그 수정해줘"):
    # 개발팀: orchestrator → pm_dev → claude_code → 성공
    return [
        make_envelope(task_id, "orchestrator", "pm_dev", "status", "running",
                      {"detail": "dev 팀으로 전달"}),
        make_envelope(task_id, "orchestrator", "pm_dev", "request", "pending",
                      {"text": text}),
        make_envelope(task_id, "pm_dev", "claude_code", "request", "pending",
                      {"text": text}),
        make_envelope(task_id, "pm_dev", "user", "status", "running",
                      {"detail": "Claude Code 호출 중"}),
        make_envelope(task_id, "claude_code", "pm_dev", "result", "success",
                      {"result": "수정 완료: fix 커밋 생성"}),
        make_envelope(task_id, "pm_dev", "orchestrator", "result", "success",
                      {"result": "개발팀 작업 완료"}),
    ]


def _personal_flow(task_id):
    # 비서팀: orchestrator → pm_assistant → brain
    return [
        make_envelope(task_id, "orchestrator", "pm_assistant", "status", "running",
                      {"detail": "personal 팀으로 전달"}),
        make_envelope(task_id, "orchestrator", "pm_assistant", "request", "pending",
                      {"text": "어제 메모 요약해줘"}),
        make_envelope(task_id, "pm_assistant", "brain", "request", "pending",
                      {"text": "메모 요약"}),
        make_envelope(task_id, "brain", "pm_assistant", "result", "success",
                      {"result": "요약: 3건의 메모 정리됨"}),
        make_envelope(task_id, "pm_assistant", "orchestrator", "result", "success",
                      {"result": "비서팀 응답 완료"}),
    ]


def _comfyui_flow(task_id):
    # ComfyUI: GPU 전환 배너(리스크 6) → 이미지 생성 → 재로드
    return [
        make_envelope(task_id, "orchestrator", "comfyui", "status", "running",
                      {"detail": "comfyui 팀으로 전달"}),
        make_envelope(task_id, "orchestrator", "user", "status", "running",
                      {"phase": "gpu_switch", "detail": "모델 전환 중 (Ollama 언로드)"}),
        make_envelope(task_id, "orchestrator", "comfyui", "request", "pending",
                      {"text": "lola 스타일 그림 그려줘"}),
        make_envelope(task_id, "comfyui", "user", "status", "running",
                      {"detail": "이미지 생성 중"}),
        make_envelope(task_id, "comfyui", "orchestrator", "result", "success",
                      {"result": "output/lola_0001.png 생성"}),
        make_envelope(task_id, "orchestrator", "user", "status", "running",
                      {"phase": "gpu_switch", "detail": "모델 전환 중 (Gemma 재로드)"}),
    ]


def _error_flow(task_id):
    # 실패/타임아웃 색상 확인용
    return [
        make_envelope(task_id, "orchestrator", "pm_dev", "request", "pending",
                      {"text": "codex로 리팩토링"}),
        make_envelope(task_id, "pm_dev", "codex", "request", "pending",
                      {"text": "리팩토링"}),
        make_envelope(task_id, "codex", "pm_dev", "error", "timeout",
                      {"reason": "idle_timeout", "message": "90초 무출력으로 종료됨"}),
        make_envelope(task_id, "pm_dev", "orchestrator", "error", "failed",
                      {"reason": "subprocess_failed", "message": "Codex 작업 실패"}),
    ]


class MockRuntime:
    """AgentRuntime 대체: q_in/q_out 인터페이스 동일, 내용은 가짜 시나리오."""

    def __init__(self, step_delay: float = STEP_DELAY):
        self.q_in: queue.Queue = queue.Queue()
        self.q_out: queue.Queue = queue.Queue()
        self.step_delay = step_delay
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        flows = itertools.cycle([_dev_flow, _personal_flow, _comfyui_flow, _error_flow])
        while not self._stop.is_set():
            # 사용자 입력이 있으면 그 task_id로 dev 흐름 응답, 없으면 자동 시나리오
            try:
                msg = self.q_in.get(timeout=self.step_delay)
                steps = _dev_flow(msg["task_id"], msg["payload"].get("text", ""))
            except queue.Empty:
                steps = next(flows)(new_task_id())
            for env in steps:
                if self._stop.is_set():
                    return
                self.q_out.put(env)
                time.sleep(self.step_delay)
            time.sleep(self.step_delay * 2)


if __name__ == "__main__":
    # 콘솔 확인 모드: 15초 동안 envelope를 stdout에 출력
    rt = MockRuntime(step_delay=0.5)
    rt.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            env = rt.q_out.get(timeout=1)
            print(f"{env['from']:>13} → {env['to']:<13} {env['type']}/{env['status']}  "
                  f"{env['payload']}")
        except queue.Empty:
            pass
    rt.stop()
