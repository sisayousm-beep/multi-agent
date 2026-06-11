# runtime.py — §6 동시성 골격: 워커 스레드 안 asyncio 루프 + queue.Queue 2개
#
# [메인 스레드]                  [워커 스레드 (이 파일)]
#  Pygame/콘솔 UI                 asyncio 이벤트 루프
#   - q_out 수신                   - 오케스트레이터
#   - 입력 → q_in.put()            - asyncio.create_subprocess_exec
#         ↕ q_in / q_out (queue.Queue)

import asyncio
import queue
import threading

from orchestrator import Orchestrator

SHUTDOWN = None  # q_in에 None을 넣으면 워커 루프 종료


class AgentRuntime:
    """UI(메인 스레드)와 에이전트 루프(워커 스레드)를 잇는 골격.

    - q_in: UI → 에이전트 (request envelope)
    - q_out: 에이전트 → UI (status / result / error envelope)
    """

    def __init__(self):
        self.q_in: queue.Queue = queue.Queue()
        self.q_out: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self.q_in.put(SHUTDOWN)
        self._thread.join(timeout=5)

    def _thread_main(self):
        # Pygame은 메인 스레드 고정이므로 asyncio.run은 반드시 이 워커 스레드 안에서 실행 (§6)
        asyncio.run(self._main_loop())

    async def _main_loop(self):
        loop = asyncio.get_running_loop()
        orchestrator = Orchestrator(self.q_out)
        pending: set[asyncio.Task] = set()
        while True:
            # queue.Queue.get은 블로킹이므로 executor로 감싸 이벤트 루프 블로킹 방지 (§6)
            msg = await loop.run_in_executor(None, self.q_in.get)
            if msg is SHUTDOWN:
                break
            # 긴 subprocess 작업이 새 입력 수신을 막지 않도록 태스크로 분리
            task = asyncio.create_task(orchestrator.handle(msg))
            pending.add(task)
            task.add_done_callback(pending.discard)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
