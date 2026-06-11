# 멀티 에이전트 시스템 설계 문서 (v2)

> 새 컨텍스트에서 이어갈 때 이 파일을 먼저 읽을 것.
> v2 변경점: 리스크 6~7 추가, 동시성 모델 확정(스레드 내 asyncio), 메시지 스키마 정의, subprocess 타임아웃 방식 변경(idle timeout), Claude Code CLI 자율 호출 방식 명시, 1단계 완료 기록.

---

## 0. 진행 상태

- ✅ 1단계 완료: Ollama 설치 + Gemma 12B Q4_K_M 로컬 실행 + Python API 연결 확인
  - ⚠️ 실제 pull한 모델 태그를 아래에 기록할 것 (예: `gemma3:12b-it-q4_K_M`):`gemma4:12b-it-q4_K_M`
  - 실측 토큰 속도 (RAM 오프로딩 포함): ___ tok/s ← 2단계 분류 지연 설계에 사용
- ✅ 2단계 완료 (2026-06-11): 오케스트레이터 + PM 루프 스켈레톤
  - `messages.py`: §4 envelope 헬퍼 (`make_envelope` / `new_task_id`, type·status 검증 포함)
  - `runtime.py`: §6 동시성 골격 — 워커 스레드 안 `asyncio.run()`, q_in/q_out 2개, `loop.run_in_executor(None, q_in.get)`로 이벤트 루프 블로킹 방지
  - `orchestrator.py`: rule-based 1차 → Ollama(httpx 비동기) 2차 보정 → 분류 실패 시 사용자 팀 선택 fallback (다음 입력이 팀 이름이면 보류 요청 라우팅)
  - personal/comfyui PM은 envelope 반환 mock, PM 예외는 error envelope로 전파
  - 테스트: `tests/test_stage2.py` (Ollama stub, 네트워크 불필요) — 전체 통과
- ✅ 3단계 완료 (2026-06-11): 개발팀 Claude Code/Codex subprocess 연동 (§7)
  - `teams/dev/subprocess_runner.py`: `asyncio.create_subprocess_exec` 기반, idle timeout 90초(출력 시 리셋) + 절대 상한 15분 + 재시도 3회
  - `teams/dev/agents.py`: ClaudeCodeAgent(`claude -p --output-format json --allowedTools "Read,Edit,Write,Bash"`), CodexAgent(`codex exec --json --skip-git-repo-check`). 출력은 JSON 파싱 후 payload로 변환 (raw stdout 금지)
  - `teams/dev/pm.py`: 요청에 "codex" 명시 시 Codex, 기본 Claude Code. cwd별 `asyncio.Lock`으로 같은 디렉토리 직렬화(리스크 7). 호출 중/재시도/대기 status 메시지를 q_out으로 송신
  - 타임아웃/실패는 `status: timeout|failed`인 error envelope로 오케스트레이터까지 전파
  - 테스트: `tests/test_stage3.py` + `tests/mock_cli.py` (idle/절대 상한/재시도/직렬화/병렬, 실제 CLI 불필요) — 전체 통과
- ✅ 4단계 완료 (2026-06-11): 개인 비서팀 세컨드 브레인 + 일정 연동 (§3·리스크 4)
  - `teams/personal/brain.py`: summary.json + SECOND_BRAIN.md 키워드 매칭 검색, 동점/0건 시 Gemma 보정, 24시간 stale 경고(리스크 4), 파일 없음/깨짐은 error outcome
  - `teams/personal/schedule.py`: schedule.json 기반 CRUD 5종(추가/조회/수정/삭제/오늘 요약), 자연어 rule-based 파싱, 임시파일+os.replace 원자적 쓰기 + asyncio.Lock 직렬화
  - `teams/personal/pm.py`: 키워드 점수 1차 → Gemma 2차로 브레인/스케줄 라우팅, outcome을 result/error envelope로 변환
  - 테스트: `tests/test_stage4.py` (네트워크 불필요, Ollama stub) — 전체 통과
