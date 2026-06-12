# tests/test_settings.py - 개발팀 모델/effort 설정 + autonomy_mode 테스트
#
# 실행: python tests/test_settings.py
# test_stage3.py 패턴: 실제 CLI/네트워크 불필요, stub/mock 주입
# 검증: 설정 로드/저장/원자성/깨진 JSON 복구/잘못된 값 거부 /
#       estimate_intensity 1·2차 / manual·auto·approval 모드 /
#       승인 타임아웃 시 default 진행 / CLI 플래그 / 오케스트레이터 y/n 라우팅

import asyncio
import json
import os
import queue
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teams.dev.pm as devpm_mod
from messages import make_envelope, new_task_id
from teams.dev import settings as dev_settings
from teams.dev.agents import ClaudeCodeAgent, CodexAgent
from teams.dev.pm import DevPM, estimate_intensity

HIGH_PROMPT = "전체 시스템 아키텍처 리팩토링과 마이그레이션 설계를 진행해줘"
LOW_PROMPT = "주석 오타 한 줄 수정해줘"
AMBIGUOUS_PROMPT = "로그인 기능의 버그를 수정해줘"


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


async def must_not_call(_prompt):
    raise AssertionError("LLM이 호출되면 안 됨")


class RecordingAgent(ClaudeCodeAgent):
    # 실제 subprocess 없이 PM이 전달한 (model, effort)만 기록
    name = "recording_agent"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls: list[tuple] = []

    async def run(self, instruction, cwd, on_attempt=None, model=None, effort=None):
        self.calls.append((model, effort))
        return {"ok": True, "status": "success",
                "payload": {"agent": self.name, "result": "ok"}}


def make_pm(q_out, agent, mode, model="sonnet", effort="medium", llm_call=None):
    settings = {"default_model": model, "default_effort": effort,
                "autonomy_mode": mode}
    return DevPM(q_out, claude_agent=agent, codex_agent=agent,
                 settings=settings, llm_call=llm_call)


def dev_request(text, cwd):
    return make_envelope(new_task_id(), "orchestrator", "pm_dev", "request",
                         "pending", {"text": text, "cwd": cwd})


# ---------------- 설정 로드/저장 ----------------

async def test_settings_load_creates_default():
    path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    s = dev_settings.load_settings(path)
    assert s == dev_settings.DEFAULTS, s
    assert os.path.exists(path)  # 파일 없으면 기본값으로 생성
    print("PASS: load_settings 기본값 생성")


async def test_settings_save_and_reload():
    path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    saved = await dev_settings.save_settings(
        {"default_model": "opus", "default_effort": "xhigh",
         "autonomy_mode": "auto"}, path)
    reloaded = dev_settings.load_settings(path)
    assert reloaded == saved, reloaded
    assert reloaded["default_model"] == "opus"
    # 원자적 쓰기: 임시 파일이 남아 있지 않아야 함
    leftovers = [f for f in os.listdir(os.path.dirname(path)) if ".tmp." in f]
    assert not leftovers, leftovers
    print("PASS: save/reload 영속화 + 임시파일 정리(원자성)")


async def test_settings_broken_json_recovery():
    path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ 깨진 json !!!")
    s = dev_settings.load_settings(path)
    assert s == dev_settings.DEFAULTS, s
    assert json.load(open(path, encoding="utf-8")) == dev_settings.DEFAULTS  # 복구 저장
    print("PASS: 깨진 JSON → 기본값 복구")


async def test_settings_invalid_values_rejected():
    path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    for bad in (
        {"default_model": "gpt-9", "default_effort": "medium", "autonomy_mode": "manual"},
        {"default_model": "sonnet", "default_effort": "ultra", "autonomy_mode": "manual"},
        {"default_model": "sonnet", "default_effort": "medium", "autonomy_mode": "yolo"},
    ):
        try:
            await dev_settings.save_settings(bad, path)
            raise AssertionError(f"ValueError가 나야 함: {bad}")
        except ValueError:
            pass
    assert not os.path.exists(path)  # 잘못된 값은 파일을 건드리지 않음
    # 파일에 잘못된 값이 들어 있으면 로드 시 기본값 복구
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"default_model": "gpt-9", "default_effort": "medium",
                   "autonomy_mode": "manual"}, f)
    assert dev_settings.load_settings(path) == dev_settings.DEFAULTS
    print("PASS: 잘못된 값 거부 (ValueError) + 로드 시 복구")


