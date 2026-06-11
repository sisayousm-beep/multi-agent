# ui/layout.py — 화면 레이아웃 상수: 영역 분할, 팀 구역, 에이전트 홈 좌표 (§8)
#
# 모든 좌표는 960x720 고정 윈도우 기준. 맵(상단) / 로그(하단) / 입력창(최하단).

import pygame

WINDOW_W, WINDOW_H = 960, 720
FPS = 60

# 키비쥬얼.png 기반 팔레트 (다크 배경 + 오렌지 로봇)
KV_BG = (23, 23, 23)            # 키비쥬얼 배경색
KV_ORANGE = (240, 128, 80)      # 메인 오렌지 (마스코트 몸통)
KV_ORANGE_DIM = (150, 75, 50)   # 보조 오렌지 (테두리)
KV_ORANGE_DARK = (96, 48, 32)   # 어두운 오렌지 (구분선)

MAP_RECT = pygame.Rect(0, 0, WINDOW_W, 430)
LOG_RECT = pygame.Rect(0, 430, WINDOW_W, 230)
INPUT_RECT = pygame.Rect(0, 660, WINDOW_W, 60)

# 팀 구역: 배경 타일 색상으로 비서팀/개발팀/ComfyUI를 시각 구분 (§8)
# (rect, 타일 밝은색, 타일 어두운색, 라벨)
ZONES = {
    "personal": (pygame.Rect(20, 50, 290, 260), (46, 74, 52), (40, 66, 46), "비서팀"),
    "dev": (pygame.Rect(340, 50, 290, 260), (44, 58, 86), (38, 52, 78), "개발팀"),
    "comfyui": (pygame.Rect(660, 50, 280, 260), (82, 48, 70), (74, 42, 63), "ComfyUI"),
}
FLOOR_LIGHT = (34, 32, 30)   # 구역 밖 공용 바닥 타일 (키비쥬얼 다크 톤)
FLOOR_DARK = (28, 27, 26)
TILE = 32

# 에이전트 홈 좌표 (스프라이트 중심점). envelope의 from/to 이름과 1:1 대응.
AGENT_HOMES = {
    "orchestrator": (480, 365),
    "user": (130, 365),
    "pm_assistant": (90, 110),
    "brain": (230, 160),
    "schedule": (120, 245),
    "pm_dev": (410, 110),
    "claude_code": (550, 160),
    "codex": (440, 245),
    "comfyui": (800, 165),
}

# 한글 라벨 (캐릭터 아래 이름표)
AGENT_LABELS = {
    "orchestrator": "오케스트레이터",
    "user": "사용자",
    "pm_assistant": "비서PM",
    "brain": "브레인",
    "schedule": "스케줄",
    "pm_dev": "개발PM",
    "claude_code": "Claude Code",
    "codex": "Codex",
    "comfyui": "ComfyUI",
}

# status별 로그/말풍선 색상 (요구사항: running=노랑, success=초록, failed/timeout=빨강)
STATUS_COLORS = {
    "running": (255, 211, 80),
    "success": (110, 220, 120),
    "failed": (240, 95, 95),
    "timeout": (240, 95, 95),
    "pending": (170, 170, 180),
}
DEFAULT_TEXT = (200, 200, 210)
