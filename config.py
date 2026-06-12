# config.py — 전역 설정

import os
import shutil

# Ollama 로컬 LLM 설정
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:12b-it-q4_K_M"
OLLAMA_TIMEOUT = 120  # 분류 호출 타임아웃 (초) — RAM 오프로딩 시 느린 응답 감안
# RTX 4060 8GB에 gemma 12b q4 전체(49층) 적재 불가 → 44층 GPU + 나머지 RAM 오프로딩.
# 모든 /api/generate 호출이 같은 값을 보내야 모델 재로드가 반복되지 않는다.
OLLAMA_NUM_GPU = 44

# 에이전트별 모델 분리: 분류(orchestrator/pm_assistant)는 경량 모델, brain은 12B 유지
# 주의: ollama의 "qwen3:4b" 태그는 thinking 변형(분류에 20s+ 사고 토큰 낭비) →
# 비-thinking instruct 변형을 사용한다.
OLLAMA_MODEL_FAST = "qwen3:4b-instruct"
AGENT_MODELS = {
    "orchestrator": OLLAMA_MODEL_FAST,
    "pm_assistant": OLLAMA_MODEL_FAST,
    "brain": OLLAMA_MODEL,
}
# keep_alive: 경량 모델은 영구 상주(-1), 12B는 10분
OLLAMA_KEEP_ALIVE_BY_MODEL = {
    OLLAMA_MODEL_FAST: -1,
    OLLAMA_MODEL: "10m",
}

# 오케스트레이터 fast-path: 인사말이면 LLM 분류 없이 즉시 응답 (구두점 제거 후 비교)
GREETING_WORDS = {"안녕", "안녕하세요", "하이", "ㅎㅇ", "헬로", "hello", "hi", "hey"}
GREETING_REPLY = "안녕하세요! 무엇을 도와드릴까요? (dev / personal / comfyui)"

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

# 4단계: 개인 비서팀 세컨드 브레인 / 일정 파일 경로 (경로는 config로 분리)
_BASE = os.path.dirname(os.path.abspath(__file__))
SECOND_BRAIN_ROOT = os.path.join(_BASE, "second_brain")
BRAIN_SUMMARY_JSON = os.path.join(SECOND_BRAIN_ROOT, "ai-index", "summary.json")
BRAIN_MD = os.path.join(SECOND_BRAIN_ROOT, "SECOND_BRAIN.md")
SCHEDULE_JSON = os.path.join(_BASE, "schedule.json")

# 리스크 4: 파일 수정 24시간 이상 경과 시 payload에 stale 경고
STALE_AFTER_SECONDS = 24 * 3600

# 비서팀 PM 내부 분배 키워드 (브레인 vs 스케줄, rule-based 1차)
ASSISTANT_BRAIN_KEYWORDS = ["정리한", "정리해", "메모", "요약", "브레인", "찾아",
                            "관련해서", "관련된", "정보", "노트", "기록", "어디"]
ASSISTANT_SCHEDULE_KEYWORDS = ["일정", "할일", "할 일", "회의", "약속", "추가", "등록",
                               "삭제", "제거", "수정", "변경", "오늘", "내일", "모레",
                               "스케줄", "todo", "잡아", "넣어"]

# 5단계: ComfyUI REST API 연동 (리스크 3·6)
COMFYUI_BASE_URL = "http://localhost:8188"
COMFYUI_WORKFLOW = os.path.join(_BASE, "workflows", "lola_base.json")
COMFYUI_POLL_INTERVAL = 2        # /history 폴링 간격 (초)
COMFYUI_POLL_TIMEOUT = 600       # 최대 대기 10분
COMFYUI_HEALTH_TIMEOUT = 2       # health check (GET /system_stats) 타임아웃
COMFYUI_HTTP_TIMEOUT = 30        # /prompt·/history 개별 호출 타임아웃
COMFYUI_DEFAULT_WIDTH = 768
COMFYUI_DEFAULT_HEIGHT = 768
# lola 그림체 스타일 — 템플릿의 {{PROMPT}}/{{NEGATIVE}} 치환에 사용 (노드 내용은 workflows/README 참고)
COMFYUI_STYLE_PREFIX = "lola style, "
COMFYUI_NEGATIVE = "lowres, bad anatomy, worst quality, low quality, jpeg artifacts"

# 리스크 6: Ollama ↔ ComfyUI VRAM 경합 → 순차 전환용 Ollama 제어
OLLAMA_KEEP_ALIVE = OLLAMA_KEEP_ALIVE_BY_MODEL[OLLAMA_MODEL]  # 12B 재로드 시 유지 시간
OLLAMA_UNLOAD_POLL_INTERVAL = 1      # /api/ps 로 언로드 확인 폴링 간격 (초)
OLLAMA_UNLOAD_POLL_MAX = 15          # 언로드 확인 최대 시도 횟수