- ✅ 5단계 완료 (2026-06-11): ComfyUI 에이전트 REST API 연동 + GPU 자원 중재 (§3·리스크 3·6)
  - `comfyui_agent.py`: 단일 에이전트. health check(GET /system_stats) → POST /prompt → GET /history 폴링(2초 간격, 최대 10분) → 이미지 경로 result. 모든 결과 envelope 변환(result/error), httpx 비동기(스레드 추가 없음)
  - `workflows/lola_base.json` + `workflows/README.md`: lola 스타일 워크플로우 placeholder 스켈레톤. 에이전트가 `{{PROMPT}}/{{NEGATIVE}}/{{SEED}}/{{WIDTH}}/{{HEIGHT}}` 토큰만 치환. 실제 체크포인트/LoRA 노드는 README 치환 지점 참고해 직접 채울 것
  - `gpu_arbiter.py`: 리스크 6. ComfyUI 작업 전 Gemma 언로드(keep_alive:0)→/api/ps로 언로드 확인→작업→finally 재로드. busy 플래그로 전환 중 다른 요청 즉시 안내(데드락 없음)
  - `orchestrator.py`: comfyui 경로를 mock PM 대신 ComfyUIAgent+GpuArbiter로 교체. dev/personal/분류/fallback 로직은 2~4단계 그대로. ComfyUI 미실행 시 비활성화→error envelope, 나머지 팀 정상
  - 테스트: `tests/test_stage5.py` (네트워크 불필요, stub 주입) — 미실행/해피패스 순서/busy 데드락/타임아웃 재로드/노드에러/템플릿 치환 전체 통과. stage2 comfyui mock 단언 2개는 실제 에이전트 동작에 맞게 갱신
- ✅ 6단계 완료 (2026-06-11): Pygame 도트풍 UI (§6·§8)
  - `ui/main.py`: 메인 스레드 60fps 루프. 매 프레임 `q_out.get_nowait()` + `except queue.Empty`, 입력은 `q_in.put()`만 — 백엔드 연결점은 queue 2개뿐(기존 루프 무수정). 로그 패널(status 색상: running=노랑/success=초록/failed·timeout=빨강, 휠 스크롤) + 한글 IME 입력창(TEXTINPUT/TEXTEDITING) + gpu_switch 배너(리스크 6)
  - `ui/actors.py`: 애니메이션 상태 머신 IDLE→WALK_OUT→PAUSE→WALK_BACK. request=from이 to로 걷기, status/running="..." 말풍선, result/error=바운스+✓/✗ 말풍선
  - `ui/sprites.py`: 외부 이미지 없이 16x16 절차적 도트 캐릭터(에이전트별 색). `get_frames()`만 교체하면 실제 스프라이트로 전환 가능
  - `ui/layout.py`: 비서팀/개발팀/ComfyUI 구역 타일 색 구분 + 에이전트 홈 좌표
  - `ui/mock_backend.py`: AgentRuntime 동일 인터페이스 mock. `python -m ui.main --mock`으로 백엔드 없이 UI 단독 테스트, `python -m ui.mock_backend`로 콘솔 확인
  - 실행: `python -m ui.main` (실백엔드) / `--mock` (단독) / `--smoke N` (N프레임 후 자동 종료)

---

## 1. 시스템 개요

로컬 LLM(Gemma 12B)이 오케스트레이터 역할을 하며, 세 팀(개인 비서팀, 개발팀, ComfyUI 에이전트)에 태스크를 분배하는 멀티 에이전트 시스템.

개발팀은 Claude Code CLI / Codex CLI를 subprocess로 자율 호출한다.

---

## 2. 기술 스택

| 역할 | 기술 |
|---|---|
| 로컬 LLM | Ollama + Gemma 12B (Q4_K_M 양자화) |
| 에이전트 프레임워크 | Python + 자체 루프 |
| 동시성 모델 | **별도 스레드 내 asyncio 이벤트 루프 (확정)** |
| 코딩 도구 | Claude Code CLI / Codex CLI (`-p` 비대화형 모드, subprocess 호출) |
| ComfyUI 연동 | ComfyUI REST API (localhost:8188) |
| UI | Pygame (도트풍 픽셀 RPG 스타일, 메인 스레드) |
| 세컨드 브레인 | 로컬 summary.json / SECOND_BRAIN.md 읽기 |
| 스레드 통신 | `queue.Queue` (UI ↔ 에이전트 루프) |

