# teams/comfyui/pm.py — ComfyUI팀 PM (5단계에서 ComfyUI REST API 연동 예정)

import asyncio

from messages import make_envelope


class ComfyUIPM:
    name = "comfyui"

    def __init__(self, q_out):
        self.q_out = q_out

    async def handle(self, envelope: dict) -> dict:
        # 현재는 mock 응답을 envelope로 반환
        task_id = envelope["task_id"]
        text = envelope["payload"]["text"]
        self.q_out.put(make_envelope(
            task_id, self.name, "comfyui", "status", "running",
            {"detail": "mock 처리 중"},
        ))
        await asyncio.sleep(0.1)  # mock 처리 지연
        return make_envelope(
            task_id, self.name, "orchestrator", "result", "success",
            {"result": f"[ComfyUI팀 mock] 태스크 수신: {text} — REST API 연동은 5단계에서 구현 예정"},
        )
