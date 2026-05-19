"""rag.py — Chroma 기반 RAG retriever for llm_dialogue_node.

인덱스는 `tools/build_chroma_by_category.py` 로 사전 구축한다.

persist_directory : ~/마음돌봄/med_data/chroma_db
컬렉션: 카테고리 디렉터리명 기준 자동 탐지
  (cardiovascular, neurology, gastroenterology, … 등 17개)

검색 전략:
  - 모든 컬렉션에서 per_collection_k 개씩 받아 score 기반 병합
  - 반환 구조: category/title/link/text/score
  - disease 메타데이터가 있으면 질환백과, 없으면 블로그 출처로 헤더 구분

사용:
    retriever = RagRetriever(
        index_dir="~/마음돌봄/med_data/chroma_db",
    )
    hits = retriever.retrieve("무릎이 아파요", k=3)
    block = retriever.format_for_prompt(hits)
"""

from __future__ import annotations

# chromadb 0.5+ 는 sqlite3 >= 3.35 강제. Ubuntu 20.04 의 시스템 sqlite3 는
# 3.31 이라 import 실패. pysqlite3 가 SQLite 정적 빌드(3.46) 로 깔려 있으면
# chromadb 가 import 되기 전에 sys.modules['sqlite3'] 를 swap 한다.
# 설치: see XAVIER_INSTALL_GUIDE §14 (pysqlite3 + amalgamation 정적 빌드).
import sys
try:
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

import os
from pathlib import Path
from typing import List, Optional, Sequence, Union