---

## 3. 에이전트 구성

### 오케스트레이터 (1명)
- 사용자 입력을 받아 어느 팀으로 보낼지 판단
- rule-based 1차 분류 → Gemma 2차 보정 방식
- 분류 실패 시 사용자에게 팀 선택 요청 (fallback)
- **GPU 자원 중재자 역할 겸임**: ComfyUI 작업 전 Ollama 모델 언로드 지시 (리스크 6 참고)

### 개인 비서팀 (3명)
| 에이전트 | 역할 |
|---|---|
| PM (팀장) | 사용자 요청 분석 → 비서팀 내 분배 |
| 브레인 에이전트 | summary.json 읽고 관련 정보 검색/응답 |
| 스케줄 에이전트 | 일정/할 일 관리 (로컬 JSON 파일 기반) |

### 개발팀 (3명)
| 에이전트 | 역할 |
|---|---|
| PM (팀장) | 요청 분석 → Claude Code 또는 Codex 선택 |
| Claude Code 에이전트 | `claude -p` subprocess 호출 |
| Codex 에이전트 | codex CLI subprocess 호출 |

### ComfyUI 에이전트 (단일)
- 노드 세팅 생성
- ComfyUI REST API로 워크플로우 전송
- lola 그림체 스타일 이미지 생성
- **실행 전 Ollama 언로드 → 완료 후 재로드** (리스크 6)

---

## 4. 메시지 스키마 (에이전트 간 통신 규약) ★신규

모든 에이전트 간 통신과 UI 이벤트는 아래 envelope 하나로 통일한다.
Pygame의 캐릭터 이동 애니메이션도 이 메시지를 구독해서 트리거한다.

```python
{
    "task_id": "uuid4 문자열",        # 하나의 사용자 요청 단위로 동일 유지
    "from": "orchestrator",          # orchestrator | pm_assistant | pm_dev | brain | schedule | claude_code | codex | comfyui | user
    "to": "pm_dev",
    "type": "request",               # request | result | error | status (진행중 알림, UI용)
    "status": "running",             # pending | running | success | failed | timeout
    "payload": { ... },              # type별 자유 형식 (request: 지시 내용 / result: 결과물 / error: 에러 상세)
    "timestamp": "ISO8601"
}
```

규칙:
- 모든 subprocess 결과는 `result` 또는 `error` 메시지로 변환 후 반환 (raw stdout을 그대로 넘기지 않음)
- `status` 타입 메시지는 UI 전용 (예: "Claude Code 호출 중" → 캐릭터 걷기 애니메이션 트리거)
- 실패/타임아웃은 반드시 `error` 메시지로 오케스트레이터까지 전파

---

## 5. 전체 구조 흐름

```
[사용자 입력]
     ↓
[오케스트레이터]
  - rule-based 1차 분류
  - Gemma 2차 보정
  - 분류 실패 → 사용자에게 팀 선택 요청
     ↓ (message envelope)
[팀 PM들 - subprocess 호출 (idle timeout + 재시도 제한)]
     ↓
[Claude Code CLI / Codex CLI / ComfyUI REST API]
     ↓ (message envelope)
[결과 → queue.Queue → Pygame UI]
```

---

## 6. 동시성 모델 (확정) ★신규

```
[메인 스레드]                     [워커 스레드]
 Pygame UI 루프                   asyncio 이벤트 루프
  - 60fps 렌더링                   - 오케스트레이터
  - q.get_nowait()로 메시지 수신    - asyncio.create_subprocess_exec
  - 입력 → q_in.put()              - aiohttp/httpx로 Ollama, ComfyUI 호출
        ↕  queue.Queue 2개 (q_in: UI→에이전트, q_out: 에이전트→UI)
```

규칙:
- Pygame은 메인 스레드 고정, 매 프레임 `get_nowait()` + `except queue.Empty` 패턴으로 non-blocking 수신
- 에이전트 루프는 `threading.Thread` 안에서 `asyncio.run()` 실행
- asyncio 쪽에서 `queue.Queue` 접근 시 `loop.run_in_executor(None, q_in.get)` 사용 (이벤트 루프 블로킹 방지)
- subprocess는 전부 `asyncio.create_subprocess_exec` (스레드 추가 생성 금지)

