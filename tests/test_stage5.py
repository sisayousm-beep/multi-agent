# tests/test_stage5.py — 5단계 ComfyUI 에이전트 + GPU 중재 테스트 (네트워크 불필요, stub 주입)
#
# 실행: python tests/test_stage5.py
# 검증: health 실패→비활성화→error(리스크 3) / 다른 팀 정상 / 해피패스 순서
#       (Ollama 언로드→제출→폴링→result→재로드, 리스크 6) / 전환 중 busy 안내(데드락 없음)
#       / 폴링 타임아웃 시 재로드 보장(finally) / 노드 에러 envelope / 템플릿 치환

import asyncio
import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messages import make_envelope, new_task_id
from orchestrator import Orchestrator
from comfyui_agent import ComfyUIAgent, ComfyUIError
from gpu_arbiter import GpuArbiter

ENVELOPE_KEYS = {"task_id", "from", "to", "type", "status", "payload", "timestamp"}


def user_request(text):
    return make_envelope(new_task_id(), "user", "orchestrator", "request", "pending",
                         {"text": text})


def drain(q):
    msgs = []
    while not q.empty():
        msgs.append(q.get())
    return msgs


def make_arbiter(q_out, log):
    # Ollama 호출을 네트워크 없이 대체. loaded=[] → 언로드 확인 즉시 통과.
    async def unload():
        log.append("unload")

    async def loaded():
        return []

    async def reload():
        log.append("reload")

    return GpuArbiter(q_out, unload=unload, loaded_models=loaded, reload=reload)


def make_agent(q_out, *, poll_ret=None, poll_exc=None, log=None):
    agent = ComfyUIAgent(q_out=q_out, auto_health_check=False)
    agent.enabled = True  # health check 통과한 셈

    async def _submit(client, workflow):
        if log is not None:
            log.append("submit")
        return "pid-test"

    async def _poll(client, prompt_id):
        if log is not None:
            log.append("poll")
        if poll_exc is not None:
            raise poll_exc
        return poll_ret

    agent._submit = _submit
    agent._poll = _poll
    return agent


HISTORY_OK = {
    "status": {"completed": True, "status_str": "success"},
    "outputs": {"9": {"images": [
        {"filename": "lola_00001_.png", "subfolder": "", "type": "output"}]}},
}


# ---------------- 리스크 3: health 실패 → 비활성화 → error ----------------

async def test_comfyui_unavailable():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    log = []

    agent = ComfyUIAgent(q_out=q_out, auto_health_check=False)

    async def unavailable():
        return False
    agent.ensure_available = unavailable
    orch.comfyui_agent = agent
    orch.arbiter = make_arbiter(q_out, log)

    await orch.handle(user_request("lola 스타일로 고양이 그려줘"))
    msgs = drain(q_out)
    errs = [m for m in msgs if m["type"] == "error" and m["from"] == "comfyui"]
    assert len(errs) == 1, msgs
    assert errs[0]["payload"]["reason"] == "comfyui_unavailable", errs[0]
    # GPU를 헛스왑하지 않음 (언로드/재로드 호출 없음)
    assert log == [], log
    assert orch.arbiter.busy is False
    print("PASS: ComfyUI 미실행 → 비활성화 → error (리스크 3, GPU 헛스왑 없음)")


async def test_other_team_works_when_comfyui_down():
    # comfyui 비활성화 상태에서도 나머지 팀 정상 동작
    q_out = queue.Queue()
    orch = Orchestrator(q_out)

    class StubPM:
        async def handle(self, env):
            return make_envelope(env["task_id"], "pm_assistant", "orchestrator",
                                 "result", "success", {"result": "ok"})
    orch.pms["personal"] = StubPM()

    await orch.handle(user_request("내일 일정 알려줘"))  # 키워드 → personal
    msgs = drain(q_out)
    res = [m for m in msgs if m["type"] == "result"]
    # 규약 4: PM result + orchestrator→user 최종, 2개 (StubPM은 leaf emit 없음)
    assert len(res) == 2 and res[0]["from"] == "pm_assistant", msgs
    assert res[-1]["from"] == "orchestrator" and res[-1]["to"] == "user", res[-1]
    print("PASS: comfyui 비활성화 상태에서도 personal 팀 정상")


# ---------------- 리스크 6: 해피패스 순서 (언로드→제출→폴링→result→재로드) ----------------

async def test_happy_path_order():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    log = []
    orch.comfyui_agent = make_agent(q_out, poll_ret=HISTORY_OK, log=log)
    orch.arbiter = make_arbiter(q_out, log)

    await orch.handle(user_request("lola 스타일로 고양이 그려줘"))
    msgs = drain(q_out)

    # 순차 전환 순서 보장 (리스크 6)
    assert log == ["unload", "submit", "poll", "reload"], log
    res = [m for m in msgs if m["type"] == "result" and m["from"] == "comfyui"]
    assert len(res) == 1, msgs
    imgs = res[0]["payload"]["images"]
    assert imgs[0]["filename"] == "lola_00001_.png", imgs
    assert imgs[0]["url"].endswith("filename=lola_00001_.png&subfolder=&type=output"), imgs
    # UI용 "모델 전환 중" status 송신 확인
    switch = [m for m in msgs if m["type"] == "status"
              and "전환 중" in m["payload"].get("detail", "")]
    assert len(switch) >= 2, msgs  # 언로드 + 재로드 안내
    assert orch.arbiter.busy is False
    assert all(ENVELOPE_KEYS <= set(m.keys()) for m in msgs), msgs
    print("PASS: 해피패스 - 언로드>제출>폴링>result>재로드 순서 (리스크 6)")


