# comfyui_agent.py — 5단계 ComfyUI 단일 에이전트 (REST API 연동, 리스크 3)
#
# 역할: 사용자 요청 → lola 스타일 워크플로우 JSON 생성(템플릿 치환) → /prompt 제출
#       → /history 폴링으로 완료 확인 → 결과 이미지 경로 반환.
# 모든 결과는 message envelope(result/error)로 변환한다 (§4 규칙, raw 응답 금지).
# GPU 자원 중재(Ollama 언로드/재로드)는 이 에이전트가 아니라 오케스트레이터의
# GpuArbiter가 담당한다 (관심사 분리). 이 파일은 ComfyUI HTTP 호출만 책임진다.
#
# HTTP는 전부 httpx 비동기 클라이언트 (기존 asyncio 루프 안에서 실행, 스레드 추가 생성 금지, §6).

import asyncio
import json
import random

import httpx

import config
from messages import make_envelope


class ComfyUIError(Exception):
    """ComfyUI가 노드 실행 에러를 반환한 경우 (history.status.status_str == 'error')."""

    def __init__(self, detail):
        super().__init__(str(detail))
        self.detail = detail


def _json_escape(s: str) -> str:
    # 템플릿의 따옴표 안 토큰("...{{PROMPT}}...")에 끼워 넣을 안전한 문자열.
    # json.dumps가 "..." 로 감싸 주므로 양끝 따옴표만 제거해 본문 이스케이프만 취한다.
    return json.dumps(s, ensure_ascii=False)[1:-1]