# ---------------- estimate_intensity ----------------

async def test_intensity_rule_based():
    assert await estimate_intensity(HIGH_PROMPT, llm_call=must_not_call) == "high"
    assert await estimate_intensity(LOW_PROMPT, llm_call=must_not_call) == "low"
    long_prompt = "이 모듈을 새 구조로 바꿔줘. " * 40  # 500자 이상 → 고강도 가산점
    assert len(long_prompt) >= 500
    print("PASS: 1차 rule-based 고강도/저강도 즉시 반환")


async def test_intensity_ambiguous_calls_gemma():
    called = []

    async def stub(prompt):
        called.append(prompt)
        return "high"

    assert await estimate_intensity(AMBIGUOUS_PROMPT, llm_call=stub) == "high"
    assert called, "애매 구간에서 Gemma 보정이 호출돼야 함"
    print("PASS: 애매 구간 → Gemma 보정 호출")


async def test_intensity_gemma_failure_returns_medium():
    async def garbage(_prompt):
        return "글쎄요 잘 모르겠네요"

    async def boom(_prompt):
        raise RuntimeError("ollama down")

    assert await estimate_intensity(AMBIGUOUS_PROMPT, llm_call=garbage) == "medium"
    assert await estimate_intensity(AMBIGUOUS_PROMPT, llm_call=boom) == "medium"
    print("PASS: Gemma 파싱 실패/예외 → medium")


# ---------------- autonomy_mode ----------------

async def test_manual_mode_skips_intensity():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "manual", model="sonnet", effort="medium")

    called = []
    original = devpm_mod.estimate_intensity

    async def spy(prompt, llm_call=None):
        called.append(prompt)
        return "high"

    devpm_mod.estimate_intensity = spy
    try:
        result = await pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp()))
    finally:
        devpm_mod.estimate_intensity = original
    assert result["type"] == "result", result
    assert not called, "manual 모드에서 estimate_intensity가 호출되면 안 됨"
    assert agent.calls == [("sonnet", "medium")], agent.calls
    print("PASS: manual 모드 - intensity 미호출, default 고정")


async def test_auto_mode_switches_with_status():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "auto", model="sonnet", effort="medium")
    result = await pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp()))
    assert result["type"] == "result", result
    assert agent.calls == [("opus", "xhigh")], agent.calls
    details = [m["payload"].get("detail", "") for m in drain(q_out)
               if m["type"] == "status"]
    assert any("모델 변경" in d for d in details), details
    print("PASS: auto 모드 - 매핑 모델 사용 + '모델 변경' status 알림")


async def test_auto_mode_same_as_default_no_notice():
    q_out = queue.Queue()
    agent = RecordingAgent()
    # default가 opus면 high 제안 모델과 동일 → 알림 없이 그대로 진행
    pm = make_pm(q_out, agent, "auto", model="opus", effort="high")
    await pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp()))
    assert agent.calls == [("opus", "xhigh")], agent.calls
    msgs = drain(q_out)
    assert not any(m["type"] == "approval_request" for m in msgs)
    assert not any("모델 변경" in str(m["payload"]) for m in msgs
                   if m["type"] == "status"), msgs
    print("PASS: 제안 == default - 알림/승인 요청 없음")


async def _wait_for_approval_request(q_out, timeout=2.0):
    # q_out(queue.Queue)에서 approval_request가 나올 때까지 폴링
    seen = []
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        while not q_out.empty():
            m = q_out.get()
            seen.append(m)
            if m["type"] == "approval_request":
                return m, seen
        await asyncio.sleep(0.01)
    raise AssertionError(f"approval_request 미수신: {seen}")


