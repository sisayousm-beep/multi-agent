# ui/actors.py — 캐릭터 애니메이션 상태 머신 (요구사항 3)
#
# 상태: IDLE → WALK_OUT(상대 쪽으로 걷기) → PAUSE(잠깐 정지) → WALK_BACK(복귀) → IDLE
# 말풍선은 걷기와 독립 채널: say()로 텍스트+TTL 설정, 만료 시 소멸.
# result/error 도착 연출은 bounce(): 0.35초 감쇠 점프.

import math

import pygame

from ui import sprites

WALK_SPEED = 180.0       # px/s
PAUSE_SECONDS = 0.5      # 상대 앞 정지 시간
APPROACH_GAP = 52.0      # 상대 중심에서 이만큼 떨어진 지점까지만 접근
WALK_FPS = 6.0           # 걷기 프레임 전환 속도
BOUNCE_SECONDS = 0.35


class Actor:
    def __init__(self, name: str, home: tuple):
        self.name = name
        self.home = pygame.Vector2(home)
        self.pos = pygame.Vector2(home)
        self.frames = sprites.get_frames(name)
        self.state = "IDLE"          # IDLE | WALK_OUT | PAUSE | WALK_BACK
        self.target = pygame.Vector2(home)
        self.pause_left = 0.0
        self.anim_t = 0.0
        self.facing_left = False
        # 말풍선: (text, color, 남은 시간)
        self.bubble = None
        self.bounce_left = 0.0

    # --- envelope 트리거 ---

    def walk_to(self, dest: tuple):
        """type=request: dest(상대 홈) 쪽으로 걸어갔다가 복귀."""
        dest = pygame.Vector2(dest)
        direction = dest - self.pos
        if direction.length() <= APPROACH_GAP:
            return
        self.target = dest - direction.normalize() * APPROACH_GAP
        self.state = "WALK_OUT"

    def say(self, text: str, color=(40, 40, 50), ttl: float = 2.5):
        self.bubble = (text, color, ttl)

    def bounce(self):
        self.bounce_left = BOUNCE_SECONDS

    # --- 프레임 갱신 ---

    def update(self, dt: float):
        self.anim_t += dt
        if self.bounce_left > 0:
            self.bounce_left = max(0.0, self.bounce_left - dt)
        if self.bubble:
            text, color, ttl = self.bubble
            ttl -= dt
            self.bubble = (text, color, ttl) if ttl > 0 else None

        if self.state in ("WALK_OUT", "WALK_BACK"):
            dest = self.target if self.state == "WALK_OUT" else self.home
            delta = dest - self.pos
            step = WALK_SPEED * dt
            if delta.length() <= step:
                self.pos = pygame.Vector2(dest)
                if self.state == "WALK_OUT":
                    self.state = "PAUSE"
                    self.pause_left = PAUSE_SECONDS
                else:
                    self.state = "IDLE"
            else:
                move = delta.normalize() * step
                self.pos += move
                self.facing_left = move.x < 0
        elif self.state == "PAUSE":
            self.pause_left -= dt
            if self.pause_left <= 0:
                self.state = "WALK_BACK"

    # --- 렌더링 ---

    def _current_frame(self) -> pygame.Surface:
        if self.state in ("WALK_OUT", "WALK_BACK"):
            frames = self.frames["walk"]
            frame = frames[int(self.anim_t * WALK_FPS) % len(frames)]
        else:
            frame = self.frames["idle"][0]
            # IDLE 시 살짝 숨쉬기 효과는 y 오프셋으로 처리 (draw에서)
        if self.facing_left:
            frame = pygame.transform.flip(frame, True, False)
        return frame

    def draw(self, screen: pygame.Surface, label_font: pygame.font.Font,
             bubble_font: pygame.font.Font, label: str):
        frame = self._current_frame()
        bob = math.sin(self.anim_t * 2.0) * 1.5 if self.state == "IDLE" else 0.0
        bounce = 0.0
        if self.bounce_left > 0:
            # 감쇠 점프: 남은 시간 비율로 sin 반주기
            t = 1.0 - self.bounce_left / BOUNCE_SECONDS
            bounce = -10.0 * math.sin(t * math.pi)
        rect = frame.get_rect(center=(self.pos.x, self.pos.y + bob + bounce))
        screen.blit(frame, rect)

        name_label = label_font.render(label, True, (225, 225, 230))
        screen.blit(name_label, name_label.get_rect(midtop=(self.pos.x, rect.bottom - 2)))

        if self.bubble:
            text, color, _ = self.bubble
            bubble = sprites.make_bubble(text, bubble_font, color)
            screen.blit(bubble, bubble.get_rect(midbottom=(self.pos.x, rect.top - 2)))
