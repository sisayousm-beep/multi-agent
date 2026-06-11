# config.py — 전역 설정

import os
import shutil

# Ollama 로컬 LLM 설정
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:12b-it-q4_K_M"
OLLAMA_TIMEOUT = 120  # 분류 호출 타임아웃 (초) — RAM 오프로딩 시 느린 응답 감안

# 재시도 설정 (Ollama 호출 / subprocess 공용)
MAX_RETRIES = 3

# §7 subprocess 실행 정책
IDLE_TIMEOUT = 90        # stdout/stderr 무출력 90초 지속 시 kill (출력 있으면 리셋)
ABSOLUTE_TIMEOUT = 900   # 절대 상한 15분

# CLI 실행 파일 경로 (Windows에서 .cmd 셔임을 정확히 잡기 위해 which로 해석)
CLAUDE_CMD = shutil.which("claude") or "claude"
CODEX_CMD = shutil.which("codex") or "codex"

# 태스크별 작업 디렉토리 루트 (리스크 7: cwd 명시 실행)
WORKSPACE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces")

# 팀 라우팅 키워드 (1차 규칙 기반 분류에 사용)
TEAM_KEYWORDS = {
    "dev": ["코드", "개발", "버그", "함수", "스크립트", "파일 만들어", "구현", "codex"],
    "personal": ["일정", "할일", "브레인", "요약", "메모", "정보 찾아"],
    "comfyui": ["이미지", "그림", "그려", "lola", "comfyui"],
}
