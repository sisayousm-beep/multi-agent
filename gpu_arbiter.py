# gpu_arbiter.py — 리스크 6: Ollama(Gemma 12B) ↔ ComfyUI VRAM 경합 순차 중재
#
# RTX 4060 8GB라 Gemma와 ComfyUI 동시 실행 불가. ComfyUI 작업 전 Gemma 언로드,
# 작업 후(성공/실패/타임아웃 무관) 재로드한다. 오케스트레이터가 GPU 자원 중재자
# 역할을 수행하며(설계 §3·리스크 6), 이 모듈이 그 실제 로직을 담당한다.
#
# 모든 Ollama 호출은 best-effort: 실패해도 예외를 삼킨다(전환 자체가 중단되면 안 됨).
# 단위 테스트에서 네트워크 없이 동작을 검증할 수 있도록 unload/loaded/reload 콜러블 주입 가능.

import asyncio

import httpx

import config
from messages import make_envelope


async def _ollama_unload():
    # keep_alive: 0 → 이 요청 처리 후 모델을 VRAM에서 즉시 언로드
    payload = {"model": config.OLLAMA_MODEL, "keep_alive": 0, "stream": False}
    async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
        resp = await client.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload)
        resp.raise_for_status()


async def _ollama_loaded_models():
    # /api/ps: 현재 메모리에 로드된 모델 목록
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{config.OLLAMA_BASE_URL}/api/ps")
        resp.raise_for_status()
        return [m.get("name") or m.get("model") for m in resp.json().get("models", [])]


async def _ollama_reload():
    # 더미 요청으로 Gemma를 다시 VRAM에 로드 (재로드 지연 감수)
    payload = {"model": config.OLLAMA_MODEL, "prompt": " ", "stream": False,
               "keep_alive": config.OLLAMA_KEEP_ALIVE}
    async with httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT) as client:
        resp = await client.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload)
        resp.raise_for_status()


class GpuArbiter:
    """ComfyUI 작업 전후로 Ollama 모델을 순차 전환한다.

    busy: ComfyUI 작업(언로드~재로드)이 진행 중인지. 오케스트레이터가 이 플래그로
          전환 중 들어온 다른 요청을 즉시 안내 반환해 데드락 없이 직렬화한다.
    """

    def __init__(self, q_out, *, unload=None, loaded_models=None, reload=None,
                 poll_interval=None, poll_max=None):
        self.q_out = q_out
        self.busy = False
        self._unload = unload or _ollama_unload
        self._loaded = loaded_models or _ollama_loaded_models
        self._reload = reload or _ollama_reload
        self.poll_interval = poll_interval if poll_interval is not None else config.OLLAMA_UNLOAD_POLL_INTERVAL
        self.poll_max = poll_max if poll_max is not None else config.OLLAMA_UNLOAD_POLL_MAX

    def _status(self, task_id: str, detail: str):
        # UI용 "모델 전환 중" status 메시지 (리스크 6)
        self.q_out.put(make_envelope(
            task_id, "orchestrator", "user", "status", "running",
            {"phase": "gpu_switch", "detail": detail},
        ))

    async def prepare(self, task_id: str):
        # ComfyUI 작업 직전: Gemma 언로드 → 언로드 완료 확인
        self._status(task_id, "모델 전환 중 (Ollama 언로드)")
        try:
            await self._unload()
        except Exception:
            pass  # best-effort: 언로드 실패해도 진행 (Ollama가 이미 꺼져 있을 수 있음)
        for _ in range(self.poll_max):
            try:
                models = await self._loaded()
            except Exception:
                break  # /api/ps 조회 불가 → 확인 포기하고 진행
            if config.OLLAMA_MODEL not in models:
                break  # 언로드 확인 완료
            await asyncio.sleep(self.poll_interval)

    async def restore(self, task_id: str):
        # ComfyUI 작업 완료/실패/타임아웃 후: Gemma 재로드 (반드시 호출 — 오케스트레이터 finally)
        self._status(task_id, "모델 전환 중 (Gemma 재로드)")
        try:
            await self._reload()
        except Exception:
            pass  # best-effort
