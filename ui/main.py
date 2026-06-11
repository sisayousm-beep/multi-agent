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

from messages import make_envelope, new_task_id
from ui import layout
from ui.actors import Actor

BANNER_TTL = 3.0          # gpu_switch status 수신 후 배너 유지 시간 (초)
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
        pygame.draw.rect(screen, (18, 18, 22), self.rect)
        pygame.draw.line(screen, (70, 70, 80), self.rect.topleft, self.rect.topright)
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
        pygame.draw.rect(screen, (28, 28, 34), self.rect)
        pygame.draw.line(screen, (70, 70, 80), self.rect.topleft, self.rect.topright)
        box = self.rect.inflate(-20, -24)
        pygame.draw.rect(screen, (15, 15, 18), box, border_radius=4)
        pygame.draw.rect(screen, (90, 90, 105), box, width=1, border_radius=4)

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
            pygame.draw.line(screen, (230, 230, 235),
                             (x + 2, box.y + 8), (x + 2, box.bottom - 8))


class UIApp:
    def __init__(self, runtime, smoke_frames: int = 0):
        self.runtime = runtime  # q_in / q_out만 사용 (연결점 2개 규칙)
        self.smoke_frames = smoke_frames
        pygame.init()
        pygame.display.set_caption("멀티 에이전트 시스템")
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

    # --- envelope → 로그 + 애니메이션 (요구사항 2·3) ---

    def apply_envelope(self, env: dict):
        sender, to = env.get("from", "?"), env.get("to", "?")
        msg_type, status = env.get("type", "?"), env.get("status", "?")
        payload = env.get("payload", {})

        color = layout.STATUS_COLORS.get(status, layout.DEFAULT_TEXT)
        if msg_type == "request":
            color = layout.DEFAULT_TEXT
        stamp = time.strftime("%H:%M:%S")
        self.log.add(f"[{stamp}] {sender}→{to} {msg_type}/{status}: "
                     f"{summarize_payload(payload)}", color)

        actor = self.actors.get(sender)
        if msg_type == "request":
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

    def submit_user_input(self, text: str):
        task_id = new_task_id()
        self.runtime.q_in.put(make_envelope(
            task_id, "user", "orchestrator", "request", "pending", {"text": text}))
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
            pygame.draw.rect(self.screen, (110, 110, 125), rect, width=1)
            tag = self.font_zone.render(label, True, (235, 235, 240))
            self.screen.blit(tag, (rect.x + 8, rect.y + 6))

        # y좌표 순으로 그려 겹침 시 아래쪽 캐릭터가 앞에 오게
        for actor in sorted(self.actors.values(), key=lambda a: a.pos.y):
            actor.draw(self.screen, self.font_label, self.font_bubble,
                       layout.AGENT_LABELS.get(actor.name, actor.name))

        # 모델 전환 배너 (리스크 6)
        if self.banner_text and time.time() < self.banner_until:
            label = self.font_zone.render("⚙ " + self.banner_text, True, (40, 40, 30))
            bar = pygame.Rect(0, 0, layout.WINDOW_W, label.get_height() + 12)
            pygame.draw.rect(self.screen, (255, 211, 80), bar)
            self.screen.blit(label, label.get_rect(center=bar.center))

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
                else:
                    submitted = self.input.handle_event(event)
                    if submitted:
                        self.submit_user_input(submitted)

            # §6: 매 프레임 non-blocking 수신
            while True:
                try:
                    env = self.runtime.q_out.get_nowait()
                except queue.Empty:
                    break
                self.apply_envelope(env)

            for actor in self.actors.values():
                actor.update(dt)

            self.screen.fill((10, 10, 12))
            self.draw_map()
            self.log.draw(self.screen)
            self.input.draw(self.screen)
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
