# teams/personal/pm.py — 개인 비서팀 PM: 요청 분석 → 브레인/스케줄 라우팅 (§3)
#
# - rule-based 1차 분류 → 애매하면 Gemma 보정
# - 결과를 envelope로 오케스트레이터에 반환, 진행 중 status 메시지를 q_out으로 송신
# - 브레인/스케줄 에이전트는 outcome dict 반환 → PM이 result/error envelope로 변환 (§4)

from functools import partial

import config
from messages import make_envelope
from ollama_client import call_ollama
from .brain import BrainAgent
from .schedule import ScheduleAgent


class PersonalPM:
    name = "pm_assistant"

    def __init__(self, q_out, brain=None, schedule=None, ollama_call=None):
        self.q_out = q_out
        self.brain = brain or BrainAgent()
        self.schedule = schedule or ScheduleAgent()
        self._ollama = ollama_call or partial(
            call_ollama, model=config.AGENT_MODELS[self.name])

    def _status(self, task_id: str, to: str, detail: str):
        # UI 전용 status 메시지 (캐릭터 애니메이션 트리거, §4/§8)
        self.q_out.put(make_envelope(
            task_id, self.name, to, "status", "running", {"detail": detail},
        ))

    @staticmethod
    def _score(text: str, keywords) -> int:
        low = text.lower()
        return sum(1 for kw in keywords if kw.lower() in low)

    async def _classify(self, text: str) -> str:
        # 1차: 키워드 점수. 동점/0점이면 Gemma 보정, 그래도 모르면 brain 기본
        brain_s = self._score(text, config.ASSISTANT_BRAIN_KEYWORDS)
        sched_s = self._score(text, config.ASSISTANT_SCHEDULE_KEYWORDS)
        if brain_s > sched_s:
            return "brain"
        if sched_s > brain_s:
            return "schedule"
        prompt = (
            "다음 요청을 brain 또는 schedule 중 하나로 분류하세요.\n"
            "- brain: 저장해둔 메모/정리/정보 검색\n"
            "- schedule: 일정/할일 추가·조회·수정·삭제\n"
            "반드시 brain 또는 schedule 한 단어만 출력하세요.\n\n"
            f"요청: {text}\n답:"
        )
        answer = (await self._ollama(prompt)).strip().lower()
        if "schedule" in answer:
            return "schedule"
        return "brain"

    async def handle(self, envelope: dict) -> dict:
        task_id = envelope["task_id"]
        text = envelope["payload"]["text"]
        self._status(task_id, "orchestrator", "요청 분석 중")  # 규약 1: PM 작업 시작
        target = await self._classify(text)
        agent = self.brain if target == "brain" else self.schedule

        self._status(task_id, agent.name, f"{agent.name} 처리 중")
        # 규약 1: 하위 에이전트(brain/schedule) 작업 시작 running emit
        self.q_out.put(make_envelope(
            task_id, agent.name, self.name, "status", "running",
            {"detail": "작업 시작"},
        ))
        outcome = await agent.run(text)

        # 규약 1: 하위 에이전트 종료 envelope(q_out) + PM 자신의 종료 envelope(반환).
        # 파일 없음/깨짐 등 실패도 error envelope로 오케스트레이터까지 전파 (§4 규칙)
        leaf_type = "result" if outcome["ok"] else "error"
        leaf_status = "success" if outcome["ok"] else outcome["status"]
        self.q_out.put(make_envelope(
            task_id, agent.name, self.name, leaf_type, leaf_status,
            outcome["payload"],
        ))
        return make_envelope(
            task_id, self.name, "orchestrator", leaf_type, leaf_status,
            outcome["payload"],
        )
