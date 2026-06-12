# token_metrics.py — 토큰 출력 속도(tok/s) 수집기
#
# ollama_client(워커 스레드)가 record()로 기록하고 UI(메인 스레드)가 snapshot()으로
# 읽는 측정 사이드채널. envelope(q_in/q_out)는 에이전트 통신 전용이므로 별도 모듈로 분리.

import json
import threading
import time

MAX_SAMPLES = 500

_lock = threading.Lock()
_samples: list[dict] = []


def record(model: str, tokens: int, duration_s: float):
    # Ollama eval_count / eval_duration 기반. 비정상 값은 무시.
    if tokens <= 0 or duration_s <= 0:
        return
    sample = {
        "ts": time.time(),
        "model": model,
        "tokens": tokens,
        "duration_s": round(duration_s, 3),
        "tps": round(tokens / duration_s, 2),
    }
    with _lock:
        _samples.append(sample)
        if len(_samples) > MAX_SAMPLES:
            del _samples[: len(_samples) - MAX_SAMPLES]


def snapshot() -> list[dict]:
    with _lock:
        return list(_samples)


def clear():
    with _lock:
        _samples.clear()


def export_json(path: str | None = None) -> str:
    # 현재 샘플을 JSON 파일로 저장하고 경로 반환
    path = path or f"token_speed_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot(), f, ensure_ascii=False, indent=2)
    return str(path)
