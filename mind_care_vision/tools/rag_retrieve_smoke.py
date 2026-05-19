"""rag_retrieve_smoke.py — bge-m3 1024d 인덱스 retrieve 검증.

한국어 의료 쿼리 5개로 chroma 두 컬렉션에서 top-3 retrieve 후 결과 출력.
컬렉션 차원/개수, dim 1024 확인까지 함께.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mind_care_vision.rag import RagRetriever

INDEX_DIR = os.environ.get("INDEX_DIR", str(Path.home() / "마음돌봄/med_data/chroma_db"))
MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
DEVICE = os.environ.get("DEVICE", "cuda")

QUERIES = [
    "무릎이 아프고 계단 오르기가 힘들어요",
    "가슴이 답답하고 숨이 차요",
    "혈당이 자꾸 높게 나와요",
    "기억이 자꾸 깜빡거려요",
    "눈이 침침하고 시야가 흐려져요",
]


def main() -> int:
    print(f"[CONFIG] index_dir = {INDEX_DIR}")
    print(f"[CONFIG] model     = {MODEL_NAME}")
    print(f"[CONFIG] device    = {DEVICE}")

    retriever = RagRetriever(
        index_dir=INDEX_DIR,
        model_name=MODEL_NAME,
        device=DEVICE,
        per_collection_k=3,
    )

    import chromadb
    client = chromadb.PersistentClient(path=INDEX_DIR)
    cols = client.list_collections()
    print(f"[CHROMA] collections detected = {[c.name for c in cols]}")
    for c in cols:
        cnt = c.count()
        peek = c.peek(1)
        embs = peek.get("embeddings")
        if embs is not None and len(embs) > 0:
            dim = len(embs[0])
        else:
            dim = "?"
        print(f"  - {c.name}: {cnt} docs, dim={dim}")

    print()
    for i, q in enumerate(QUERIES, 1):
        print(f"--- Q{i}: {q} ---")
        hits = retriever.retrieve(q, k=3)
        if not hits:
            print("  (no hits)")
            continue
        for j, h in enumerate(hits, 1):
            text = (h.get("text") or "").strip().replace("\n", " ")
            snippet = text[:120] + ("..." if len(text) > 120 else "")
            disease = h.get("disease") or ""
            section = h.get("section") or ""
            tag = f"[{h['collection']}]"
            if disease:
                tag += f" {disease}"
            if section:
                tag += f" · {section}"
            print(f"  {j}. score={h['score']:.4f} {tag}")
            print(f"     {snippet}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
