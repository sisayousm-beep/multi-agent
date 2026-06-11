# teams/personal/schedule.py — 스케줄 에이전트: 로컬 JSON 일정/할일 CRUD (§3)
#
# - schedule.json 기반. 동작 5개: 추가 / 조회 / 수정 / 삭제 / 오늘 요약
# - 동시 쓰기 방지: 임시 파일 작성 후 os.replace로 원자적 교체 + asyncio.Lock으로 프로세스 내 직렬화
# - 자연어에서 동작/날짜/시간/제목을 rule-based로 파싱
#
# run()은 dev/agents.py와 동일하게 outcome dict 반환:
#   성공 {"ok": True, "status": "success", "payload": {...}}
#   실패 {"ok": False, "status": "failed", "payload": {...}}

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import config

_TIME_HM = re.compile(r"(\d{1,2}):(\d{2})")
_TIME_AMPM = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시")
_TIME_HOUR = re.compile(r"(\d{1,2})\s*시")
_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ID = re.compile(r"(\d+)\s*번")

# 제목 추출 시 제거할 동작/메타 토큰
_NOISE = ["추가", "등록", "잡아줘", "잡아", "넣어줘", "넣어", "삭제", "제거", "지워줘", "지워",
          "수정", "변경", "바꿔줘", "바꿔", "조회", "목록", "보여줘", "보여", "알려줘", "알려",
          "오늘", "내일", "모레", "오전", "오후", "일정", "할일", "할 일", "스케줄", "todo", "해줘", "줘"]


class ScheduleAgent:
    name = "schedule"

    def __init__(self, path: str | None = None):
        self.path = path or config.SCHEDULE_JSON
        self._lock = asyncio.Lock()  # 프로세스 내 읽기-수정-쓰기 직렬화

    # ---- 저장소 ----
    def _load(self) -> dict:
        # 파일 없음 → 빈 저장소(첫 쓰기 때 생성). 깨짐 → ValueError
        if not os.path.exists(self.path):
            return {"items": []}
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("schedule.json 스키마 불량: 'items' 리스트 없음")
        return data

    def _save(self, data: dict):
        # 원자적 교체: 임시 파일 작성 → flush/fsync → os.replace (동시 쓰기/부분 쓰기 방지)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ---- 파싱 ----
    @staticmethod
    def _today() -> date:
        return datetime.now().date()

    def _parse_date(self, text: str) -> str | None:
        m = _DATE_ISO.search(text)
        if m:
            return m.group(0)
        if "모레" in text:
            return (self._today() + timedelta(days=2)).isoformat()
        if "내일" in text:
            return (self._today() + timedelta(days=1)).isoformat()
        if "오늘" in text:
            return self._today().isoformat()
        return None

    def _parse_time(self, text: str) -> str | None:
        m = _TIME_HM.search(text)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        m = _TIME_AMPM.search(text)
        if m:
            h = int(m.group(2)) % 12
            if m.group(1) == "오후":
                h += 12
            return f"{h:02d}:00"
        m = _TIME_HOUR.search(text)
        if m:
            return f"{int(m.group(1)):02d}:00"
        return None

    def _parse_title(self, text: str) -> str:
        t = _TIME_HM.sub(" ", text)
        t = _TIME_AMPM.sub(" ", t)
        t = _TIME_HOUR.sub(" ", t)
        t = _DATE_ISO.sub(" ", t)
        t = _ID.sub(" ", t)
        for w in _NOISE:
            t = t.replace(w, " ")
        return " ".join(t.split()).strip()

    def _detect_action(self, text: str) -> str:
        if any(k in text for k in ("삭제", "제거", "지워")):
            return "delete"
        if any(k in text for k in ("수정", "변경", "바꿔")):
            return "update"
        if any(k in text for k in ("추가", "등록", "잡아", "넣어")):
            return "add"
        if "오늘" in text and any(k in text for k in ("일정", "할일", "요약", "뭐", "보여", "알려", "스케줄")):
            return "today"
        if any(k in text for k in ("조회", "목록", "보여", "알려", "뭐 있", "전체")):
            return "list"
        return "list"  # 기본: 조회

    # ---- 동작 ----
    async def run(self, text: str) -> dict:
        action = self._detect_action(text)
        try:
            async with self._lock:
                return self._dispatch(action, text)
        except ValueError as exc:
            return {"ok": False, "status": "failed", "payload": {
                "agent": self.name, "reason": "file_corrupt", "detail": str(exc),
            }}

    def _dispatch(self, action: str, text: str) -> dict:
        data = self._load()
        items = data["items"]

        if action == "add":
            item = {
                "id": (max((i["id"] for i in items), default=0) + 1),
                "title": self._parse_title(text) or "(제목 없음)",
                "date": self._parse_date(text),
                "time": self._parse_time(text),
                "kind": "todo" if "할일" in text or "할 일" in text else "event",
                "done": False,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            items.append(item)
            self._save(data)
            return self._ok("add", item=item, count=len(items))

        if action == "list":
            return self._ok("list", items=items, count=len(items))

        if action == "today":
            today = self._today().isoformat()
            todays = [i for i in items if i.get("date") == today]
            return self._ok("today", date=today, items=todays, count=len(todays))

        if action == "delete":
            target_id = self._target_id(text)
            if target_id is None:
                return self._fail("missing_id", "삭제할 항목 id(예: '2번 삭제')를 찾지 못함")
            remaining = [i for i in items if i["id"] != target_id]
            if len(remaining) == len(items):
                return self._fail("not_found", f"id {target_id} 항목 없음")
            data["items"] = remaining
            self._save(data)
            return self._ok("delete", deleted_id=target_id, count=len(remaining))

        if action == "update":
            target_id = self._target_id(text)
            if target_id is None:
                return self._fail("missing_id", "수정할 항목 id(예: '2번 수정')를 찾지 못함")
            item = next((i for i in items if i["id"] == target_id), None)
            if item is None:
                return self._fail("not_found", f"id {target_id} 항목 없음")
            new_date, new_time, new_title = self._parse_date(text), self._parse_time(text), self._parse_title(text)
            if new_date:
                item["date"] = new_date
            if new_time:
                item["time"] = new_time
            if new_title:
                item["title"] = new_title
            item["updated"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
            return self._ok("update", item=item, count=len(items))

        return self._fail("unknown_action", f"알 수 없는 동작: {action}")

    def _target_id(self, text: str) -> int | None:
        m = _ID.search(text)
        if m:
            return int(m.group(1))
        nums = re.findall(r"\d+", _TIME_HM.sub(" ", text))  # 시:분 숫자 제외
        return int(nums[0]) if nums else None

    def _ok(self, action: str, **extra) -> dict:
        return {"ok": True, "status": "success",
                "payload": {"agent": self.name, "action": action, **extra}}

    def _fail(self, reason: str, detail: str) -> dict:
        return {"ok": False, "status": "failed",
                "payload": {"agent": self.name, "reason": reason, "detail": detail}}
