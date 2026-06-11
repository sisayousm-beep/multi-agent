# tests/test_stage2.py — 2단계 골격 테스트 (Ollama 호출은 stub, 네트워크 불필요)
#
# 실행: python tests/test_stage2.py
# 검증 항목: envelope 스키마 / 키워드 분류 라우팅 / 분류 실패 fallback /
#           PM 예외의 error envelope 전파 / 워커 스레드 + queue 골격

import asyncio
import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messages import make_envelope, new_task_id
from orchestrator import Orchestrator

ENVELOPE_KEYS = {"task_id", "from", "to", "type", "status", "payload", "timestamp"}


def user_request(text):
    return make_envelope(new_task_id(), "user", "orchestrator", "request", "pending",
                         {"text": text})


def drain(q):
    msgs = []
    while not q.empty():
        msgs.append(q.get())
    return msgs


async def test_keyword_routing_envelope():
    # 키워드 1차 분류 → personal mock PM → result envelope
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    await orch.handle(user_request("내일 일정 알려줘"))
    msgs = drain(q_out)
    assert all(ENVELOPE_KEYS <= set(m.keys()) for m in msgs), msgs
    result = [m for m in msgs if m["type"] == "result"]
    assert len(result) == 1, msgs
    # 4단계에서 mock PM → 실제 비서팀으로 교체: result는 하위 에이전트(brain/schedule)가 귀속
    # (개발팀이 claude_code/codex로 귀속하는 것과 동일 패턴). PM은 status 메시지로 분리 송신.
    assert result[0]["from"] in ("brain", "schedule"), result[0]
    assert result[0]["status"] == "success", result[0]
    assert any(m["type"] == "status" for m in msgs), msgs  # UI용 status 분리 확인
    print("PASS: keyword routing + envelope schema")


async def test_fallback_team_selection():
    # 분류 실패(Ollama stub이 빈 답) → 사용자 팀 선택 → 원본 요청 라우팅
    q_out = queue.Queue()
    orch = Orchestrator(q_out)

    async def no_answer(prompt):
        return ""

    orch._call_ollama = no_answer
    await orch.handle(user_request("아무 키워드에도 안 걸리는 요청"))
    msgs = drain(q_out)
    errors = [m for m in msgs if m["type"] == "error"]
    assert len(errors) == 1, msgs
    assert errors[0]["payload"]["reason"] == "classification_failed", errors[0]

    # 사용자가 팀 이름으로 응답 → comfyui 경로(5단계: 실제 에이전트)로 라우팅.
    # 테스트 환경엔 ComfyUI가 없으므로 health check 실패 → error envelope 반환(리스크 3).
    await orch.handle(user_request("comfyui"))
    msgs = drain(q_out)
    comfy = [m for m in msgs if m["from"] == "comfyui"]
    assert len(comfy) == 1 and comfy[0]["type"] == "error", msgs
    assert comfy[0]["payload"]["reason"] == "comfyui_unavailable", comfy[0]
    print("PASS: classification fallback -> user team selection")


async def test_pm_exception_to_error_envelope():
    # PM 내부 예외가 error envelope로 변환되는지
    q_out = queue.Queue()
    orch = Orchestrator(q_out)

    class BoomPM:
        async def handle(self, envelope):
            raise RuntimeError("boom")

    orch.pms["personal"] = BoomPM()
    await orch.handle(user_request("일정 알려줘"))
    msgs = drain(q_out)
    errors = [m for m in msgs if m["type"] == "error"]
    assert len(errors) == 1, msgs
    assert errors[0]["status"] == "failed", errors[0]
    assert "boom" in errors[0]["payload"]["detail"], errors[0]
    print("PASS: pm exception -> error envelope")


def test_runtime_skeleton():
    # 워커 스레드 + asyncio + queue.Queue 2개 골격이 실제로 도는지
    from runtime import AgentRuntime
    rt = AgentRuntime()
    rt.start()
    task_id = new_task_id()
    rt.q_in.put(make_envelope(task_id, "user", "orchestrator", "request", "pending",
                              {"text": "이미지 그려줘"}))
    deadline_msgs = []
    while True:
        msg = rt.q_out.get(timeout=10)
        deadline_msgs.append(msg)
        if msg["type"] in ("result", "error") and msg["task_id"] == task_id:
            break
    rt.stop()
    # 5단계: comfyui는 실제 에이전트. ComfyUI 미실행 환경이면 error, 실행 중이면 result.
    assert deadline_msgs[-1]["type"] in ("result", "error"), deadline_msgs
    assert deadline_msgs[-1]["from"] == "comfyui", deadline_msgs
    print("PASS: worker thread + q_in/q_out skeleton")


async def main():
    await test_keyword_routing_envelope()
    await test_fallback_team_selection()
    await test_pm_exception_to_error_envelope()


if __name__ == "__main__":
    asyncio.run(main())
    test_runtime_skeleton()
    print("\nALL STAGE-2 TESTS PASSED")
