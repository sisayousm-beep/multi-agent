# tests/e2e_console.py — 콘솔 E2E: 사용자 입력 → 분류 → PM → mock 결과 → 콘솔 출력
#
# 실행: python tests/e2e_console.py
# main.py를 실제 subprocess로 띄워 UTF-8 stdin으로 한글 입력을 주입한다.
# (Windows 콘솔 파이프 인코딩 문제를 피하기 위한 드라이버)
# 마지막 입력은 키워드에 안 걸리므로 Ollama 2차 보정 경로를 실제로 태운다.

import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INPUTS = "\n".join([
    "내일 일정 알려줘",       # 키워드 → personal
    "이미지 하나 그려줘",     # 키워드 → comfyui
    "내일 뭐 하기로 했더라",  # 키워드 없음 → Ollama 2차 보정
    "exit",
]) + "\n"


def main():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "main.py"],
        input=INPUTS,
        capture_output=True,
        encoding="utf-8",
        env=env,
        cwd=PROJECT,
        timeout=600,
    )
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_output.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(proc.stdout)
        if proc.stderr:
            f.write("\n--- stderr ---\n" + proc.stderr)
    assert proc.returncode == 0, proc.stderr
    assert "[pm_assistant]" in proc.stdout, proc.stdout
    assert "[comfyui]" in proc.stdout, proc.stdout
    print(f"E2E OK - output saved: {out_path}")


if __name__ == "__main__":
    main()
