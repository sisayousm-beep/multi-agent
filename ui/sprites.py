# ui/sprites.py — 절차적 도트 캐릭터 생성 (§8, 요구사항 4)
#
# 외부 이미지 파일 없이 pygame.Surface에 픽셀을 직접 찍어 16x16 캐릭터를 만든다.
# 나중에 실제 스프라이트 이미지로 교체할 때는 get_frames()만 파일 로드 버전으로
# 바꾸면 된다 (반환 형식: {"idle": [Surface], "walk": [Surface, Surface]}).

import pygame

SPRITE_SIZE = 16
SCALE = 4  # 16x16 → 64x64 픽셀 확대 (nearest, 도트 유지)

# 에이전트별 몸통 색 (시각 구분)
AGENT_COLORS = {
    "orchestrator": (240, 128, 80),   # 키비쥬얼 오렌지 (마스코트)
    "user": (210, 210, 215),          # 흰색/회색
    "pm_assistant": (95, 200, 120),   # 초록
    "brain": (170, 120, 230),         # 보라
    "schedule": (90, 200, 220),       # 시안
    "pm_dev": (95, 140, 235),         # 파랑
    "claude_code": (235, 140, 70),    # 주황
    "codex": (70, 180, 165),          # 청록
    "comfyui": (235, 120, 170),       # 분홍
}

# 픽셀 맵 문자: . 투명 / O 외곽선 / B 몸통색 / D 몸통 어두운색 / S 피부 / E 눈
_HEAD = [
    "....OOOOOOOO....",
    "...OBBBBBBBBO...",
    "..OBBBBBBBBBBO..",
    "..OBBBBBBBBBBO..",
    "..OSSSSSSSSSSO..",
    "..OSESSSSSSESO..",
    "..OSSSSSSSSSSO..",
    "...OSSSSSSSSO...",
    "..OBBBBBBBBBBO..",
    ".OBDBBBBBBBBDBO.",
    ".OBBBBBBBBBBBBO.",
    "..OBBBBBBBBBBO..",
]
_LEGS_IDLE = [
    "...ODDDDDDDDO...",
    "...ODD....DDO...",
    "...ODD....DDO...",
    "...OO......OO...",
]
_LEGS_WALK1 = [
    "...ODDDDDDDDO...",
    "..ODD.....DDO...",
    ".ODD......DDO...",
    ".OO........OO...",
]
_LEGS_WALK2 = [
    "...ODDDDDDDDO...",
    "...ODD.....DDO..",
    "...ODD......DDO.",
    "...OO........OO.",
]

_OUTLINE = (25, 25, 30)
_SKIN = (245, 220, 185)
_EYE = (30, 30, 40)


def _darken(color, factor=0.6):
    return tuple(int(c * factor) for c in color)


def _build(pixel_rows, body_color):
    surf = pygame.Surface((SPRITE_SIZE, SPRITE_SIZE), pygame.SRCALPHA)
    palette = {
        "O": _OUTLINE,
        "B": body_color,
        "D": _darken(body_color),
        "S": _SKIN,
        "E": _EYE,
    }
    for y, row in enumerate(pixel_rows):
        for x, ch in enumerate(row):
            if ch != ".":
                surf.set_at((x, y), palette[ch])
    return pygame.transform.scale(
        surf, (SPRITE_SIZE * SCALE, SPRITE_SIZE * SCALE)
    )


def get_frames(agent_name: str) -> dict:
    """에이전트 이름 → {"idle": [Surface], "walk": [Surface, Surface]}.

    실제 스프라이트 이미지로 교체 시 이 함수만 pygame.image.load 버전으로 바꾸면 됨.
    """
    body = AGENT_COLORS.get(agent_name, (150, 150, 150))
    return {
        "idle": [_build(_HEAD + _LEGS_IDLE, body)],
        "walk": [_build(_HEAD + _LEGS_WALK1, body),
                 _build(_HEAD + _LEGS_WALK2, body)],
    }


def make_bubble(text: str, font: pygame.font.Font, color=(40, 40, 50)) -> pygame.Surface:
    """말풍선 Surface (둥근 사각형 + 꼬리). text 색상은 color."""
    label = font.render(text, True, color)
    pad = 6
    w, h = label.get_width() + pad * 2, label.get_height() + pad * 2
    bubble = pygame.Surface((w, h + 6), pygame.SRCALPHA)
    pygame.draw.rect(bubble, (250, 250, 250), (0, 0, w, h), border_radius=6)
    pygame.draw.rect(bubble, (90, 90, 100), (0, 0, w, h), width=1, border_radius=6)
    # 꼬리
    pygame.draw.polygon(bubble, (250, 250, 250),
                        [(w // 2 - 5, h - 1), (w // 2 + 5, h - 1), (w // 2, h + 5)])
    bubble.blit(label, (pad, pad))
    return bubble