async def test_approval_mode_approved():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "approval", model="sonnet", effort="medium")
    task = asyncio.create_task(pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp())))
    req, _ = await _wait_for_approval_request(q_out)
    payload = req["payload"]
    assert payload["proposed_model"] == "opus" and payload["default_model"] == "sonnet"
    assert pm.has_pending_approval()
    assert pm.resolve_approval(payload["request_id"], True)
    result = await task
    assert result["type"] == "result", result
    assert agent.calls == [("opus", "xhigh")], agent.calls
    print("PASS: approval 승인 → 제안 모델로 진행")


async def test_approval_mode_denied():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "approval", model="sonnet", effort="medium")
    task = asyncio.create_task(pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp())))
    req, _ = await _wait_for_approval_request(q_out)
    pm.resolve_approval(req["payload"]["request_id"], False)
    await task
    assert agent.calls == [("sonnet", "medium")], agent.calls
    print("PASS: approval 거부 → default로 진행")


async def test_approval_mode_timeout_uses_default():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "approval", model="sonnet", effort="medium")
    pm.approval_timeout = 0.05  # 60초 → 단축 (monkeypatch)
    result = await pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp()))
    assert result["type"] == "result", result  # 작업이 멈추지 않고 완료됨
    assert agent.calls == [("sonnet", "medium")], agent.calls
    assert not pm.has_pending_approval()
    print("PASS: approval 60초 타임아웃 → default로 자동 진행 (시스템 비정지)")


async def test_approval_mode_same_as_default_no_request():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "approval", model="opus", effort="high")
    await pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp()))
    assert not any(m["type"] == "approval_request" for m in drain(q_out))
    assert agent.calls == [("opus", "xhigh")], agent.calls
    print("PASS: approval 모드 - 제안 == default면 승인 요청 없음")


# ---------------- settings_update ----------------

async def test_settings_update_envelope():
    q_out = queue.Queue()
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "manual")
    tmp_path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    original_path = dev_settings.SETTINGS_PATH
    dev_settings.SETTINGS_PATH = tmp_path
    try:
        env = make_envelope(new_task_id(), "user", "pm_dev", "settings_update",
                            "pending", {"autonomy_mode": "auto", "default_model": "opus"})
        result = await pm.handle_settings_update(env)
        assert result["type"] == "status" and result["status"] == "success", result
        assert pm.settings["autonomy_mode"] == "auto"
        assert dev_settings.load_settings(tmp_path)["default_model"] == "opus"  # 영속화

        bad = make_envelope(new_task_id(), "user", "pm_dev", "settings_update",
                            "pending", {"default_model": "없는모델"})
        result = await pm.handle_settings_update(bad)
        assert result["type"] == "error", result
        assert pm.settings["default_model"] == "opus"  # 실패 시 기존 설정 유지
    finally:
        dev_settings.SETTINGS_PATH = original_path
    print("PASS: settings_update 저장/회신 + 잘못된 값 error")


# ---------------- CLI 플래그 ----------------

async def test_agent_command_flags():
    claude = ClaudeCodeAgent()
    assert claude.model_flags("opus", "xhigh") == ["--model", "opus", "--effort", "xhigh"]
    assert claude.model_flags(None, None) == []
    codex = CodexAgent()
    flags = codex.model_flags("opus", "xhigh")  # claude 별칭은 codex에 전달 안 함
    assert "--model" not in flags and "-m" not in flags, flags
    assert flags == ["-c", "model_reasoning_effort=high"], flags

    # run() 경유 시 실제 커맨드에 플래그가 포함되는지 (subprocess stub)
    from teams.dev import agents as agents_mod
    recorded = {}

    class FakeResult:
        timeout_kind = None
        returncode = 0
        stdout = json.dumps({"result": "ok", "is_error": False})
        stderr = ""
        duration = 0.0

    async def fake_run_with_retry(cmd, cwd, idle, absolute, retries, on_attempt=None):
        recorded["cmd"] = cmd
        return FakeResult(), 1

    original = agents_mod.subprocess_runner.run_with_retry
    agents_mod.subprocess_runner.run_with_retry = fake_run_with_retry
    try:
        outcome = await claude.run("hello", None, model="opus", effort="xhigh")
    finally:
        agents_mod.subprocess_runner.run_with_retry = original
    assert outcome["ok"], outcome
    cmd = recorded["cmd"]
    assert cmd[cmd.index("--model") + 1] == "opus", cmd
    assert cmd[cmd.index("--effort") + 1] == "xhigh", cmd
    print("PASS: CLI 커맨드에 모델/effort 플래그 포함")


