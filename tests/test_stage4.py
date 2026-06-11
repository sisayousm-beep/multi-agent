# tests/test_stage4.py — 4단계 개인 비서팀 테스트 (네트워크/Ollama 불필요, stub 주입)
#
# 실행: python tests/test_stage4.py
# 검증: 브레인 매칭/근거 / stale 경고(리스크 4) / 파일없음·깨짐 error /
#       스케줄 CRUD(원자적 쓰기) / PM 라우팅 / 오케스트레이터 E2E(2단계 구조 유지)

import asyncio
import json
import os
import queue
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messages import make_envelope, new_task_id
from orchestrator import Orchestrator
from teams.personal.pm import PersonalPM
from teams.personal.brain import BrainAgent
from teams.personal.schedule import ScheduleAgent

ENVELOPE_KEYS = {"task_id", "from", "to", "type", "status", "payload", "timestamp"}

SAMPLE_SUMMARY = {
    "documents": [
        {"id": "rag-notes", "title": "RAG 정리", "path": "notes/rag.md",
         "summary": "RAG는 검색 결과를 프롬프트에 넣어 환각을 줄이는 패턴.",
         "keywords": ["rag", "임베딩"], "tags": ["llm"], "related_topics": ["벡터 검색"]},
        {"id": "asyncio-notes", "title": "asyncio 메모", "path": "notes/asyncio.md",
         "summary": "이벤트 루프는 단일 스레드, 블로킹은 executor로.",
         "keywords": ["asyncio", "동시성"], "tags": ["python"], "related_topics": ["스레드"]},
    ]
}


def user_request(text):
    return make_envelope(new_task_id(), "user", "orchestrator", "request", "pending",
                         {"text": text})


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


async def stub_empty(_prompt):
    return ""


def write_summary(tmp, data=SAMPLE_SUMMARY):
    p = os.path.join(tmp, "summary.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


# ---------------- 브레인 ----------------

async def test_brain_match():
    with tempfile.TemporaryDirectory() as tmp:
        brain = BrainAgent(summary_path=write_summary(tmp),
                           brain_md_path=os.path.join(tmp, "none.md"),
                           ollama_call=stub_empty)
        out = await brain.run("RAG 관련해서 내가 정리한 거 있어?")
        assert out["ok"], out
        p = out["payload"]
        assert p["matched"][0]["id"] == "rag-notes", p
        assert "RAG" in p["answer"], p
        assert p["stale"] is False, p
        assert p["used_llm"] is False, p  # rule 매칭으로 충분 → llm 미사용
    print("PASS: brain keyword match + summary 근거")


async def test_brain_stale():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_summary(tmp)
        old = os.path.getmtime(path) - 48 * 3600  # 48시간 전으로 위조
        os.utime(path, (old, old))
        brain = BrainAgent(summary_path=path,
                           brain_md_path=os.path.join(tmp, "none.md"),
                           ollama_call=stub_empty)
        out = await brain.run("rag 정리")
        assert out["ok"] and out["payload"]["stale"] is True, out
        assert "warning" in out["payload"], out
    print("PASS: brain stale 경고 (리스크 4)")


async def test_brain_file_missing():
    brain = BrainAgent(summary_path=os.path.join(tempfile.gettempdir(), "nope_xyz.json"),
                       ollama_call=stub_empty)
    out = await brain.run("rag")
    assert not out["ok"] and out["payload"]["reason"] == "file_not_found", out
    print("PASS: brain 파일 없음 → error")


async def test_brain_corrupt():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "summary.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ broken json ")
        brain = BrainAgent(summary_path=path, ollama_call=stub_empty)
        out = await brain.run("rag")
        assert not out["ok"] and out["payload"]["reason"] == "file_corrupt", out
    print("PASS: brain 깨진 파일 → error")


async def test_brain_ambiguous_uses_llm():
    # 두 문서가 동점(rag/asyncio 둘 다 1 hit)되도록 질문 구성 → llm 보정 경로
    async def pick_asyncio(_prompt):
        return "asyncio-notes"
    with tempfile.TemporaryDirectory() as tmp:
        brain = BrainAgent(summary_path=write_summary(tmp),
                           brain_md_path=os.path.join(tmp, "none.md"),
                           ollama_call=pick_asyncio)
        out = await brain.run("rag 랑 asyncio 중에 뭐 정리했더라")
        assert out["ok"] and out["payload"]["used_llm"] is True, out
        assert out["payload"]["matched"][0]["id"] == "asyncio-notes", out
    print("PASS: brain 동점 → Gemma 관련도 보정")


# ---------------- 스케줄 ----------------