class RagRetriever:
    def __init__(
        self,
        index_dir: str,
        collection: Union[str, Sequence[str], None] = None,
        # Xavier 이전 후 "BAAI/bge-m3" 로 변경 + chroma_db 재빌드 예정
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        device: str = "cpu",
        max_chars_per_chunk: int = 500,
        per_collection_k: int = 3,
    ) -> None:
        self.index_dir = Path(os.path.expanduser(index_dir))
        # None 이면 런타임에 chroma_db 에서 자동 탐지
        if collection is None:
            self._explicit_collections: Optional[List[str]] = None
        elif isinstance(collection, str):
            self._explicit_collections = [collection]
        else:
            self._explicit_collections = list(collection)
        self.model_name = model_name
        self.device = device
        self.max_chars_per_chunk = max_chars_per_chunk
        self.per_collection_k = per_collection_k
        self._stores: dict = {}   # name -> Chroma
        self._embedder = None

    # --------- collection 목록 결정 ---------
    @property
    def collections(self) -> List[str]:
        """_stores 로드 후 사용 가능한 컬렉션명 목록."""
        return list(self._stores.keys())

    def _resolve_collections(self) -> List[str]:
        """명시적 지정이 없으면 chroma_db 에서 실제 컬렉션명을 읽어 반환."""
        if self._explicit_collections is not None:
            return self._explicit_collections
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self.index_dir))
            names = [c.name for c in client.list_collections()]
            return sorted(names)
        except Exception:
            # chromadb 직접 접근 실패 시 구버전 컬렉션으로 폴백
            return ["med_disease", "med_blog"]

    # --------- lazy load ---------
    def _load(self) -> None:
        if self._stores:
            return

        if not self.index_dir.exists():
            raise FileNotFoundError(
                f"RAG 인덱스 디렉터리 없음: {self.index_dir}\n"
                "  빌드: ~/마음돌봄/.venv-ros/bin/python "
                "tools/build_chroma_by_category.py"
            )

        from langchain_chroma import Chroma
        from langchain_huggingface import HuggingFaceEmbeddings

        self._embedder = HuggingFaceEmbeddings(
            model_name=self.model_name,
            model_kwargs={"device": self.device},
            encode_kwargs={"normalize_embeddings": True},
        )
        _st = getattr(self._embedder, "_client", None) \
              or getattr(self._embedder, "client", None)
        if _st is not None and hasattr(_st, "max_seq_length"):
            _st.max_seq_length = int(os.environ.get("MAX_SEQ_LEN", 512))

        col_names = self._resolve_collections()
        loaded_any = False
        for name in col_names:
            try:
                store = Chroma(
                    collection_name=name,
                    persist_directory=str(self.index_dir),
                    embedding_function=self._embedder,
                )
                try:
                    cnt = store._collection.count()  # type: ignore[attr-defined]
                except Exception:
                    cnt = -1
                if cnt == 0:
                    continue
                self._stores[name] = store
                loaded_any = True
            except Exception:
                continue

        if not loaded_any:
            raise RuntimeError(
                f"사용 가능한 Chroma 컬렉션 없음 at {self.index_dir} "
                f"(탐지된 컬렉션: {col_names})"
            )

    # --------- public ---------
    def retrieve(self, query: str, k: int = 3) -> List[dict]:
        """모든 등록 컬렉션에서 검색 후 상위 k 반환.

        병합 규칙: 각 컬렉션의 top-1 을 먼저 확보(다양성),
        나머지 슬롯은 전체 score 순으로 채움.
        """
        self._load()
        if not query or not query.strip():
            return []

        per_k = max(k, self.per_collection_k)
        per_collection: dict[str, List[dict]] = {}
        for name, store in self._stores.items():
            try:
                pairs = store.similarity_search_with_score(query, k=per_k)
            except Exception:
                continue
            bucket: List[dict] = []
            for doc, distance in pairs:
                d = float(distance)
                score = max(0.0, 1.0 - d / 2.0)
                meta = dict(doc.metadata) if doc.metadata else {}
                bucket.append({
                    "collection": name,
                    "category":   meta.get("category", name),
                    "title":      meta.get("title", ""),
                    "link":       meta.get("link") or meta.get("url", ""),
                    "source":     meta.get("source", ""),
                    "disease":    meta.get("disease", ""),
                    "section":    meta.get("section", ""),
                    "text":       doc.page_content,
                    "score":      score,
                })
            bucket.sort(key=lambda x: x["score"], reverse=True)
            per_collection[name] = bucket

        seen: set = set()
        merged: List[dict] = []

        def _push(h: dict) -> bool:
            key = h["text"][:200]
            if key in seen:
                return False
            seen.add(key)
            merged.append(h)
            return True

        # 1) 각 컬렉션 top-1 우선 (다양성)
        for name in self._stores:
            bucket = per_collection.get(name) or []
            if bucket and len(merged) < k:
                _push(bucket[0])

        # 2) 나머지 슬롯은 score 순
        remaining = []
        for name, bucket in per_collection.items():
            remaining.extend(bucket[1:])
        remaining.sort(key=lambda x: x["score"], reverse=True)
        for h in remaining:
            if len(merged) >= k:
                break
            _push(h)

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:k]

    def format_for_prompt(self, hits: List[dict]) -> str:
        """검색 결과를 LLM 시스템 메시지용 블록으로 포맷."""
        if not hits:
            return ""

        lines = [
            "다음은 어르신 돌봄 참고용 건강 정보입니다. "
            "아래 규칙을 반드시 지키세요:",
            "- 판매·광고성 문구(구매, 할인, 영양제 추천, 제품명)는 절대 쓰지 마세요.",
            "- 제품/브랜드/병원명을 구체적으로 언급하지 마세요.",
            "- 참고자료의 일반 건강 지식만 활용하세요. 진단·처방은 금지.",
            "- 자료에 없는 내용은 \"잘 모르겠습니다\"라고 솔직히 말하거나, "
            "보건소·보호자 연락을 권유하세요.",
            "",
            "<참고자료>",
        ]
        for i, h in enumerate(hits, 1):
            text = (h.get("text") or "").strip()
            if len(text) > self.max_chars_per_chunk:
                text = text[: self.max_chars_per_chunk] + "…"
            # disease 메타가 있으면 질환백과, 없으면 블로그
            if h.get("disease"):
                header = (f"[{i}] (질환백과/{h.get('category', '')}) "
                          f"{h['disease']} — {h.get('section', '')}")
            else:
                header = f"[{i}] ({h.get('category', '')}) {h.get('title', '')}"
            lines.append(header)
            lines.append(text)
            lines.append("")
        lines.append("</참고자료>")
        return "\n".join(lines)
