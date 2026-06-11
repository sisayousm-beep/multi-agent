# teams/dev/subprocess_runner.py — §7 subprocess 실행 정책
#
# - asyncio.create_subprocess_exec 전용 (스레드 추가 생성 금지)
# - idle timeout: stdout/stderr 무출력이 idle_timeout초 지속되면 kill (출력 시 리셋)
# - 절대 상한: absolute_timeout초 초과 시 무조건 kill
# - 재시도: run_with_retry가 성공(returncode 0)까지 최대 max_retries회 시도

import asyncio
import time
from dataclasses import dataclass

CHECK_INTERVAL = 0.5  # 타임아웃 판정 주기 (초)


@dataclass
class RunResult:
    returncode: int | None
    stdout: str
    stderr: str
    timeout_kind: str | None  # None(정상 종료) | "idle" | "absolute"
    duration: float


async def run_once(cmd: list[str], cwd: str | None,
                   idle_timeout: float, absolute_timeout: float) -> RunResult:
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    state = {"last_output": time.monotonic()}
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    async def pump(stream, chunks):
        # 출력이 나올 때마다 idle 타이머 리셋
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            state["last_output"] = time.monotonic()
            chunks.append(chunk)

    pumps = [
        asyncio.create_task(pump(proc.stdout, out_chunks)),
        asyncio.create_task(pump(proc.stderr, err_chunks)),
    ]
    waiter = asyncio.create_task(proc.wait())
    timeout_kind = None
    while not waiter.done():
        await asyncio.wait({waiter}, timeout=CHECK_INTERVAL)
        if waiter.done():
            break
        now = time.monotonic()
        if now - state["last_output"] >= idle_timeout:
            timeout_kind = "idle"
            break
        if now - start >= absolute_timeout:
            timeout_kind = "absolute"
            break
    if timeout_kind is not None:
        proc.kill()
        await proc.wait()
    # 파이프가 닫힐 때까지 남은 출력 수거
    await asyncio.gather(*pumps, return_exceptions=True)
    return RunResult(
        returncode=proc.returncode,
        stdout=b"".join(out_chunks).decode("utf-8", errors="replace"),
        stderr=b"".join(err_chunks).decode("utf-8", errors="replace"),
        timeout_kind=timeout_kind,
        duration=time.monotonic() - start,
    )


async def run_with_retry(cmd: list[str], cwd: str | None,
                         idle_timeout: float, absolute_timeout: float,
                         max_retries: int, on_attempt=None) -> tuple[RunResult, int]:
    """성공할 때까지 최대 max_retries회 시도. (마지막 결과, 시도 횟수) 반환."""
    last = None
    for attempt in range(1, max_retries + 1):
        if on_attempt is not None:
            on_attempt(attempt)
        last = await run_once(cmd, cwd, idle_timeout, absolute_timeout)
        if last.timeout_kind is None and last.returncode == 0:
            return last, attempt
    return last, max_retries