async def test_schedule_crud():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "schedule.json")
        sched = ScheduleAgent(path=path)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        add = await sched.run("내일 3시 회의 추가")
        assert add["ok"] and add["payload"]["action"] == "add", add
        item = add["payload"]["item"]
        assert item["title"] == "회의" and item["date"] == tomorrow, item
        assert item["time"] == "03:00", item
        # 원자적 교체 후 임시 파일 잔존 없음
        assert os.path.exists(path) and not any(f.startswith("schedule.json.tmp") for f in os.listdir(tmp)), os.listdir(tmp)

        await sched.run("오늘 스탠드업 추가")
        today_out = await sched.run("오늘 일정 알려줘")
        assert today_out["payload"]["action"] == "today", today_out
        assert today_out["payload"]["count"] == 1, today_out
        assert today_out["payload"]["items"][0]["title"] == "스탠드업", today_out

        lst = await sched.run("일정 목록 보여줘")
        assert lst["payload"]["count"] == 2, lst

        dele = await sched.run("1번 삭제")
        assert dele["ok"] and dele["payload"]["deleted_id"] == 1, dele
        lst2 = await sched.run("전체 조회")
        assert lst2["payload"]["count"] == 1, lst2

        upd = await sched.run("2번 수정 모레 14:00")
        assert upd["ok"] and upd["payload"]["action"] == "update", upd
        assert upd["payload"]["item"]["time"] == "14:00", upd
    print("PASS: 스케줄 CRUD 5종 + 원자적 쓰기")


# ---------------- PM 라우팅 ----------------

async def test_pm_routing():
    q_out = queue.Queue()
    with tempfile.TemporaryDirectory() as tmp:
        pm = PersonalPM(q_out,
                        brain=BrainAgent(summary_path=write_summary(tmp),
                                         brain_md_path=os.path.join(tmp, "none.md"),
                                         ollama_call=stub_empty),
                        schedule=ScheduleAgent(path=os.path.join(tmp, "s.json")),
                        ollama_call=stub_empty)
        r1 = await pm.handle(user_request("RAG 관련해서 내가 정리한 거 있어?"))
        assert r1["from"] == "brain" and r1["type"] == "result", r1
        r2 = await pm.handle(user_request("내일 3시 회의 추가"))
        assert r2["from"] == "schedule" and r2["type"] == "result", r2
        assert ENVELOPE_KEYS <= set(r1.keys()) and ENVELOPE_KEYS <= set(r2.keys())
    print("PASS: PM 브레인/스케줄 라우팅")


# ---------------- 오케스트레이터 E2E (2단계 구조 유지) ----------------

async def test_orchestrator_e2e():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    # 기본 personal PM이 mock이 아닌 실제 PersonalPM으로 교체됐는지
    assert isinstance(orch.pms["personal"], PersonalPM), orch.pms["personal"]
    assert isinstance(orch.pms["personal"].brain, BrainAgent)

    async def to_personal(_prompt):
        return "personal"
    orch._call_ollama = to_personal  # 분류 stub (네트워크 불필요)

    tmp = tempfile.mkdtemp()
    orch.pms["personal"] = PersonalPM(
        q_out,
        brain=BrainAgent(summary_path=write_summary(tmp),
                         brain_md_path=os.path.join(tmp, "none.md"),
                         ollama_call=stub_empty),
        schedule=ScheduleAgent(path=os.path.join(tmp, "s.json")),
        ollama_call=stub_empty,
    )

    await orch.handle(user_request("RAG 관련해서 내가 정리한 거 있어?"))
    msgs = drain(q_out)
    assert all(ENVELOPE_KEYS <= set(m.keys()) for m in msgs), msgs
    res = [m for m in msgs if m["type"] == "result"]
    assert len(res) == 1 and res[0]["from"] == "brain", msgs
    assert "RAG" in res[0]["payload"]["answer"], res[0]
    assert any(m["type"] == "status" for m in msgs), msgs  # 진행중 status 분리

    await orch.handle(user_request("내일 3시 회의 추가"))
    msgs = drain(q_out)
    res = [m for m in msgs if m["type"] == "result"]
    assert len(res) == 1 and res[0]["from"] == "schedule", msgs
    assert res[0]["payload"]["action"] == "add", res[0]
    print("PASS: 오케스트레이터 → 비서팀 PM E2E (브레인/스케줄)")


async def test_orchestrator_brain_error():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)

    async def to_personal(_prompt):
        return "personal"
    orch._call_ollama = to_personal
    orch.pms["personal"] = PersonalPM(
        q_out,
        brain=BrainAgent(summary_path=os.path.join(tempfile.gettempdir(), "missing_abc.json"),
                         ollama_call=stub_empty),
        ollama_call=stub_empty,
    )
    await orch.handle(user_request("RAG 관련 정리한 거 찾아줘"))
    msgs = drain(q_out)
    errs = [m for m in msgs if m["type"] == "error"]
    assert len(errs) == 1 and errs[0]["from"] == "brain", msgs
    assert errs[0]["payload"]["reason"] == "file_not_found", errs[0]
    print("PASS: 파일 없음 error 경로 오케스트레이터까지 전파")


async def main():
    await test_brain_match()
    await test_brain_stale()
    await test_brain_file_missing()
    await test_brain_corrupt()
    await test_brain_ambiguous_uses_llm()
    await test_schedule_crud()
    await test_pm_routing()
    await test_orchestrator_e2e()
    await test_orchestrator_brain_error()
    print("\nALL STAGE-4 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
