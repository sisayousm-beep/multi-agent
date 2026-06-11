# workflows/ — ComfyUI 워크플로우 템플릿

`comfyui_agent.py`가 `lola_base.json`을 **텍스트로 읽어** 아래 토큰만 치환한 뒤
`json.loads()`로 파싱해 ComfyUI `/prompt` API에 제출한다.
(API 포맷 = 노드 그래프 dict. 이 파일이 그대로 `{"prompt": <이 dict>}`의 값이 된다.)

## 치환 토큰 (에이전트가 자동 치환 — 직접 건드리지 말 것)

| 토큰 | 치환 값 | 위치 (현재 스켈레톤) |
|---|---|---|
| `{{PROMPT}}` | `config.COMFYUI_STYLE_PREFIX` + 사용자 요청 (JSON 이스케이프됨) | node `6` CLIPTextEncode.text |
| `{{NEGATIVE}}` | `config.COMFYUI_NEGATIVE` (JSON 이스케이프됨) | node `7` CLIPTextEncode.text |
| `{{SEED}}` | 랜덤 정수 (또는 호출 시 지정) | node `3` KSampler.seed |
| `{{WIDTH}}` | `config.COMFYUI_DEFAULT_WIDTH` | node `5` EmptyLatentImage.width |
| `{{HEIGHT}}` | `config.COMFYUI_DEFAULT_HEIGHT` | node `5` EmptyLatentImage.height |

> 토큰은 따옴표 안(`"text": "{{PROMPT}}"`) 또는 숫자 자리(`"seed": {{SEED}}`)에 그대로
> 둔다. 문자열 토큰은 에이전트가 `json.dumps()`로 이스케이프 후 끼워 넣으므로 한글/따옴표 안전.

## 내가 나중에 채울 부분 (PLACEHOLDER)

현재는 **동작하는 최소 SD 그래프 스켈레톤**이다. lola 그림체로 만들려면 아래를 교체/추가:

1. **node `4` `ckpt_name`** — `"__REPLACE_ME__lola_checkpoint.safetensors"` 를 실제 lola 체크포인트 파일명으로 교체.
2. **LoRA (선택)** — lola LoRA를 쓴다면 `LoraLoader` 노드를 추가하고
   node `6`/`7`/`3`의 `model`·`clip` 입력을 LoRA 노드 출력으로 재배선.
   예) `"10": {"class_type":"LoraLoader","inputs":{"lora_name":"lola.safetensors","strength_model":0.8,"strength_clip":0.8,"model":["4",0],"clip":["4",1]}}`
   → KSampler.model = `["10",0]`, CLIPTextEncode.clip = `["10",1]`.
3. **샘플러 파라미터** — node `3`의 `steps`/`cfg`/`sampler_name`/`scheduler`를 lola 권장값으로 조정.
4. **해상도** — 기본값은 `config.COMFYUI_DEFAULT_*`. 그림체 권장 해상도가 있으면 config에서 변경.

노드 번호("3","4",...)는 ComfyUI API 포맷의 노드 ID일 뿐 순서와 무관하다.
재배선 시 `[노드ID, 출력슬롯]` 링크만 맞으면 된다.
