# teams/personal/brain.py — 브레인 에이전트: 세컨드 브레인 검색 (§3)
#
# - ai-index/summary.json + SECOND_BRAIN.md 읽기 (경로 config 분리, 주입 가능)
# - 질문 키워드를 각 문서의 keywords/tags/related_topics와 매칭 → 관련 문서 + summary 근거
# - 매칭 애매하면 Ollama(Gemma)로 관련도 보정 (비동기)
# - 리스크 4: 파일 수정 24시간 경과 시 payload에 stale 경고
# - 파일 없음/깨짐 → 추측 금지, error outcome 반환
#
# run()은 dev/agents.py와 동일하게 outcome dict 반환:
#   성공 {"ok": True, "status": "success", "payload": {...}}
#   실패 {"ok": False, "status": "failed", "payload": {...}}  (파일 문제)

import json
import os
import time
from functools import partial

import config
from ollama_client import call_ollama

MAX_MATCHES = 3  # payload에 담을 상위 문서 수


class BrainAgent:
    name = "brain"

    def __init__(self, summary_path: str | None = None,
                 brain_md_path: str | None = None,
                 stale_after: float | None = None,
                 ollama_call=None):
        self.summary_path = summary_path or config.BRAIN_SUMMARY_JSON
        self.brain_md_path = brain_md_path or config.BRAIN_MD
        self.stale_after = stale_after if stale_after is not None else config.STALE_AFTER_SECONDS
        self._ollama = ollama_call or partial(
            call_ollama, model=config.AGENT_MODELS[self.name])

    def _load_summary(self) -> tuple[list[dict], float]:
        # 파일 없음 → FileNotFoundError, 깨짐/스키마 불량 → ValueError (run에서 error로 변환)
        if not os.path.exists(self.summary_path):
            raise FileNotFoundError(self.summary_path)
        with open(self.summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)  # JSONDecodeError ⊂ ValueError
        docs = data.get("documents") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            raise ValueError("summary.json 스키마 불량: 'documents' 리스트 없음")
        mtime = os.path.getmtime(self.summary_path)
        return docs, mtime

    def _oldest_mtime(self, summary_mtime: float) -> float:
        # summary.json과 SECOND_BRAIN.md 중 더 오래된 수정 시각 기준으로 stale 판정 (리스크 4)
        mtime = summary_mtime
        if os.path.exists(self.brain_md_path):
            mtime = min(mtime, os.path.getmtime(self.brain_md_path))
        return mtime

    @staticmethod
    def _doc_terms(doc: dict) -> set[str]:
        terms = set()
        for field in ("keywords", "tags", "related_topics"):
            for t in doc.get(field, []) or []:
                t = str(t).strip().lower()
                if t:
                    terms.add(t)
        return terms

    def _match(self, query: str, docs: list[dict]) -> list[tuple[int, dict, list[str]]]:
        # term이 질문 문자열에 부분 포함되면 hit. 점수 = hit한 term 수
        q = query.lower()
        scored = []
        for doc in docs:
            hits = sorted(t for t in self._doc_terms(doc) if t in q)
            if hits:
                scored.append((len(hits), doc, hits))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    async def _llm_pick(self, query: str, docs: list[dict]) -> dict | None:
        # 애매할 때 Gemma로 가장 관련 높은 문서 id 선택 (비동기). 실패 시 None
        listing = "\n".join(
            f"- {d.get('id')}: {d.get('title')} ({', '.join(map(str, d.get('keywords', [])))})"
            for d in docs
        )
        prompt = (
            "다음 문서 목록에서 사용자 질문과 가장 관련 높은 문서의 id 하나만 출력하세요.\n"
            "관련 문서가 없으면 none 이라고만 출력하세요.\n\n"
            f"질문: {query}\n\n문서:\n{listing}\n\nid:"
        )
        answer = (await self._ollama(prompt)).strip().lower()
        if not answer or "none" in answer:
            return None
        for d in docs:
            if str(d.get("id", "")).lower() and str(d.get("id")).lower() in answer:
                return d
        return None

    @staticmethod
    def _doc_view(doc: dict, matched_terms: list[str]) -> dict:
        return {
            "id": doc.get("id"),
            "title": doc.get("title"),
            "path": doc.get("path"),
            "summary": doc.get("summary"),
            "matched_terms": matched_terms,
        }

    async def run(self, text: str) -> dict:
        try:
            docs, summary_mtime = self._load_summary()
        except FileNotFoundError:
            return {"ok": False, "status": "failed", "payload": {
                "agent": self.name, "reason": "file_not_found",
                "detail": f"summary.json 없음: {self.summary_path}",
            }}
        except ValueError as exc:
            return {"ok": False, "status": "failed", "payload": {
                "agent": self.name, "reason": "file_corrupt",
                "detail": str(exc),
            }}

        scored = self._match(text, docs)
        used_llm = False
        if scored:
            top = scored[0][0]
            tied = [s for s in scored if s[0] == top]
            if len(tied) > 1:  # 동점 → 애매 → Gemma 보정
                used_llm = True
                picked = await self._llm_pick(text, [s[1] for s in tied])
                if picked is not None:
                    scored = [s for s in scored if s[1] is picked] + \
                             [s for s in scored if s[1] is not picked]
            matches = [self._doc_view(d, terms) for _, d, terms in scored[:MAX_MATCHES]]
            answer = scored[0][1].get("summary") or "(summary 비어 있음)"
        else:
            # rule 매칭 0건 → Gemma로 전체 문서 중 관련 문서 탐색 (애매 케이스)
            used_llm = True
            picked = await self._llm_pick(text, docs)
            if picked is not None:
                matches = [self._doc_view(picked, [])]
                answer = picked.get("summary") or "(summary 비어 있음)"
            else:
                matches = []
                answer = "관련 정리 문서를 찾지 못했습니다."

        stale = (time.time() - self._oldest_mtime(summary_mtime)) > self.stale_after
        payload = {
            "agent": self.name,
            "query": text,
            "matched": matches,
            "answer": answer,
            "used_llm": used_llm,
            "stale": stale,
            "sources": [os.path.basename(self.summary_path)],
        }
        if os.path.exists(self.brain_md_path):
            payload["sources"].append(os.path.basename(self.brain_md_path))
        if stale:
            payload["warning"] = "세컨드 브레인 파일이 24시간 이상 갱신되지 않음 — 최신 정보가 아닐 수 있음 (리스크 4)"
        return {"ok": True, "status": "success", "payload": payload}
