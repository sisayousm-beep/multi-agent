# ollama_client.py — Ollama Gemma 비동기 호출 (비서팀 브레인/PM 보정 공용)
#
# orchestrator._call_ollama와 동일 정책(재시도 + 실패 시 빈 문자열). 2단계 코드를
# 건드리지 않기 위해 공용 함수로 분리해 4단계 신규 코드에서만 사용한다.

import time

import httpx

import config
import token_metrics


async def call_ollama(prompt: str, model: str | None = None) -> str:
    # /api/generate 비동기 호출. 오류 시 빈 문자열 반환 → 호출부가 rule-based 결과로 폴백
    model = model or config.OLLAMA_MODEL
    payload = {"model": model, "prompt": prompt, "stream": False,
               "keep_alive": config.OLLAMA_KEEP_ALIVE_BY_MODEL.get(model, config.OLLAMA_KEEP_ALIVE)}
    if model == config.OLLAMA_MODEL:
        # num_gpu 오프로딩은 12B 전용 (config 주석 참고)
        payload["options"] = {"num_gpu": config.OLLAMA_NUM_GPU}
    t0 = time.perf_counter()
    for _ in range(config.MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
                resp = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/generate", json=payload
                )
                resp.raise_for_status()
                print(f"[timing] ollama {model}: {(time.perf_counter() - t0) * 1000:.0f}ms")
                data = resp.json()
                # 토큰 출력 속도 기록 (eval_duration은 나노초)
                token_metrics.record(model, data.get("eval_count", 0),
                                     data.get("eval_duration", 0) / 1e9)
                answer = data.get("response", "")
                # thinking 모델 방어: 사고 과정이 섞여 오면 최종 답만 남김
                if "</think>" in answer:
                    answer = answer.rsplit("</think>", 1)[-1]
                return answer.strip()
        except httpx.HTTPError:
            continue
    print(f"[timing] ollama {model}: 실패 ({(time.perf_counter() - t0) * 1000:.0f}ms)")
    return ""
