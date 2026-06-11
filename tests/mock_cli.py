# tests/mock_cli.py — 실제 CLI 없이 §7 정책을 검증하기 위한 mock 실행 파일
#
# 사용: python mock_cli.py <mode> [기록파일]
#   ok          즉시 Claude 스타일 JSON 출력 후 정상 종료
#   silent_hang 출력 없이 대기 → idle timeout 유도
#   drip        0.2초마다 출력하며 종료하지 않음 → 절대 상한 유도
#   fail        호출 횟수를 기록파일에 적고 exit 1 → 재시도 검증
#   slow_ok     시작/종료 시각을 기록파일에 적고 1초 뒤 JSON 출력 → 직렬화 검증

import json
import sys
import time

mode = sys.argv[1]

if mode == "ok":
    print(json.dumps({"type": "result", "result": "mock done", "is_error": False}))
elif mode == "silent_hang":
    time.sleep(60)
elif mode == "drip":
    while True:
        print("progress", flush=True)
        time.sleep(0.2)
elif mode == "fail":
    if len(sys.argv) > 2:
        with open(sys.argv[2], "a") as f:
            f.write("x")
    print("boom", file=sys.stderr)
    sys.exit(1)
elif mode == "slow_ok":
    log = sys.argv[2]
    with open(log, "a") as f:
        f.write(f"start {time.monotonic()}\n")
    time.sleep(1.0)
    with open(log, "a") as f:
        f.write(f"end {time.monotonic()}\n")
    print(json.dumps({"type": "result", "result": "slow done", "is_error": False}))
else:
    print(f"unknown mode: {mode}", file=sys.stderr)
    sys.exit(2)
