# tests/test_stage3.py — §7 subprocess 정책 테스트 (실제 CLI 불필요, mock_cli.py 사용)
#
# 실행: python tests/test_stage3.py
# 검증 항목: idle timeout / 절대 상한 / 재시도 / 같은 cwd 직렬화

import asyncio
import os
import queue
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messages import make_envelope, new_task_id
from teams.dev.agents import ClaudeCodeAgent
from teams.dev.pm import DevPM
from teams.dev.subprocess_runner import run_once, run_with_retry

PY = sys.executable
MOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_cli.py")


class MockAgent(ClaudeCodeAgent):
    # build_command만 mock 스크립트로 바꿔치기 (parse_output은 Claude 포맷 그대로 사용)
    name = "mock_agent"

    def __init__(self, mode, record_file=None, **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        self.record_file = record_file

    def build_command(self, instruction):
        cmd = [PY, MOCK, self.mode]
        if self.record_file:
            cmd.append(self.record_file)
        return cmd


async def test_idle_timeout():
    # 무출력 프로세스는 idle_timeout 후 kill
    r = await run_once([PY, MOCK, "silent_hang"], cwd=None,
                       idle_timeout=1.0, absolute_timeout=30.0)
    assert r.timeout_kind == "idle", r
    assert r.duration < 10, r
    print("PASS: idle timeout")


async def test_idle_reset_then_absolute():
    # 계속 출력하면 idle은 리셋되지만 절대 상한에서 kill
    r = await run_once([PY, MOCK, "drip"], cwd=None,
                       idle_timeout=2.0, absolute_timeout=3.0)
    assert r.timeout_kind == "absolute", r
    assert r.duration >= 3.0, r
    print("PASS: idle reset + absolute timeout")


async def test_retry():
    # 실패 시 max_retries회 재시도 후 중단, 호출 횟수 검증
    counter = os.path.join(tempfile.mkdtemp(), "count.txt")
    r, attempts = await run_with_retry([PY, MOCK, "fail", counter], cwd=None,
                                       idle_timeout=5.0, absolute_timeout=10.0,
                                       max_retries=3)
    assert attempts == 3, attempts
    assert r.returncode == 1, r
    with open(counter) as f:
        assert f.read() == "xxx"  # 정확히 3회 실행됨
    print("PASS: retry limit")


async def test_timeout_error_envelope():
    # 타임아웃이 status: timeout인 error envelope로 변환되는지 (PM 경유)
    q_out = queue.Queue()
    agent = MockAgent("silent_hang", idle_timeout=0.7, absolute_timeout=10.0, max_retries=2)
    pm = DevPM(q_out, claude_agent=agent, codex_agent=agent)
    cwd = tempfile.mkdtemp()
    req = make_envelope(new_task_id(), "orchestrator", "pm_dev", "request", "pending",
                        {"text": "hang please", "cwd": cwd})
    result = await pm.handle(req)
    assert result["type"] == "error", result
    assert result["status"] == "timeout", result
    assert result["payload"]["reason"] == "idle_timeout", result
    assert result["payload"]["attempts"] == 2, result
    # 진행 status 메시지가 q_out으로 나갔는지
    statuses = []
    while not q_out.empty():
        statuses.append(q_out.get())
    assert any(m["type"] == "status" for m in statuses), statuses
    print("PASS: timeout -> error envelope (status: timeout)")


async def test_serialization_same_cwd():
    # 같은 cwd 작업 2개를 동시에 dispatch해도 실행 구간이 겹치지 않아야 함 (리스크 7)
    q_out = queue.Queue()
    log = os.path.join(tempfile.mkdtemp(), "timing.txt")
    agent = MockAgent("slow_ok", record_file=log,
                      idle_timeout=10.0, absolute_timeout=30.0, max_retries=1)
    pm = DevPM(q_out, claude_agent=agent, codex_agent=agent)
    cwd = tempfile.mkdtemp()

    def req(text):
        return make_envelope(new_task_id(), "orchestrator", "pm_dev", "request", "pending",
                             {"text": text, "cwd": cwd})

    r1, r2 = await asyncio.gather(pm.handle(req("task A")), pm.handle(req("task B")))
    assert r1["type"] == "result" and r2["type"] == "result", (r1, r2)

    with open(log) as f:
        lines = [line.split() for line in f.read().splitlines()]
    assert [kind for kind, _ in lines] == ["start", "end", "start", "end"], lines
    times = [float(t) for _, t in lines]
    assert times[1] <= times[2], f"실행 구간 겹침: {times}"
    print("PASS: same-cwd serialization")


async def test_parallel_different_cwd():
    # 다른 cwd는 병렬 실행 허용 (직렬화가 과도하게 적용되지 않는지)
    q_out = queue.Queue()
    log = os.path.join(tempfile.mkdtemp(), "timing.txt")
    agent = MockAgent("slow_ok", record_file=log,
                      idle_timeout=10.0, absolute_timeout=30.0, max_retries=1)
    pm = DevPM(q_out, claude_agent=agent, codex_agent=agent)

    def req(text, cwd):
        return make_envelope(new_task_id(), "orchestrator", "pm_dev", "request", "pending",
                             {"text": text, "cwd": cwd})

    import time
    start = time.monotonic()
    await asyncio.gather(
        pm.handle(req("task A", tempfile.mkdtemp())),
        pm.handle(req("task B", tempfile.mkdtemp())),
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.9, f"병렬 실행이어야 하는데 {elapsed:.2f}초 걸림"
    print("PASS: different-cwd parallel")


async def main():
    await test_idle_timeout()
    await test_idle_reset_then_absolute()
    await test_retry()
    await test_timeout_error_envelope()
    await test_serialization_same_cwd()
    await test_parallel_different_cwd()
    print("\nALL STAGE-3 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