# ---------------- 전환 중 다른 요청 → busy 안내 (데드락 없음) ----------------

async def test_busy_no_deadlock():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    log = []
    orch.arbiter = make_arbiter(q_out, log)

    gate = asyncio.Event()
    agent = ComfyUIAgent(q_out=q_out, auto_health_check=False)
    agent.enabled = True

    async def slow_handle(env):
        await gate.wait()  # ComfyUI 작업이 진행 중인 상태를 모사
        return make_envelope(env["task_id"], "comfyui", "orchestrator",
                             "result", "success", {"images": [], "prompt_id": "pid-test"})
    agent.handle = slow_handle
    orch.comfyui_agent = agent

    # dev PM이 실제 호출되지 않아야 함 (busy면 분류 전에 차단)
    class TrapPM:
        called = False

        async def handle(self, env):
            TrapPM.called = True
            return make_envelope(env["task_id"], "pm_dev", "orchestrator",
                                 "result", "success", {"result": "x"})
    orch.pms["dev"] = TrapPM()

    t1 = asyncio.create_task(orch.handle(user_request("lola 고양이 그려줘")))
    await asyncio.sleep(0.05)  # t1이 busy 설정 후 gate에서 대기하도록
    assert orch.arbiter.busy is True

    # 전환 중 dev 요청 → busy 안내로 즉시 반환 (블로킹/데드락 없음)
    await asyncio.wait_for(orch.handle(user_request("코드 짜줘")), timeout=2)
    busy_msgs = [m for m in drain(q_out)
                 if m["payload"].get("reason") == "comfyui_busy"]
    assert len(busy_msgs) == 1, busy_msgs
    assert TrapPM.called is False, "전환 중 dev PM이 호출되면 안 됨"

    gate.set()
    await asyncio.wait_for(t1, timeout=2)  # 데드락 없이 완료
    assert orch.arbiter.busy is False
    print("PASS: 전환 중 다른 요청 busy 안내 + 데드락 없음")


# ---------------- 폴링 타임아웃 → 재로드 보장 (finally) ----------------

async def test_timeout_still_reloads():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    log = []
    orch.comfyui_agent = make_agent(q_out, poll_exc=asyncio.TimeoutError(), log=log)
    orch.arbiter = make_arbiter(q_out, log)

    await orch.handle(user_request("lola 고양이 그려줘"))
    msgs = drain(q_out)
    errs = [m for m in msgs if m["type"] == "error" and m["from"] == "comfyui"]
    assert len(errs) == 1 and errs[0]["status"] == "timeout", msgs
    assert errs[0]["payload"]["reason"] == "poll_timeout", errs[0]
    assert "reload" in log, log  # 타임아웃에도 Gemma 재로드 (finally)
    assert orch.arbiter.busy is False
    print("PASS: 폴링 타임아웃 → timeout error + 재로드 보장 (finally)")


# ---------------- ComfyUI 노드 에러 → error envelope ----------------

async def test_node_error_envelope():
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    log = []
    orch.comfyui_agent = make_agent(
        q_out, poll_exc=ComfyUIError({"reason": "node_execution_error"}), log=log)
    orch.arbiter = make_arbiter(q_out, log)

    await orch.handle(user_request("lola 고양이 그려줘"))
    msgs = drain(q_out)
    errs = [m for m in msgs if m["type"] == "error" and m["from"] == "comfyui"]
    assert len(errs) == 1 and errs[0]["payload"]["reason"] == "comfyui_error", msgs
    assert "reload" in log, log
    print("PASS: ComfyUI 노드 에러 → error envelope + 재로드")


# ---------------- 템플릿 치환 ----------------

def test_build_workflow_substitution():
    agent = ComfyUIAgent(auto_health_check=False)
    wf = agent.build_workflow("고양이", seed=42, width=512, height=640)
    assert wf["6"]["inputs"]["text"] == "lola style, 고양이", wf["6"]
    assert wf["3"]["inputs"]["seed"] == 42, wf["3"]            # 정수 유지
    assert wf["5"]["inputs"]["width"] == 512, wf["5"]
    assert wf["5"]["inputs"]["height"] == 640, wf["5"]
    # 한글 + 따옴표 안전 (JSON 파싱 깨지지 않음)
    wf2 = agent.build_workflow('a "b" 고양이')
    assert wf2["6"]["inputs"]["text"] == 'lola style, a "b" 고양이', wf2["6"]
    print("PASS: 워크플로우 템플릿 치환 (프롬프트/시드/해상도, 한글·따옴표 안전)")


async def main():
    await test_comfyui_unavailable()
    await test_other_team_works_when_comfyui_down()
    await test_happy_path_order()
    await test_busy_no_deadlock()
    await test_timeout_still_reloads()
    await test_node_error_envelope()
    test_build_workflow_substitution()
    print("\nALL STAGE-5 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
