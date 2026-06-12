# tests/test_ui_status.py - UI 에이전트 활성 표시 + 태스크 상태 바 검증 (headless)
#
# 실행: python tests/test_ui_status.py
# mock 태스크 1개를 성공/타임아웃 두 시나리오로 UIApp.apply_envelope에 흘려
# 요구 2(활성 집합), 3(액터 active 플래그), 4(종결 정의), 5(상태 바 전이)를 확인.
# SDL dummy 드라이버로 창 없이 동작 (Pygame 메인 스레드 규칙 그대로).

import os
import queue
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from messages import make_envelope, new_task_id
from ui.main import UIApp


class FakeRuntime:
    # q_in/q_out 인터페이스만 제공 (연결점 2개 규칙 유지)
    def __init__(self):
        self.q_in = queue.Queue()
        self.q_out = queue.Queue()

    def start(self):
        pass

    def stop(self):
        pass


def env(task_id, sender, to, msg_type, status, payload=None):
    return make_envelope(task_id, sender, to, msg_type, status, payload or {})


def active(app, name):
    return bool(app.active_tasks.get(name))


def test_success_flow(app):
    app.task = app._idle_task()
    app.submit_user_input("버그 수정해줘")
    tid = app.task["id"]
    assert app.task["phase"] == "active", app.task

    # orchestrator → pm_dev → claude_code 순서로 running
    app.apply_envelope(env(tid, "orchestrator", "user", "status", "running",
                           {"detail": "요청 분석 중"}))
    assert active(app, "orchestrator"), app.active_tasks
    assert app.task["agent"] == "orchestrator", app.task

    app.apply_envelope(env(tid, "pm_dev", "orchestrator", "status", "running",
                           {"detail": "요청 분석 중"}))
    app.apply_envelope(env(tid, "claude_code", "pm_dev", "status", "running",
                           {"detail": "작업 시작"}))
    # 동시에 여러 에이전트 활성 (요구 3: 독립 표시)
    assert active(app, "orchestrator") and active(app, "pm_dev") and active(app, "claude_code")
    assert app.task["agent"] == "claude_code", app.task  # "현재: claude_code 작업"

    # claude_code 종료 → 본인 집합에서만 제거, pm_dev는 여전히 활성
    app.apply_envelope(env(tid, "claude_code", "pm_dev", "result", "success",
                           {"result": "수정 완료"}))
    assert not active(app, "claude_code") and active(app, "pm_dev"), app.active_tasks

    # PM 종료 - 아직 user 반환 전이므로 "완료" 아님 (요구 4)
    app.apply_envelope(env(tid, "pm_dev", "orchestrator", "result", "success",
                           {"result": "수정 완료"}))
    assert app.task["phase"] == "active", app.task

    # orchestrator → user 최종 result = 완료
    app.apply_envelope(env(tid, "orchestrator", "user", "result", "success",
                           {"result": "수정 완료"}))
    assert app.task["phase"] == "done", app.task
    assert all(tid not in ids for ids in app.active_tasks.values()), app.active_tasks
    print("PASS: 성공 시나리오 - 활성 집합 추가/제거 + user 최종 반환 시점에만 완료")


def test_timeout_flow(app):
    app.task = app._idle_task()
    app.submit_user_input("codex로 리팩토링")
    tid = app.task["id"]

    app.apply_envelope(env(tid, "orchestrator", "user", "status", "running",
                           {"detail": "요청 분석 중"}))
    app.apply_envelope(env(tid, "pm_dev", "orchestrator", "status", "running",
                           {"detail": "요청 분석 중"}))
    app.apply_envelope(env(tid, "codex", "pm_dev", "status", "running",
                           {"detail": "작업 시작"}))
    assert active(app, "codex"), app.active_tasks

    # 재시도 소진 후 idle timeout → error 전파
    app.apply_envelope(env(tid, "codex", "pm_dev", "error", "timeout",
                           {"reason": "idle_timeout", "attempts": 2}))
    assert not active(app, "codex"), app.active_tasks
    assert app.task["phase"] == "active", app.task  # 에이전트 개별 종료 ≠ 태스크 종결

    app.apply_envelope(env(tid, "pm_dev", "orchestrator", "error", "timeout",
                           {"reason": "idle_timeout", "attempts": 2}))
    app.apply_envelope(env(tid, "orchestrator", "user", "error", "timeout",
                           {"reason": "idle_timeout", "attempts": 2}))
    assert app.task["phase"] == "failed", app.task
    assert "timeout" in app.task["msg"] and "재시도" in app.task["msg"], app.task
    assert all(tid not in ids for ids in app.active_tasks.values()), app.active_tasks

    # 수동 확인 전까지 유지: 다른 태스크 envelope가 와도 failed 유지 (요구 5)
    app.apply_envelope(env(new_task_id(), "orchestrator", "user", "status", "running",
                           {"detail": "요청 분석 중"}))
    assert app.task["phase"] == "failed", app.task

    # 상태 바 클릭(수동 확인) → idle 복귀
    app._ack_task_failure()
    assert app.task["phase"] == "idle", app.task
    print("PASS: 타임아웃 시나리오 - 실패 종결 표시 + 수동 확인 전까지 유지")


def test_actor_flag_sync(app):
    # run() 루프의 actor.active 동기화 한 줄과 동일한 갱신 검증 (요구 3)
    tid = new_task_id()
    app.task = app._idle_task()
    app.apply_envelope(env(tid, "brain", "pm_assistant", "status", "running",
                           {"detail": "작업 시작"}))
    for name, actor in app.actors.items():
        actor.active = bool(app.active_tasks.get(name))
    assert app.actors["brain"].active and not app.actors["schedule"].active
    app.apply_envelope(env(tid, "brain", "pm_assistant", "result", "success", {}))
    for name, actor in app.actors.items():
        actor.active = bool(app.active_tasks.get(name))
    assert not app.actors["brain"].active
    print("PASS: 액터 active 플래그 동기화 (노란 점멸/이름 하이라이트 조건)")


if __name__ == "__main__":
    app = UIApp(FakeRuntime())
    test_success_flow(app)
    test_timeout_flow(app)
    test_actor_flag_sync(app)
    print("\nALL UI-STATUS TESTS PASSED")