class ComfyUIAgent:
    name = "comfyui"

    def __init__(self, q_out=None, base_url=None, workflow_path=None,
                 poll_interval=None, poll_timeout=None, auto_health_check=True):
        self.q_out = q_out
        self.base_url = (base_url or config.COMFYUI_BASE_URL).rstrip("/")
        self.workflow_path = workflow_path or config.COMFYUI_WORKFLOW
        self.poll_interval = poll_interval if poll_interval is not None else config.COMFYUI_POLL_INTERVAL
        self.poll_timeout = poll_timeout if poll_timeout is not None else config.COMFYUI_POLL_TIMEOUT
        # 리스크 3: 시작 시 health check. 실패 시 콘솔 경고 + 비활성화 플래그.
        self.enabled = self._sync_health_check() if auto_health_check else False

    # ---------------- health check (리스크 3) ----------------

    def _sync_health_check(self) -> bool:
        # 시스템 시작 시 1회 동기 확인 (오케스트레이터 생성 시점).
        try:
            with httpx.Client(timeout=config.COMFYUI_HEALTH_TIMEOUT) as client:
                resp = client.get(f"{self.base_url}/system_stats")
                resp.raise_for_status()
            return True
        except Exception:
            print(f"[ComfyUI] health check 실패 ({self.base_url}) - ComfyUI 에이전트 비활성화. "
                  "이미지 요청은 error로 반환됩니다.")
            return False

    async def ensure_available(self) -> bool:
        # 매 이미지 요청 직전 가용성 재확인 — 부팅 후 ComfyUI가 늦게 떴을 수 있으므로
        # 비활성 상태였어도 복구 가능. 살아있으면 enabled 갱신.
        if self.enabled:
            return True
        try:
            async with httpx.AsyncClient(timeout=config.COMFYUI_HEALTH_TIMEOUT) as client:
                resp = await client.get(f"{self.base_url}/system_stats")
                resp.raise_for_status()
            self.enabled = True
        except Exception:
            self.enabled = False
        return self.enabled

    # ---------------- 워크플로우 빌드 (템플릿 치환) ----------------

    def build_workflow(self, text: str, seed: int = None,
                       width: int = None, height: int = None) -> dict:
        with open(self.workflow_path, encoding="utf-8") as f:
            raw = f.read()
        seed = random.randint(0, 2**31 - 1) if seed is None else seed
        width = width or config.COMFYUI_DEFAULT_WIDTH
        height = height or config.COMFYUI_DEFAULT_HEIGHT
        prompt_text = config.COMFYUI_STYLE_PREFIX + text
        raw = raw.replace("{{PROMPT}}", _json_escape(prompt_text))
        raw = raw.replace("{{NEGATIVE}}", _json_escape(config.COMFYUI_NEGATIVE))
        raw = raw.replace("{{SEED}}", str(seed))
        raw = raw.replace("{{WIDTH}}", str(int(width)))
        raw = raw.replace("{{HEIGHT}}", str(int(height)))
        return json.loads(raw)

    # ---------------- REST 호출 ----------------

    async def _submit(self, client: httpx.AsyncClient, workflow: dict) -> str:
        resp = await client.post(f"{self.base_url}/prompt", json={"prompt": workflow})
        resp.raise_for_status()
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            # 검증 실패 시 node_errors가 함께 옴
            raise ComfyUIError({"reason": "submit_rejected", "response": data})
        return prompt_id

    async def _poll(self, client: httpx.AsyncClient, prompt_id: str) -> dict:
        # 완료까지 폴링. 타임아웃 → asyncio.TimeoutError, 노드 에러 → ComfyUIError.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.poll_timeout
        while True:
            resp = await client.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
            entry = resp.json().get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyUIError(self._error_detail(status))
                if status.get("completed") is True or entry.get("outputs"):
                    return entry
            if loop.time() >= deadline:
                raise asyncio.TimeoutError()
            await asyncio.sleep(self.poll_interval)

    @staticmethod
    def _error_detail(status: dict) -> dict:
        # history.status.messages 에서 execution_error 이벤트 추출
        for msg in status.get("messages", []):
            if isinstance(msg, list) and msg and msg[0] == "execution_error":
                return {"reason": "node_execution_error", "error": msg[1] if len(msg) > 1 else None}
        return {"reason": "node_execution_error", "status": status}

    def _extract_images(self, entry: dict) -> list:
        images = []
        for node_id, node_out in entry.get("outputs", {}).items():
            for img in node_out.get("images", []):
                fn = img.get("filename")
                sub = img.get("subfolder", "")
                typ = img.get("type", "output")
                url = f"{self.base_url}/view?filename={fn}&subfolder={sub}&type={typ}"
                images.append({"node": node_id, "filename": fn,
                               "subfolder": sub, "type": typ, "url": url})
        return images

    def _status(self, task_id: str, detail: str):
        # UI 전용 진행 status (캐릭터 애니메이션 트리거, §4/§8)
        if self.q_out is not None:
            self.q_out.put(make_envelope(
                task_id, self.name, "user", "status", "running", {"detail": detail},
            ))

    # ---------------- 진입점 ----------------

    async def handle(self, envelope: dict) -> dict:
        # request envelope 하나를 처리해 result/error envelope를 반환한다.
        task_id = envelope["task_id"]
        text = str(envelope["payload"].get("text", "")).strip()
        self._status(task_id, "이미지 생성 작업 시작")  # 규약 1: 작업 시작 running
        prompt_id = None
        try:
            workflow = self.build_workflow(text)
        except Exception as exc:
            return make_envelope(
                task_id, self.name, "orchestrator", "error", "failed",
                {"reason": "workflow_build_error", "detail": repr(exc)},
            )
        try:
            async with httpx.AsyncClient(timeout=config.COMFYUI_HTTP_TIMEOUT) as client:
                prompt_id = await self._submit(client, workflow)
                self._status(task_id, f"워크플로우 제출됨 (prompt_id={prompt_id}), 생성 폴링 중")
                entry = await self._poll(client, prompt_id)
                images = self._extract_images(entry)
            return make_envelope(
                task_id, self.name, "orchestrator", "result", "success",
                {"prompt_id": prompt_id, "images": images, "request": text},
            )
        except asyncio.TimeoutError:
            return make_envelope(
                task_id, self.name, "orchestrator", "error", "timeout",
                {"reason": "poll_timeout", "prompt_id": prompt_id,
                 "message": f"ComfyUI 폴링 타임아웃 ({self.poll_timeout}s 초과)"},
            )
        except ComfyUIError as exc:
            return make_envelope(
                task_id, self.name, "orchestrator", "error", "failed",
                {"reason": "comfyui_error", "prompt_id": prompt_id, "detail": exc.detail},
            )
        except httpx.HTTPError as exc:
            return make_envelope(
                task_id, self.name, "orchestrator", "error", "failed",
                {"reason": "http_error", "prompt_id": prompt_id, "detail": repr(exc)},
            )
