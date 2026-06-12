# tests/verify_fastpath.py — 성능 개선 검증 스크립트 (일회성, 네트워크 필요 항목은 자동 스킵)
#
# 1) "안녕?" → fast-path: LLM 호출 없이 1초 내 result/success 응답 (Ollama 불필요)
# 2) 일반 쿼리 → 키워드 매칭으로 brain까지 라우팅 (stage4와 동일, Ollama 불필요)
# 3) (Ollama 실행 중일 때만) 비키워드 쿼리 → qwen3:4b 실제 분류 호출

import asyncio
import os
import queue
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import config
from messages import make_envelope, new_task_id
from orchestrator import Orchestrator


def make_request(text: str) -> dict:
    return make_envelope(new_task_id(), "user", "orchestrator", "request", "pending",
                         {"text": text})


async def llm_must_not_be_called(prompt: str) -> str:
    raise AssertionError("fast-path가 LLM을 호출함")


def drain(q: queue.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_fastpath():
    q = queue.Queue()
    orch = Orchestrator(q)
    orch._call_ollama = llm_must_not_be_called  # LLM 호출되면 즉시 실패

    t0 = time.perf_counter()
    asyncio.run(orch.handle(make_request("안녕?")))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    msgs = drain(q)
    # 규약 1: 시작 running status + result, 2개
    assert len(msgs) == 2 and msgs[0]["type"] == "status", msgs
    m = msgs[-1]
    assert m["type"] == "result" and m["status"] == "success", m
    assert m["payload"].get("fast_path") is True, m
    assert m["payload"]["result"] == config.GREETING_REPLY, m
    assert set(m) == {"task_id", "from", "to", "type", "status", "payload", "timestamp"}, m
    assert elapsed_ms < 1000, f"{elapsed_ms:.0f}ms"
    print(f"PASS: fast-path 인사 응답 {elapsed_ms:.0f}ms (<1s, LLM 미호출)")


def test_normal_routing():
    # 일반 쿼리는 기존대로 personal 팀 → brain까지 라우팅되는지 (stage4 stub 방식)
    q = queue.Queue()
    orch = Orchestrator(q)
    orch._call_ollama = llm_must_not_be_called  # 키워드 매칭으로 충분해야 함

    asyncio.run(orch.handle(make_request("어제 정리한 메모 찾아줘")))
    msgs = drain(q)
    froms = [m["from"] for m in msgs]
    assert "brain" in froms, froms  # brain이 result 또는 error envelope를 보냈는지
    print(f"PASS: 일반 쿼리 brain 라우팅 (envelope from={froms})")


def test_live_qwen_classify():
    try:
        httpx.get(f"{config.OLLAMA_BASE_URL}/api/version", timeout=2)
    except httpx.HTTPError:
        print("SKIP: Ollama 미실행 → 라이브 분류 테스트 생략")
        return
    q = queue.Queue()
    orch = Orchestrator(q)
    t0 = time.perf_counter()
    team = asyncio.run(orch.classify_team("저녁 메뉴 추천해줘"))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"PASS(live): qwen3:4b 분류={team}, {elapsed_ms:.0f}ms")


if __name__ == "__main__":
    test_fastpath()
    test_normal_routing()
    test_live_qwen_classify()
    print("\nALL FAST-PATH CHECKS PASSED")
