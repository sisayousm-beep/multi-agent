# messages.py — §4 메시지 envelope: 모든 에이전트 간 통신과 UI 이벤트의 단일 규약

import uuid
from datetime import datetime, timezone

# payload 규약 (신규 3종 — 기존 request/result/error/status 동작은 변경 없음):
#   approval_request (pm_dev → user):
#     {"proposed_model", "proposed_effort", "reason", "default_model", "request_id"}
#   approval_response (user → pm_dev):
#     {"request_id", "approved": bool}
#   settings_update (user → pm_dev):
#     dev_settings.json 스키마와 동일한 부분 dict
#     ({"default_model"?, "default_effort"?, "autonomy_mode"?})
TYPES = ("request", "result", "error", "status",
         "approval_request", "approval_response", "settings_update")
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
