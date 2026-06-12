# tests/test_token_metrics.py — 토큰 속도 수집기: 기록·tps 계산·JSON 내보내기
#
# 실행: python tests/test_token_metrics.py

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import token_metrics


def test_record_computes_tps():
    token_metrics.clear()
    token_metrics.record("gemma3:12b", 100, 2.0)
    samples = token_metrics.snapshot()
    assert len(samples) == 1
    assert samples[0]["tps"] == 50.0
    assert samples[0]["model"] == "gemma3:12b"
    print("OK test_record_computes_tps")


def test_record_ignores_invalid():
    token_metrics.clear()
    token_metrics.record("m", 0, 1.0)
    token_metrics.record("m", 10, 0)
    assert token_metrics.snapshot() == []
    print("OK test_record_ignores_invalid")


def test_max_samples_cap():
    token_metrics.clear()
    for _ in range(token_metrics.MAX_SAMPLES + 50):
        token_metrics.record("m", 10, 1.0)
    assert len(token_metrics.snapshot()) == token_metrics.MAX_SAMPLES
    print("OK test_max_samples_cap")


def test_export_json():
    token_metrics.clear()
    token_metrics.record("m", 30, 1.5)
    with tempfile.TemporaryDirectory() as tmp:
        path = token_metrics.export_json(os.path.join(tmp, "out.json"))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    assert data[0]["tokens"] == 30
    assert data[0]["tps"] == 20.0
    print("OK test_export_json")


if __name__ == "__main__":
    test_record_computes_tps()
    test_record_ignores_invalid()
    test_max_samples_cap()
    test_export_json()
    print("token_metrics 테스트 전부 통과")