# ---------------- 오케스트레이터 y/n 라우팅 ----------------

async def test_orchestrator_approval_routing():
    from orchestrator import Orchestrator
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "approval", model="sonnet", effort="medium")
    orch.pms["dev"] = pm

    task = asyncio.create_task(pm.handle(dev_request(HIGH_PROMPT, tempfile.mkdtemp())))
    req, _ = await _wait_for_approval_request(q_out)

    def user_req(text):
        return make_envelope(new_task_id(), "user", "orchestrator", "request",
                             "pending", {"text": text})

    # 승인 대기 중 y/n 외 입력 → 안내 후 무시 (새 작업 분류 안 함)
    await orch.handle(user_req("다른 작업 해줘"))
    msgs = drain(q_out)
    assert any(m["type"] == "error"
               and m["payload"].get("reason") == "approval_pending" for m in msgs), msgs
    assert pm.has_pending_approval()
    assert orch._pending_input is None  # 보류 상태 동시 존재 금지

    # y 입력 → approval_response 변환 + Future 해제
    await orch.handle(user_req("y"))
    msgs = drain(q_out)
    assert any(m["type"] == "approval_response"
               and m["payload"]["approved"] is True for m in msgs), msgs
    result = await task
    assert result["type"] == "result", result
    assert agent.calls == [("opus", "xhigh")], agent.calls
    print("PASS: 오케스트레이터 - y/n → approval_response 라우팅 + 기타 입력 보류 안내")


async def test_orchestrator_settings_update_routing():
    from orchestrator import Orchestrator
    q_out = queue.Queue()
    orch = Orchestrator(q_out)
    agent = RecordingAgent()
    pm = make_pm(q_out, agent, "manual")
    orch.pms["dev"] = pm

    tmp_path = os.path.join(tempfile.mkdtemp(), "dev_settings.json")
    original_path = dev_settings.SETTINGS_PATH
    dev_settings.SETTINGS_PATH = tmp_path
    try:
        await orch.handle(make_envelope(
            new_task_id(), "user", "pm_dev", "settings_update", "pending",
            {"default_effort": "high"}))
    finally:
        dev_settings.SETTINGS_PATH = original_path
    msgs = drain(q_out)
    assert any(m["type"] == "status" and m["status"] == "success"
               and "설정 저장됨" in m["payload"].get("detail", "") for m in msgs), msgs
    assert pm.settings["default_effort"] == "high"
    print("PASS: 오케스트레이터 - settings_update를 pm_dev로 직접 라우팅")


async def main():
    await test_settings_load_creates_default()
    await test_settings_save_and_reload()
    await test_settings_broken_json_recovery()
    await test_settings_invalid_values_rejected()
    await test_intensity_rule_based()
    await test_intensity_ambiguous_calls_gemma()
    await test_intensity_gemma_failure_returns_medium()
    await test_manual_mode_skips_intensity()
    await test_auto_mode_switches_with_status()
    await test_auto_mode_same_as_default_no_notice()
    await test_approval_mode_approved()
    await test_approval_mode_denied()
    await test_approval_mode_timeout_uses_default()
    await test_approval_mode_same_as_default_no_request()
    await test_settings_update_envelope()
    await test_agent_command_flags()
    await test_orchestrator_approval_routing()
    await test_orchestrator_settings_update_routing()
    print("\nALL SETTINGS TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())