---

## 7. subprocess 실행 정책 ★변경

### Claude Code CLI 자율 호출 방식
```bash
claude -p "<지시 프롬프트>" --output-format json --allowedTools "Read,Edit,Write,Bash"
```
- 비대화형(print) 모드 필수 — 대화형 모드는 자동화 불가
- 권한: `--allowedTools`로 허용 도구 명시. `--dangerously-skip-permissions`는 **격리된 작업 디렉토리에서만** 사용 고려
- 출력: `--output-format json` 파싱 → message envelope의 `payload`로 변환
- 작업 디렉토리: 태스크별 지정 디렉토리에서 실행 (`cwd` 파라미터). Claude Code와 Codex가 같은 디렉토리를 동시에 만지지 않도록 개발팀 PM이 직렬화

### 타임아웃 (고정 120초 → idle timeout 방식으로 변경)
- **idle timeout**: stdout/stderr 무출력 90초 지속 시 kill (출력이 계속 나오면 리셋)
- **절대 상한**: 15분 (어떤 경우에도 초과 불가)
- 최대 재시도: 3회 (기존 유지)
- kill 시 `status: timeout`의 error 메시지를 오케스트레이터로 반환

---

## 8. Pygame UI 컨셉

- 도트풍 픽셀 RPG 스타일
- 에이전트가 소통할 때 도트 캐릭터가 상대방 쪽으로 걸어가는 애니메이션
- 애니메이션 트리거: message envelope의 `from`/`to`/`type` 필드 구독
- 상단: 에이전트 맵 (캐릭터 이동 시각화)
- 하단: 대화 로그 (터미널 스타일)

```
┌─────────────────────────────────┐
│  🗺️ 에이전트 맵 (도트 캐릭터 이동)  │
│                                 │
│  [PM]──→[브레인]   [PM]──→[CC]  │
│   비서팀            개발팀       │
│                                 │
│         [ComfyUI]               │
├─────────────────────────────────┤
│  💬 대화 로그 (터미널 스타일)      │
│  > 오케스트레이터: 개발팀으로 전달  │
│  > 개발PM: Claude Code 호출 중   │
└─────────────────────────────────┘
```

---

## 9. 구현 순서

```
1단계: Ollama + Gemma 12B 세팅                          ✅ 완료
2단계: 오케스트레이터 + 각 팀 PM 기본 루프               ✅ 완료
       └ 메시지 스키마(§4) + 동시성 골격(§6)을 이 단계에서 구현
3단계: 개발팀 Claude Code/Codex subprocess 연동          ✅ 완료
       └ subprocess 실행 정책(§7) 적용
4단계: 개인 비서팀 세컨드 브레인 + 일정 연동             ✅ 완료
5단계: ComfyUI 에이전트 REST API 연동                    ✅ 완료
       └ GPU 자원 중재(리스크 6) 이 단계에서 구현
6단계: Pygame 도트풍 UI                                 ✅ 완료
```

> **우선순위**: Fable 5가 구독 플랜 무료인 6월 22일 전에 2~3단계 완료 목표.

---

## 10. 리스크 및 해결 방향

### 🔴 리스크 1: subprocess 무한 실행 위험
**문제**: Claude Code/Codex subprocess 호출 시 에러 발생하면 재시도 루프에 빠질 수 있음.

**해결** (v2에서 변경):
- 최대 재시도 횟수 고정 (3회)
- ~~고정 120초 타임아웃~~ → **idle timeout 90초 + 절대 상한 15분** (§7 참고. 정상적인 긴 코딩 작업이 강제 종료되는 것 방지)
- 실패 시 오케스트레이터에 `error` 메시지 반환

### 🔴 리스크 2: Gemma 분류 불안정
**문제**: 12B 로컬 모델이 애매한 요청을 팀에 잘못 분류할 수 있음.

**해결**:
- rule-based 키워드 매칭으로 1차 분류
- Gemma LLM으로 2차 보정
- 분류 실패 시 사용자에게 팀 직접 선택 요청 (fallback)

