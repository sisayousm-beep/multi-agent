# messages.py — §4 메시지 envelope: 모든 에이전트 간 통신과 UI 이벤트의 단일 규약

import uuid
from datetime import datetime, timezone

TYPES = ("request", "result", "error", "status")
STATUSES = ("pending", "running", "success", "failed", "timeout")


def new_task_id() -> str:
    # 하나의 사용자 요청 단위로 동일하게 유지되는 ID
    return str(uuid.uuid4())


def make_envelope(task_id: str, sender: str, to: str, msg_type: str, status: str, payload: dict) -> dict:
    # "from"이 파이썬 예약어라 파라미터명은 sender, 키는 스키마대로 "from" 유지
    if msg_type not in TYPES:
        raise ValueError(f"invalid envelope type: {msg_type}")
    if status not in STATUSES:
        raise ValueError(f"invalid envelope status: {status}")
    return {
        "task_id": task_id,
        "from": sender,
        "to": to,
        "type": msg_type,
        "status": status,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
