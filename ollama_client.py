# ollama_client.py — Ollama Gemma 비동기 호출 (비서팀 브레인/PM 보정 공용)
#
# orchestrator._call_ollama와 동일 정책(재시도 + 실패 시 빈 문자열). 2단계 코드를
# 건드리지 않기 위해 공용 함수로 분리해 4단계 신규 코드에서만 사용한다.

import httpx

import config


async def call_ollama(prompt: str) -> str:
    # /api/generate 비동기 호출. 오류 시 빈 문자열 반환 → 호출부가 rule-based 결과로 폴백
    payload = {"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False}
    for _ in range(config.MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
                resp = await client.post(
                    f"{config.OLLAMA_BASE_URL}/api/generate", json=payload
                )
                resp.raise_for_status()
                return resp.json().get("response", "")
        except httpx.HTTPError:
            continue
    return ""
