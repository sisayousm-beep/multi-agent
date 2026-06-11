# SECOND_BRAIN

세컨드 브레인 루트 인덱스. 자동화 파이프라인이 `ai-index/summary.json`을 생성/갱신한다.
브레인 에이전트는 summary.json을 1차 근거로 검색하며, 이 파일의 수정 시각도 stale 판정(리스크 4)에 함께 본다.

## 노트
- [[notes/rag.md]] — RAG 파이프라인 정리
- [[notes/asyncio.md]] — 파이썬 asyncio 동시성 메모
- [[notes/comfyui.md]] — ComfyUI 워크플로우 메모
