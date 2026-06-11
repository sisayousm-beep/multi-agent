# 멀티 에이전트 시스템

<p align="center">
  <img src="키비쥬얼.png" alt="키비쥬얼" width="240">
</p>

로컬 LLM(Ollama + Gemma 12B)이 오케스트레이터 역할을 하며, 세 팀(개인 비서팀 / 개발팀 / ComfyUI 에이전트)에 태스크를 분배하는 로컬 멀티 에이전트 시스템. Pygame 도트풍 픽셀 RPG UI로 에이전트 간 통신을 시각화한다.

![UI 미리보기](assets/ui_preview.png)

> 상세 설계는 [multi_agent_system_design_v3.md](multi_agent_system_design_v3.md) 참고. 새 컨텍스트에서 작업을 이어갈 때는 설계 문서를 먼저 읽을 것.

---

## 🚀 처음이라면: 따라 하기 실행 가이드

프로그래밍을 몰라도 아래 순서대로만 하면 실행됩니다.

### 1단계. Python 설치

1. https://www.python.org/downloads/ 에서 **Python 3.13 이상** 다운로드
2. 설치 화면 **맨 아래 "Add python.exe to PATH" 체크박스를 반드시 체크**하고 Install Now 클릭
3. 확인: 키보드에서 `윈도우 키 + R` → `cmd` 입력 → 엔터 → 검은 창에 아래 입력

   ```
   python --version
   ```

   `Python 3.13.x` 같은 글자가 나오면 성공.

### 2단계. 필요한 패키지 설치

같은 검은 창(명령 프롬프트)에 한 줄씩 입력하고 엔터:

```
pip install pygame httpx
```

### 3단계. Ollama 설치 + AI 모델 받기

1. https://ollama.com 에서 Ollama 다운로드 후 설치 (설치만 하면 자동으로 켜져 있음)
2. 검은 창에 아래 입력 (모델 용량이 커서 다운로드에 시간이 걸림):

   ```
   ollama pull gemma4:12b-it-q4_K_M
   ```

   > 다른 모델을 쓰고 싶으면 받은 뒤 `config.py`의 `OLLAMA_MODEL` 값을 그 이름으로 바꾸면 됨.

### 4단계. 실행

**바탕화면의 "멀티 에이전트" 아이콘(주황색 로봇)을 더블클릭** — 끝.

아이콘이 없으면 이 폴더의 `run_app.bat`을 더블클릭해도 똑같습니다.

### 5단계. 사용법

- 창이 뜨면 **맨 아래 입력칸에 한국어로 하고 싶은 일을 입력**하고 엔터
  - 예: `오늘 일정 알려줘` → 비서팀
  - 예: `hello.py 파일 만들어줘` → 개발팀
  - 예: `고양이 그림 그려줘` → ComfyUI
- 위쪽 맵에서 도트 캐릭터가 담당 팀으로 걸어가는 게 보이고, 가운데 로그 창에 진행 상황이 글자로 출력됨
  - 노랑 = 진행 중 / 초록 = 성공 / 빨강 = 실패

### 자주 막히는 곳 (문제 해결)

| 증상 | 해결 |
|---|---|
| `python은(는) 내부 또는 외부 명령...` 오류 | 1단계에서 "Add to PATH" 체크를 빠뜨림 → Python 재설치하며 체크 |
| 창은 뜨는데 무엇을 입력해도 응답이 없음 | Ollama가 꺼져 있거나 모델을 안 받음 → 3단계 다시 확인 |
| 그림 그려달라니 에러 | ComfyUI(localhost:8188)가 꺼져 있는 것. 그림 기능만 비활성화되고 나머지는 정상 |
| 개발 요청이 에러 | Claude Code CLI / Codex CLI가 설치 안 된 것 (개발자용 기능, 없어도 다른 팀은 정상) |
| 일단 화면만 구경하고 싶음 | 검은 창에서 이 폴더로 이동 후 `python -m ui.main --mock` (AI 없이 가짜 데이터로 동작) |

---

## 아키텍처

```
[사용자 입력]
     ↓
[오케스트레이터]  ← rule-based 1차 분류 → Gemma 2차 보정 → 실패 시 사용자 팀 선택 fallback
     ↓ (message envelope)
┌─────────────┬──────────────┬──────────────┐
│ 개인 비서팀   │ 개발팀        │ ComfyUI 에이전트 │
│ 브레인/스케줄 │ Claude Code/  │ REST API      │
│             │ Codex CLI     │ (GPU 중재)     │
└─────────────┴──────────────┴──────────────┘
     ↓ (message envelope)
[queue.Queue → Pygame UI]
```

- **동시성 모델**: 메인 스레드 = Pygame 60fps 루프, 워커 스레드 = asyncio 이벤트 루프. 연결점은 `queue.Queue` 2개(q_in / q_out)뿐.
- **메시지 스키마**: 모든 에이전트 간 통신과 UI 이벤트는 단일 envelope(`task_id`/`from`/`to`/`type`/`status`/`payload`/`timestamp`)로 통일.
- **GPU 중재**: RTX 4060 8GB에서 Ollama와 ComfyUI 동시 실행 불가 → ComfyUI 작업 전 Gemma 언로드, 완료 후 재로드 (`gpu_arbiter.py`).

