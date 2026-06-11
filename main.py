# main.py — 콘솔 REPL 진입점 (6단계 Pygame UI 도입 전 대체 UI)

from messages import make_envelope, new_task_id
from runtime import AgentRuntime


def drain_until_done(q_out, task_id: str):
    """해당 task의 result/error가 나올 때까지 q_out 메시지를 출력.

    Pygame 도입 시 이 블로킹 패턴 대신 매 프레임 get_nowait() +
    except queue.Empty 패턴으로 대체된다 (§6).
    """
    while True:
        msg = q_out.get()
        msg_type = msg["type"]
        if msg_type == "status":
            print(f"  [status] {msg['from']} -> {msg['to']}: {msg['payload'].get('detail', '')}")
            continue
        if msg_type == "result":
            print(f"[{msg['from']}] {msg['payload'].get('result', msg['payload'])}")
        elif msg_type == "error":
            payload = msg["payload"]
            print(f"[error/{msg['status']}] {payload.get('message') or payload}")
        if msg["task_id"] == task_id:
            return


def main():
    runtime = AgentRuntime()
    runtime.start()
    print("멀티 에이전트 시스템 시작. 'exit'로 종료.")
    try:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            if user_input.lower() == "exit":
                break
            task_id = new_task_id()
            runtime.q_in.put(make_envelope(
                task_id, "user", "orchestrator", "request", "pending",
                {"text": user_input},
            ))
            drain_until_done(runtime.q_out, task_id)
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
