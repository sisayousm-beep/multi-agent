# ui/main.py — 6단계 Pygame 도트풍 UI (§6·§8)
#
# 실행:
#   python -m ui.main          # 실제 백엔드 (runtime.AgentRuntime)
#   python -m ui.main --mock   # 백엔드 없이 mock envelope로 UI 단독 테스트
#
# §6 동시성 모델 준수:
#   - Pygame은 메인 스레드에서 60fps 루프
#   - 백엔드와의 연결점은 q_in / q_out (queue.Queue) 2개뿐
#   - 매 프레임 q_out.get_nowait() + except queue.Empty (블로킹 금지)

import argparse
import queue
import sys
import time

import pygame

import token_metrics
from messages import make_envelope, new_task_id
from ui import layout
from ui.actors import Actor

BANNER_TTL = 3.0          # gpu_switch status 수신 후 배너 유지 시간 (초)
TASK_DONE_TTL = 5.0       # ✅ TASK 완료 표시 유지 시간 (초)
LOG_MAX_LINES = 500
LOG_LINE_H = 18


def load_korean_font(size: int) -> pygame.font.Font:
    # 한글 지원 시스템 폰트 탐색 (Windows: 맑은 고딕 우선)
    for name in ("malgungothic", "malgun gothic", "gulim",
                 "notosanscjkkr", "applesdgothicneo", "nanumgothic"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


def summarize_payload(payload) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    for key in ("text", "message", "detail", "result", "reason"):
        value = payload.get(key)
        if value:
            text = str(value).replace("\n", " ")
            return text if len(text) <= 90 else text[:90] + "…"
    return str(payload)[:90]


class LogView:
    """하단 대화 로그 (터미널 스타일, 마우스 휠 스크롤)."""

    def __init__(self, rect: pygame.Rect, font: pygame.font.Font):
        self.rect = rect
        self.font = font
        self.lines: list[tuple[str, tuple]] = []  # (텍스트, 색)
        self.scroll = 0  # 맨 아래에서 위로 올라간 줄 수 (0 = 최신 따라가기)

    def add(self, text: str, color):
        self.lines.append((text, color))
        if len(self.lines) > LOG_MAX_LINES:
            self.lines = self.lines[-LOG_MAX_LINES:]

    def on_wheel(self, dy: int):
        visible = (self.rect.h - 12) // LOG_LINE_H
        max_scroll = max(0, len(self.lines) - visible)
        self.scroll = max(0, min(max_scroll, self.scroll + dy * 3))

    def draw(self, screen: pygame.Surface):
        pygame.draw.rect(screen, layout.KV_BG, self.rect)
        pygame.draw.line(screen, layout.KV_ORANGE_DARK, self.rect.topleft, self.rect.topright)
        visible = (self.rect.h - 12) // LOG_LINE_H
        end = len(self.lines) - self.scroll
        start = max(0, end - visible)
        y = self.rect.y + 6
        for text, color in self.lines[start:end]:
            screen.blit(self.font.render(text, True, color), (self.rect.x + 10, y))
            y += LOG_LINE_H
        if self.scroll > 0:
            hint = self.font.render(f"▼ 최신 ({self.scroll}줄 위)", True, (130, 130, 140))
            screen.blit(hint, hint.get_rect(bottomright=(self.rect.right - 10,
                                                         self.rect.bottom - 4)))


class InputBox:
    """최하단 사용자 입력창. pygame.TEXTINPUT/TEXTEDITING으로 한글 IME 지원."""

    def __init__(self, rect: pygame.Rect, font: pygame.font.Font):
        self.rect = rect
        self.font = font
        self.text = ""          # 확정된 입력
        self.composition = ""   # IME 조합 중 텍스트 (밑줄 표시)
        pygame.key.start_text_input()
        pygame.key.set_text_input_rect(rect)

    def handle_event(self, event) -> str | None:
        """확정 입력(Enter)이 있으면 그 문자열 반환, 아니면 None."""
        if event.type == pygame.TEXTINPUT:
            self.text += event.text
            self.composition = ""
        elif event.type == pygame.TEXTEDITING:
            self.composition = event.text
        elif event.type == pygame.KEYDOWN and not self.composition:
            # 조합 중에는 백스페이스/엔터를 IME가 처리하므로 확정 텍스트만 건드림
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key == pygame.K_RETURN and self.text.strip():
                submitted = self.text.strip()
                self.text = ""
                return submitted
        return None

    def draw(self, screen: pygame.Surface):
        pygame.draw.rect(screen, (30, 28, 26), self.rect)
        pygame.draw.line(screen, layout.KV_ORANGE_DARK, self.rect.topleft, self.rect.topright)
        box = self.rect.inflate(-20, -24)
        pygame.draw.rect(screen, (18, 17, 17), box, border_radius=4)
        pygame.draw.rect(screen, layout.KV_ORANGE_DIM, box, width=1, border_radius=4)

        shown = "> " + self.text
        label = self.font.render(shown, True, (230, 230, 235))
        screen.blit(label, (box.x + 8, box.y + (box.h - label.get_height()) // 2))
        x = box.x + 8 + label.get_width()
        if self.composition:
            comp = self.font.render(self.composition, True, (255, 211, 80))
            y = box.y + (box.h - comp.get_height()) // 2
            screen.blit(comp, (x, y))
            pygame.draw.line(screen, (255, 211, 80),
                             (x, y + comp.get_height()), (x + comp.get_width(), y + comp.get_height()))
            x += comp.get_width()
        # 커서 (0.5초 점멸)
        if int(time.time() * 2) % 2 == 0:
            pygame.draw.line(screen, layout.KV_ORANGE,
                             (x + 2, box.y + 8), (x + 2, box.bottom - 8))


class SpeedPanel:
    """우측 토큰 출력 속도 그래프 패널 (우상단 버튼으로 확장/축소, JSON 내보내기)."""

    GRAPH_SAMPLES = 60  # 그래프에 표시할 최근 샘플 수

    def __init__(self, rect: pygame.Rect, font_title: pygame.font.Font,
                 font_small: pygame.font.Font):
        self.rect = rect
        self.font_title = font_title
        self.font_small = font_small
        self.graph_rect = pygame.Rect(rect.x + 16, rect.y + 72, rect.w - 32, 220)
        self.export_rect = pygame.Rect(rect.x + 16, self.graph_rect.bottom + 16,
                                       rect.w - 32, 32)
        self.notice = ""
        self.notice_until = 0.0

    def clicked_export(self, pos) -> bool:
        return self.export_rect.collidepoint(pos)

    def set_notice(self, text: str, ttl: float = 4.0):
        self.notice = text
        self.notice_until = time.time() + ttl

    def draw(self, screen: pygame.Surface):
        samples = token_metrics.snapshot()
        pygame.draw.rect(screen, layout.KV_BG, self.rect)
        pygame.draw.line(screen, layout.KV_ORANGE_DARK,
                         self.rect.topleft, self.rect.bottomleft)
        title = self.font_title.render("토큰 출력 속도 (tok/s)", True, layout.KV_ORANGE)
        screen.blit(title, (self.rect.x + 16, self.rect.y + 14))

        if samples:
            last = samples[-1]
            info = f"최근 {last['tps']} tok/s · {last['model']} · 샘플 {len(samples)}개"
        else:
            info = "샘플 없음 (Ollama 호출 시 기록됨)"
        screen.blit(self.font_small.render(info, True, layout.DEFAULT_TEXT),
                    (self.rect.x + 16, self.rect.y + 44))

        # 그래프: 최근 GRAPH_SAMPLES개를 y축 자동 스케일로 꺾은선 표시
        g = self.graph_rect
        pygame.draw.rect(screen, (18, 17, 17), g)
        pygame.draw.rect(screen, layout.KV_ORANGE_DIM, g, width=1)
        recent = samples[-self.GRAPH_SAMPLES:]
        if recent:
            max_tps = max(s["tps"] for s in recent) or 1.0
            for frac in (0.5, 1.0):  # 눈금선: 중간·최대
                y = g.bottom - 4 - int((g.h - 8) * frac)
                pygame.draw.line(screen, (50, 46, 42), (g.x + 1, y), (g.right - 2, y))
                tag = self.font_small.render(f"{max_tps * frac:.0f}", True, (130, 130, 140))
                screen.blit(tag, (g.x + 4, y + 2))
            pts = []
            n = len(recent)
            for i, s in enumerate(recent):
                x = g.x + 6 + (g.w - 12) * (i / max(1, n - 1))
                y = g.bottom - 4 - (g.h - 8) * (s["tps"] / max_tps)
                pts.append((x, y))
            if len(pts) >= 2:
                pygame.draw.lines(screen, layout.KV_ORANGE, False, pts, 2)
            lx, ly = pts[-1]
            pygame.draw.circle(screen, (255, 211, 80), (int(lx), int(ly)), 3)
        else:
            empty = self.font_small.render("데이터 없음", True, (130, 130, 140))
            screen.blit(empty, empty.get_rect(center=g.center))

        # JSON 내보내기 버튼
        pygame.draw.rect(screen, (30, 28, 26), self.export_rect, border_radius=4)
        pygame.draw.rect(screen, layout.KV_ORANGE_DIM, self.export_rect,
                         width=1, border_radius=4)
        label = self.font_small.render("JSON 내보내기", True, layout.KV_ORANGE)
        screen.blit(label, label.get_rect(center=self.export_rect.center))

        if self.notice and time.time() < self.notice_until:
            note = self.font_small.render(self.notice, True,
                                          layout.STATUS_COLORS["success"])
            screen.blit(note, (self.rect.x + 16, self.export_rect.bottom + 10))


class UIApp:
    def __init__(self, runtime, smoke_frames: int = 0):
        self.runtime = runtime  # q_in / q_out만 사용 (연결점 2개 규칙)
        self.smoke_frames = smoke_frames
        pygame.init()
        pygame.display.set_caption("멀티 에이전트 시스템")
        # 키비쥬얼을 창 아이콘 + 맵 로고로 사용 (없으면 생략)
        self.logo: pygame.Surface | None = None
        try:
            kv = pygame.image.load("키비쥬얼.png")
            pygame.display.set_icon(kv)
            self.logo = pygame.transform.smoothscale(kv, (40, 40))
        except (pygame.error, FileNotFoundError):
            pass
        self.screen = pygame.display.set_mode((layout.WINDOW_W, layout.WINDOW_H))
        self.clock = pygame.time.Clock()

        self.font_log = load_korean_font(14)
        self.font_label = load_korean_font(12)
        self.font_bubble = load_korean_font(13)
        self.font_input = load_korean_font(18)
        self.font_zone = load_korean_font(15)

        self.actors = {name: Actor(name, home)
                       for name, home in layout.AGENT_HOMES.items()}
        self.log = LogView(layout.LOG_RECT, self.font_log)
        self.input = InputBox(layout.INPUT_RECT, self.font_input)
        self.banner_text: str | None = None
        self.banner_until = 0.0

        # 토큰 속도 그래프 패널 (우상단 버튼 토글 → 창이 오른쪽으로 확장)
        self.panel_open = False
        self.speed_panel = SpeedPanel(layout.PANEL_RECT, self.font_zone, self.font_log)
        self._panel_arrows = (("◀", "▶") if self._font_has(self.font_label, "▶")
                              else ("<", ">"))

        # 개발팀 설정 모드 (F2): 로그 패널 텍스트 메뉴로 model/effort/mode 변경
        self.settings_mode = False

        # 에이전트별 활성 task_id 집합 (요구 2: running 추가 / result·error 제거)
        self.active_tasks: dict[str, set[str]] = {}
        # 태스크 상태 바 (요구 4·5): 종결 = orchestrator → user 최종 result/error
        self.task = self._idle_task()
        self.font_task = load_korean_font(14)
        self._task_icons = {
            key: emoji if self._font_has(self.font_task, emoji) else fallback
            for key, emoji, fallback in (
                ("active", "⏳", "[~]"), ("done", "✅", "[OK]"), ("failed", "❌", "[X]"))
        }

    @staticmethod
    def _idle_task() -> dict:
        return {"id": None, "phase": "idle", "agent": "", "until": 0.0, "msg": ""}

    @staticmethod
    def _font_has(font: pygame.font.Font, ch: str) -> bool:
        # 글리프 없으면 tofu 박스가 되므로 ASCII 대체 표기 사용
        try:
            metrics = font.metrics(ch)
        except Exception:
            return False
        return bool(metrics) and metrics[0] is not None

    # --- envelope → 로그 + 애니메이션 (요구사항 2·3) ---

    def apply_envelope(self, env: dict):
        sender, to = env.get("from", "?"), env.get("to", "?")
        msg_type, status = env.get("type", "?"), env.get("status", "?")
        payload = env.get("payload", {})
        task_id = env.get("task_id")

        # 요구 2: 에이전트별 활성 task_id 집합 갱신
        if msg_type == "status" and status == "running":
            self.active_tasks.setdefault(sender, set()).add(task_id)
        elif msg_type in ("result", "error"):
            self.active_tasks.get(sender, set()).discard(task_id)
            if to == "user":  # 최종 종결(요구 4) → 잔여 활성 표시 안전망 정리
                for ids in self.active_tasks.values():
                    ids.discard(task_id)
        self._update_task_state(env)

        color = layout.STATUS_COLORS.get(status, layout.DEFAULT_TEXT)
        if msg_type == "request":
            color = layout.DEFAULT_TEXT
        stamp = time.strftime("%H:%M:%S")
        self.log.add(f"[{stamp}] {sender}→{to} {msg_type}/{status}: "
                     f"{summarize_payload(payload)}", color)

        actor = self.actors.get(sender)
        if msg_type == "approval_request":
            # pm_dev가 user에게 모델 변경 승인 요청: user 쪽으로 걸어가서 "?" 말풍선
            user_actor = self.actors.get("user")
            if actor and user_actor:
                actor.walk_to(tuple(user_actor.home))
                actor.say("?", (255, 211, 80), ttl=5.0)
            p = payload if isinstance(payload, dict) else {}
            self.log.add(f"[승인 요청] {p.get('default_model')} → {p.get('proposed_model')} "
                         f"({p.get('reason')}). y/n 입력",
                         layout.STATUS_COLORS["running"])
        elif msg_type == "request":
            target = self.actors.get(to)
            if actor and target and actor is not target:
                actor.walk_to(tuple(target.home))
        elif msg_type == "status" and status == "running":
            if isinstance(payload, dict) and payload.get("phase") == "gpu_switch":
                # 리스크 6: Ollama↔ComfyUI 전환 배너
                self.banner_text = str(payload.get("detail", "모델 전환 중"))
                self.banner_until = time.time() + BANNER_TTL
            elif actor:
                actor.say("...", (60, 60, 70))
        elif msg_type == "result" and actor:
            actor.bounce()
            actor.say("✓", (40, 160, 70), ttl=2.0)
        elif msg_type == "error" and actor:
            actor.bounce()
            actor.say("✗", (200, 60, 60), ttl=2.0)

    def _update_task_state(self, env: dict):
        # 태스크 상태 바 갱신 (요구 4·5). 한 번에 한 태스크만 추적.
        task_id, msg_type, status = env.get("task_id"), env.get("type"), env.get("status")
        if self.task["phase"] == "done" and time.time() >= self.task["until"]:
            self.task = self._idle_task()

        if task_id != self.task["id"]:
            # 진행 중이거나 실패 표시(수동 확인 대기) 중에는 다른 태스크로 전환하지 않음
            if self.task["phase"] in ("active", "failed"):
                return
            if msg_type == "request" or (msg_type == "status" and status == "running"):
                self.task = {"id": task_id, "phase": "active",
                             "agent": env.get("from", "?"), "until": 0.0, "msg": ""}
            return

        if msg_type == "status" and status == "running":
            self.task["agent"] = env.get("from", "?")
        elif env.get("to") == "user" and msg_type == "result":
            # 요구 4: orchestrator가 user로 최종 result 반환 = 완료
            self.task.update(phase="done", until=time.time() + TASK_DONE_TTL)
        elif env.get("to") == "user" and msg_type == "error":
            payload = env.get("payload", {})
            parts = [status]
            if isinstance(payload, dict):
                if payload.get("reason"):
                    parts.append(str(payload["reason"]))
                if payload.get("attempts"):
                    parts.append(f"재시도 {payload['attempts']}회 소진")
            self.task.update(phase="failed", msg=", ".join(parts))

    def _toggle_panel(self):
        # 패널 열기 = 창을 오른쪽으로 PANEL_W만큼 확장 (기존 레이아웃 좌표 불변)
        self.panel_open = not self.panel_open
        w = layout.WINDOW_W + (layout.PANEL_W if self.panel_open else 0)
        self.screen = pygame.display.set_mode((w, layout.WINDOW_H))

    def _export_metrics(self):
        try:
            path = token_metrics.export_json()
        except OSError as exc:
            self.speed_panel.set_notice(f"저장 실패: {exc}")
            return
        self.speed_panel.set_notice(f"저장됨: {path}")
        self.log.add(f"[{time.strftime('%H:%M:%S')}] 토큰 속도 데이터 내보내기 → {path}",
                     layout.STATUS_COLORS["success"])

    def draw_graph_button(self):
        rect = layout.GRAPH_BTN_RECT
        pygame.draw.rect(self.screen, (30, 28, 26), rect, border_radius=4)
        pygame.draw.rect(self.screen, layout.KV_ORANGE_DIM, rect, width=1, border_radius=4)
        close_arrow, open_arrow = self._panel_arrows
        text = f"{close_arrow} 속도" if self.panel_open else f"속도 {open_arrow}"
        label = self.font_label.render(text, True, layout.KV_ORANGE)
        self.screen.blit(label, label.get_rect(center=rect.center))

    def _ack_task_failure(self):
        # 요구 5: 실패 표시는 수동 확인(상태 바 클릭) 시 해제
        if self.task["phase"] == "failed":
            self.task = self._idle_task()

    # --- 개발팀 설정 패널 (F2 + 로그 패널 텍스트 메뉴) ---

    def _toggle_settings_mode(self):
        self.settings_mode = not self.settings_mode
        if self.settings_mode:
            from teams.dev import settings as dev_settings
            s = dev_settings.load_settings()
            self.log.add(f"[설정] 현재: model {s['default_model']} / "
                         f"effort {s['default_effort']} / mode {s['autonomy_mode']}",
                         layout.KV_ORANGE)
            self.log.add("[설정] 변경: \"model <이름> / effort <값> / "
                         "mode <auto|approval|manual>\" 형식 입력 (F2: 취소)",
                         layout.KV_ORANGE)
        else:
            self.log.add("[설정] 설정 모드 종료", layout.KV_ORANGE)

    def _submit_settings(self, text: str):
        # "model sonnet / effort high / mode auto" 형식 파싱 → settings_update 송신
        keymap = {"model": "default_model", "effort": "default_effort",
                  "mode": "autonomy_mode"}
        tokens = text.replace("/", " ").split()
        payload = {}
        for i in range(len(tokens) - 1):
            if tokens[i] in keymap:
                payload[keymap[tokens[i]]] = tokens[i + 1]
        if not payload:
            self.log.add("[설정] 형식 인식 실패. 예: model sonnet / effort high / mode auto",
                         layout.STATUS_COLORS["failed"])
            return
        self.runtime.q_in.put(make_envelope(
            new_task_id(), "user", "pm_dev", "settings_update", "pending", payload))
        self.log.add(f"[설정] 변경 요청 전송: {payload}", layout.KV_ORANGE)
        self.settings_mode = False

    def submit_user_input(self, text: str):
        task_id = new_task_id()
        self.runtime.q_in.put(make_envelope(
            task_id, "user", "orchestrator", "request", "pending", {"text": text}))
        # 새 요청 제출 = 이전 실패 표시 확인으로 간주하고 새 태스크 추적 시작
        self.task = {"id": task_id, "phase": "active",
                     "agent": "orchestrator", "until": 0.0, "msg": ""}
        # q_in으로 보낸 요청은 q_out으로 echo되지 않으므로 로그/애니메이션 직접 처리
        stamp = time.strftime("%H:%M:%S")
        self.log.add(f"[{stamp}] user→orchestrator request/pending: {text}",
                     (235, 235, 240))
        self.actors["user"].walk_to(tuple(self.actors["orchestrator"].home))

    # --- 렌더링 ---

    def draw_map(self):
        # 공용 바닥 타일
        for ty in range(layout.MAP_RECT.top, layout.MAP_RECT.bottom, layout.TILE):
            for tx in range(0, layout.WINDOW_W, layout.TILE):
                even = ((tx // layout.TILE) + (ty // layout.TILE)) % 2 == 0
                color = layout.FLOOR_LIGHT if even else layout.FLOOR_DARK
                pygame.draw.rect(self.screen, color, (tx, ty, layout.TILE, layout.TILE))
        # 팀 구역 타일 (배경색으로 구분)
        for _, (rect, light, dark, label) in layout.ZONES.items():
            for ty in range(rect.top, rect.bottom, layout.TILE):
                for tx in range(rect.left, rect.right, layout.TILE):
                    even = ((tx // layout.TILE) + (ty // layout.TILE)) % 2 == 0
                    tile = pygame.Rect(tx, ty, layout.TILE, layout.TILE).clip(rect)
                    pygame.draw.rect(self.screen, light if even else dark, tile)
            pygame.draw.rect(self.screen, layout.KV_ORANGE_DARK, rect, width=1)
            tag = self.font_zone.render(label, True, (235, 235, 240))
            self.screen.blit(tag, (rect.x + 8, rect.y + 6))

        # y좌표 순으로 그려 겹침 시 아래쪽 캐릭터가 앞에 오게
        for actor in sorted(self.actors.values(), key=lambda a: a.pos.y):
            actor.draw(self.screen, self.font_label, self.font_bubble,
                       layout.AGENT_LABELS.get(actor.name, actor.name))

        # 키비쥬얼 로고 + 타이틀 (상단 좌측 여백)
        if self.logo:
            self.screen.blit(self.logo, (12, 5))
            title = self.font_zone.render("멀티 에이전트 시스템", True, layout.KV_ORANGE)
            self.screen.blit(title, (60, 5 + (40 - title.get_height()) // 2))

        # 모델 전환 배너 (리스크 6) — 태스크 상태 바와 별도 줄 (요구 6: 화면 최상단)
        if self.banner_text and time.time() < self.banner_until:
            label = self.font_zone.render("⚙ " + self.banner_text, True, (40, 40, 30))
            bar = pygame.Rect(0, 0, layout.WINDOW_W, label.get_height() + 12)
            pygame.draw.rect(self.screen, (255, 211, 80), bar)
            self.screen.blit(label, label.get_rect(center=bar.center))

    def draw_taskbar(self):
        # 요구 5: 로그 위 한 줄 태스크 상태 바
        if self.task["phase"] == "done" and time.time() >= self.task["until"]:
            self.task = self._idle_task()
        rect = layout.TASKBAR_RECT
        pygame.draw.rect(self.screen, (30, 28, 26), rect)
        pygame.draw.line(self.screen, layout.KV_ORANGE_DARK, rect.topleft, rect.topright)

        phase = self.task["phase"]
        if phase == "active":
            text = f"{self._task_icons['active']} TASK 진행 중 — 현재: {self.task['agent']} 작업"
            color = layout.STATUS_COLORS["running"]
        elif phase == "done":
            text = f"{self._task_icons['done']} TASK 완료"
            color = layout.STATUS_COLORS["success"]
        elif phase == "failed":
            text = f"{self._task_icons['failed']} TASK 실패 ({self.task['msg']}) — 클릭하여 확인"
            color = layout.STATUS_COLORS["failed"]
        else:
            text, color = "TASK 없음 (대기)", (130, 130, 140)
        label = self.font_task.render(text, True, color)
        self.screen.blit(label, (rect.x + 10, rect.y + (rect.h - label.get_height()) // 2))

    def run(self):
        frame = 0
        running = True
        while running:
            dt = self.clock.tick(layout.FPS) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEWHEEL:
                    self.log.on_wheel(event.y)
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and layout.GRAPH_BTN_RECT.collidepoint(event.pos)):
                    self._toggle_panel()
                elif (event.type == pygame.MOUSEBUTTONDOWN and self.panel_open
                      and self.speed_panel.clicked_export(event.pos)):
                    self._export_metrics()
                elif (event.type == pygame.MOUSEBUTTONDOWN
                      and layout.TASKBAR_RECT.collidepoint(event.pos)):
                    self._ack_task_failure()  # 실패 표시 수동 확인 (요구 5)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F2:
                    self._toggle_settings_mode()  # 개발팀 모델/effort 설정 패널
                else:
                    submitted = self.input.handle_event(event)
                    if submitted:
                        if self.settings_mode:
                            self._submit_settings(submitted)
                        else:
                            self.submit_user_input(submitted)

            # §6: 매 프레임 non-blocking 수신
            while True:
                try:
                    env = self.runtime.q_out.get_nowait()
                except queue.Empty:
                    break
                self.apply_envelope(env)

            for name, actor in self.actors.items():
                actor.active = bool(self.active_tasks.get(name))  # 요구 3
                actor.update(dt)

            self.screen.fill(layout.KV_BG)
            self.draw_map()
            self.draw_taskbar()
            self.log.draw(self.screen)
            self.input.draw(self.screen)
            self.draw_graph_button()
            if self.panel_open:
                self.speed_panel.draw(self.screen)
            pygame.display.flip()

            frame += 1
            if self.smoke_frames and frame >= self.smoke_frames:
                running = False
        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="멀티 에이전트 Pygame UI")
    parser.add_argument("--mock", action="store_true",
                        help="백엔드 없이 mock envelope로 UI 단독 테스트")
    parser.add_argument("--smoke", type=int, default=0, metavar="N",
                        help="N프레임 후 자동 종료 (렌더링 스모크 테스트용)")
    args = parser.parse_args()

    if args.mock:
        from ui.mock_backend import MockRuntime
        runtime = MockRuntime()
    else:
        from runtime import AgentRuntime
        runtime = AgentRuntime()

    runtime.start()
    try:
        UIApp(runtime, smoke_frames=args.smoke).run()
    finally:
        runtime.stop()


if __name__ == "__main__":
    sys.exit(main())
