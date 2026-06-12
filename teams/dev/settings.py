# teams/dev/settings.py — 개발팀 모델/effort/자율 모드 설정 (dev_settings.json 영속화)
#
# ── 0번 사전 조사 결과 (조사일: 2026-06-12, 설치된 CLI --help 직접 확인) ──
# Claude Code CLI 2.1.175 (`claude --help`):
#   --model <model>   별칭('fable', 'opus', 'sonnet') 또는 전체 이름('claude-fable-5' 등)
#   --effort <level>  low | medium | high | xhigh | max   (help 출력에서 직접 확인)
#   ※ 'haiku' 별칭은 --help 예시에 없어 미확인 → VALID_MODELS에서 제외 (실제 값 확인 필요)
# Codex CLI 0.137.0 (`codex --help`, `codex exec --help`):
#   -m/--model <MODEL>  모델 지정 플래그는 존재하나 유효 모델명 목록은 CLI에서 미노출
#                       → codex 모델명은 placeholder(None), 전달하지 않음 (실제 값 확인 필요)
#   추론 강도 전용 플래그 없음. config 오버라이드로만 지정:
#   -c model_reasoning_effort=<값>  유효 값 목록 미확인 → CODEX_EFFORT_MAP은
#                                   low/medium/high만 사용 (실제 값 확인 필요)
#
# 저장/로드는 teams/personal/schedule.py와 동일 패턴:
# 임시파일 + os.replace 원자적 쓰기, 프로세스 내 직렬화는 asyncio.Lock 1개.

import asyncio
import json
import os

# 프로젝트 루트 (teams/dev → teams → 루트)
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_PATH = os.path.join(_BASE, "dev_settings.json")

# claude --help에서 확인된 별칭만 허용 (전체 모델명 입력은 별칭으로 통일)
VALID_MODELS = ("fable", "opus", "sonnet")
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
VALID_MODES = ("auto", "approval", "manual")

DEFAULTS = {
    "default_model": "sonnet",
    "default_effort": "medium",
    "autonomy_mode": "manual",
}

# 강도 → (claude 모델 별칭, effort) 매핑. 0번 조사 결과 기반:
# high는 상위 모델 + 높은 effort, low는 같은 모델의 낮은 effort.
INTENSITY_MODEL_MAP = {
    "high": ("opus", "xhigh"),
    "medium": ("sonnet", "medium"),
    "low": ("sonnet", "low"),
}

# claude effort → codex model_reasoning_effort 매핑.
# codex 쪽 유효 값 목록 미확인 → 보수적으로 low/medium/high만 사용 (실제 값 확인 필요)
CODEX_EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# codex 유효 모델명 미확인 → placeholder. 확인 전까지 codex에는 --model을 전달하지 않음.
CODEX_MODEL = None  # 실제 값 확인 필요

_LOCK = asyncio.Lock()  # 설정 변경은 워커 루프(UI 경유)에서만 발생 → Lock 1개로 충분


def validate(settings: dict):
    # 없는 모델명/effort/mode 저장 시도 시 ValueError
    model = settings.get("default_model")
    if model not in VALID_MODELS:
        raise ValueError(f"없는 모델명: {model!r} (유효: {VALID_MODELS})")
    effort = settings.get("default_effort")
    if effort not in VALID_EFFORTS:
        raise ValueError(f"없는 effort: {effort!r} (유효: {VALID_EFFORTS})")
    mode = settings.get("autonomy_mode")
    if mode not in VALID_MODES:
        raise ValueError(f"없는 autonomy_mode: {mode!r} (유효: {VALID_MODES})")


def _write_atomic(path: str, data: dict):
    # schedule.py와 동일: 임시 파일 작성 → flush/fsync → os.replace
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_settings(path: str | None = None) -> dict:
    """설정 로드. 파일 없으면 기본값으로 생성, JSON 깨짐/잘못된 값이면 기본값 복구."""
    path = path or SETTINGS_PATH
    if not os.path.exists(path):
        data = dict(DEFAULTS)
        _write_atomic(path, data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("dev_settings.json 루트가 객체가 아님")
    except (ValueError, OSError) as exc:
        print(f"[settings] dev_settings.json 깨짐 → 기본값 복구: {exc}")
        data = dict(DEFAULTS)
        _write_atomic(path, data)
        return data
    data = {key: raw.get(key, default) for key, default in DEFAULTS.items()}
    try:
        validate(data)
    except ValueError as exc:
        print(f"[settings] 잘못된 설정값 → 기본값 복구: {exc}")
        data = dict(DEFAULTS)
        _write_atomic(path, data)
    return data


async def save_settings(settings: dict, path: str | None = None) -> dict:
    """검증 후 원자적으로 저장. 잘못된 값이면 ValueError (파일은 건드리지 않음)."""
    validate(settings)
    data = {key: settings[key] for key in DEFAULTS}
    async with _LOCK:
        _write_atomic(path or SETTINGS_PATH, data)
    return data