## 요구 사항

- Python 3.13+ / `httpx` / `pygame`
- [Ollama](https://ollama.com) + Gemma 12B Q4_K_M (`gemma4:12b-it-q4_K_M`) — `localhost:11434`
- (개발팀) Claude Code CLI / Codex CLI가 PATH에 있을 것
- (이미지 생성) ComfyUI — `localhost:8188` (꺼져 있으면 해당 팀만 비활성화, 나머지 정상 동작)

## 실행

```bash
# Pygame UI (실백엔드) — 바탕화면 아이콘 또는 run_app.bat과 동일
python -m ui.main

# UI 단독 테스트 (백엔드 없이 mock)
python -m ui.main --mock

# N프레임 후 자동 종료 (스모크 테스트)
python -m ui.main --smoke 300

# 콘솔 REPL (UI 없이)
python main.py
```

## 디자인 / 키비쥬얼

- `키비쥬얼.png` — 앱 대표 이미지 (도트 로봇). UI 창 아이콘 + 맵 좌측 상단 로고로 사용
- `assets/app_icon.ico` — 키비쥬얼에서 변환한 Windows 아이콘 (바탕화면 바로가기용)
- UI 팔레트는 키비쥬얼 기반: 다크 배경 `(23,23,23)` + 오렌지 `(240,128,80)` (`ui/layout.py`의 `KV_*` 상수)
- 바탕화면 바로가기가 지워졌으면 PowerShell에서 재생성:

  ```powershell
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut("$env:USERPROFILE\Desktop\멀티 에이전트.lnk")
  $lnk.TargetPath = "C:\Users\User\Desktop\multi-agent\run_app.bat"
  $lnk.WorkingDirectory = "C:\Users\User\Desktop\multi-agent"
  $lnk.IconLocation = "C:\Users\User\Desktop\multi-agent\assets\app_icon.ico,0"
  $lnk.Save()
  ```

## 테스트

전부 네트워크/실제 CLI 불필요 (Ollama stub + mock CLI 사용).

```bash
python -m pytest tests/
```

| 파일 | 범위 |
|---|---|
| `tests/test_stage2.py` | 오케스트레이터 분류 + envelope + 동시성 골격 |
| `tests/test_stage3.py` | 개발팀 subprocess (idle/절대 타임아웃, 재시도, 직렬화) |
| `tests/test_stage4.py` | 비서팀 브레인 검색 + 일정 CRUD |
| `tests/test_stage5.py` | ComfyUI 에이전트 + GPU 중재 |

## 디렉토리 구조

```
config.py               # 전역 설정 (모델, 타임아웃, 키워드, 경로)
messages.py             # message envelope 헬퍼 (§4)
runtime.py              # 워커 스레드 + asyncio 루프 골격 (§6)
orchestrator.py         # rule-based + Gemma 분류, 팀 라우팅
ollama_client.py        # Ollama API 비동기 클라이언트
gpu_arbiter.py          # Ollama ↔ ComfyUI VRAM 순차 전환 (리스크 6)
comfyui_agent.py        # ComfyUI REST API 에이전트 (health check → prompt → history 폴링)
main.py                 # 콘솔 REPL 진입점
run_app.bat             # 더블클릭 실행용 (바탕화면 바로가기가 가리키는 파일)
assets/                 # app_icon.ico (키비쥬얼 변환) + ui_preview.png
teams/
  dev/                  # 개발팀: PM + Claude Code/Codex subprocess (§7)
  personal/             # 비서팀: PM + 브레인(summary.json 검색) + 스케줄(schedule.json CRUD)
  comfyui/              # ComfyUI 팀 PM
ui/                     # Pygame 도트풍 UI (main/actors/sprites/layout/mock_backend)
workflows/              # ComfyUI 워크플로우 템플릿 (lola_base.json — placeholder, README 참고)
second_brain/           # 세컨드 브레인 데이터 (SECOND_BRAIN.md + ai-index/summary.json)
tests/                  # 단계별 테스트 + mock CLI
```

## subprocess 실행 정책 (개발팀)

- `claude -p "<지시>" --output-format json --allowedTools "Read,Edit,Write,Bash"` / `codex exec --json`
- idle timeout 90초(출력 시 리셋) + 절대 상한 15분 + 최대 재시도 3회
- 결과는 raw stdout 금지 — JSON 파싱 후 envelope `payload`로 변환
- 같은 작업 디렉토리 대상 작업은 PM이 `asyncio.Lock`으로 직렬화 (리스크 7)

## 진행 상태

설계 문서 §9 기준 1~6단계 전부 완료 (Ollama 세팅 → 오케스트레이터 → 개발팀 subprocess → 비서팀 → ComfyUI → Pygame UI).