### 🔴 리스크 6: Ollama ↔ ComfyUI VRAM 경합 ★신규
**문제**: Gemma 12B Q4_K_M은 단독으로 7~8GB를 차지해 RTX 4060(8GB)을 거의 점유. ComfyUI 이미지 생성과 동시 실행 시 OOM 또는 극단적 속도 저하.

**해결**:
- **동시 실행 금지, 순차 실행 원칙**
- ComfyUI 작업 직전: Ollama에 `keep_alive: 0`으로 요청하거나 `ollama stop <model>`로 모델 언로드
- 이미지 생성 완료 후 모델 재로드 (재로드 지연 감수, UI에 "모델 전환 중" 상태 표시)
- 오케스트레이터가 GPU 자원 중재자 역할 수행

### 🟡 리스크 3: ComfyUI API 연결 전제 조건
**문제**: ComfyUI가 꺼져있으면 에이전트 호출 시 에러.

**해결**:
- 에이전트 시작 시 localhost:8188 health check
- ComfyUI 꺼져있으면 즉시 사용자에게 알림
- ComfyUI 에이전트 선택적 비활성화 옵션 제공

### 🟡 리스크 4: 세컨드 브레인 파일 동기화 타이밍
**문제**: summary.json이 자동화 파이프라인으로 업데이트되는데, 로컬 에이전트가 구버전을 읽을 수 있음.

**해결**:
- summary.json 읽을 때 파일 수정 시각 확인
- 24시간 이상 오래됐으면 "최신 정보가 아닐 수 있음" 경고 표시

### 🟡 리스크 5: Pygame UI와 백엔드 루프 스레드 충돌
**문제**: Pygame은 메인 스레드 필수, 에이전트 루프는 별도 스레드 필요. 혼용 시 UI 멈춤 또는 에이전트 블로킹.

**해결** (v2에서 확정): §6 동시성 모델 참고. 스레드 내 asyncio + queue.Queue 2개로 확정.

### 🟡 리스크 7: 작업 디렉토리 충돌 ★신규
**문제**: Claude Code와 Codex가 동시에 같은 파일/디렉토리를 수정하면 충돌.

**해결**:
- 모든 subprocess는 태스크별 작업 디렉토리(`cwd`) 명시 실행
- 개발팀 PM이 같은 디렉토리 대상 작업을 직렬화 (동시 dispatch 금지)

---

## 11. 모델/Effort 선택 기준

| 단계 | 모델 | Effort | 이유 |
|---|---|---|---|
| 전체 아키텍처 설계 | Fable 5 | ultrathink | 한번 잘못 짜면 전체 수정 |
| subprocess 비동기 루프 | Fable 5 | ultrathink | 버그 나기 가장 쉬운 부분 |
| 세컨드 브레인 파일 연동 | Opus 4.8 | think | 기존 구조 파악되어 있음 |
| ComfyUI API 연동 | Opus 4.8 | think | 단순 REST 호출 패턴 |
| Pygame UI | Fable 5 | think | 반복 수정 많아 iteration 속도 중요 |

---

## 12. 하드웨어 환경

- GPU: RTX 4060 (VRAM 8GB) — **Ollama와 ComfyUI 동시 사용 불가, 순차 전환 (리스크 6)**
- RAM: 32GB
- Gemma 12B Q4_K_M → VRAM 초과분 RAM 오프로딩 허용 (속도 저하 감수)

---

## 13. 다음 액션 (2단계)

```
1. 메시지 envelope dataclass/dict 헬퍼 작성 (§4)
2. 워커 스레드 + asyncio 루프 골격 작성 (§6)
   → queue.Queue 2개 연결, 콘솔 echo로 통신 테스트 (UI 없이)
3. 오케스트레이터 구현
   → rule-based 키워드 분류 → Ollama API(localhost:11434)로 2차 보정
   → 분류 실패 fallback 경로 포함
4. 각 팀 PM 더미 루프 (실제 도구 호출 없이 mock 응답 반환)
5. E2E 테스트: 사용자 입력 → 분류 → PM → mock 결과 → 콘솔 출력
```
