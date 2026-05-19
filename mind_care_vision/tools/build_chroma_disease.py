"""build_chroma_disease.py

~/마음돌봄/med_data/*.json (1,300여개) — 아산병원 질환 백과 —
각 파일을 subsection 단위로 잘라 Chroma 컬렉션 `med_disease` 에 저장.

JSON 스키마:
  {
    "content": "---\\ndisease: ...\\nsource: Asan Medical Center\\nurl: ...\\n---",
    "sections": [ { "title": "...", "level": 1, "sections": [
        { "title": "증상",  "level": 2, "content": "..." },
        { "title": "원인",  "level": 2, "content": "..." },
        { "title": "치료",  "level": 2, "content": "..." },
        ...
    ] } ],
    "title": "질환명"
  }

각 subsection = 1 Document:
  page_content = "[{질환명}] {섹션}\\n\\n{content}"
  metadata     = {disease, section, level, url, source, file}

출력:
  ~/마음돌봄/med_data/chroma_db/   (persistent, 기존 med_blog 와 공존)
    collection: med_disease

환경변수:
  DATA_DIR, CHROMA_DIR, COLLECTION (기본 med_disease), RESET=1
  MIN_SECTION_CHARS (기본 30) — stub 섹션 제거
"""

from __future__ import annotations  # JP5/Python 3.8: PEP 585 type-hints 지연 평가

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path.home() / "마음돌봄/med_data")))
CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", str(DATA_DIR / "chroma_db")))
COLLECTION = os.environ.get("COLLECTION", "med_disease")
# MiniLM 384dim — WSL CPU 빠름. Xavier 이전 후 BAAI/bge-m3 로 교체 + 재빌드 예정.
MODEL_NAME = os.environ.get(
    "EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 64))
ADD_BATCH = int(os.environ.get("ADD_BATCH", 512))
RESET = os.environ.get("RESET", "0") == "1"
MIN_SECTION_CHARS = int(os.environ.get("MIN_SECTION_CHARS", 30))

FRONT_URL_RE = re.compile(r"^url:\s*(\S+)", re.MULTILINE)
FRONT_SOURCE_RE = re.compile(r"^source:\s*(.+)$", re.MULTILINE)
FRONT_DISEASE_RE = re.compile(r"^disease:\s*(.+)$", re.MULTILINE)


def parse_frontmatter(content: str) -> dict:
    """`content` 필드(\ufeff + YAML-ish frontmatter) → dict."""
    info = {}
    if not content:
        return info
    # BOM 제거
    content = content.lstrip("\ufeff")
    m = FRONT_URL_RE.search(content)
    if m:
        info["url"] = m.group(1).strip()
    m = FRONT_SOURCE_RE.search(content)
    if m:
        info["source"] = m.group(1).strip()
    m = FRONT_DISEASE_RE.search(content)
    if m:
        info["disease"] = m.group(1).strip()
    return info


def iter_subsections(obj: dict) -> Iterable[dict]:
    """sections 트리를 재귀 순회해 content 있는 leaf-level subsection 방출."""
    for top in obj.get("sections") or []:
        # 최상위 level=1 은 질환명 래퍼 — 스킵
        subs = top.get("sections") or []
        for sub in subs:
            # 더 깊이 들어갈 수도 있으므로 재귀
            yield from _walk(sub)


def _walk(node: dict, parent_title: str = "") -> Iterable[dict]:
    title = str(node.get("title") or "").strip()
    content = str(node.get("content") or "").strip()
    if content:
        yield {
            "section": title or parent_title,
            "level": int(node.get("level") or 0),
            "content": content,
        }
    for child in node.get("sections") or []:
        yield from _walk(child, parent_title=title or parent_title)


def main() -> None:
    print(f"[CONFIG] DATA_DIR  = {DATA_DIR}")
    print(f"[CONFIG] CHROMA    = {CHROMA_DIR}  (collection={COLLECTION})")
    print(f"[CONFIG] MODEL     = {MODEL_NAME}")
    print(f"[CONFIG] RESET     = {RESET}")
    print(f"[CONFIG] MIN_SECTION_CHARS = {MIN_SECTION_CHARS}")

    # *.json (단, *Zone.Identifier 제외)
    files = sorted(
        p for p in DATA_DIR.rglob("*.json")
        if "Zone.Identifier" not in p.name
    )
    if not files:
        print(f"[ERROR] no JSON files in {DATA_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"[INPUT] {len(files)} disease JSON files")

    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    docs: list[Document] = []
    total_subs = 0
    kept = 0
    skip_reasons: dict[str, int] = {}
    bad_files = 0

    for path in files:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                obj = json.load(f)
        except Exception as e:
            print(f"  [WARN] {path.name}: JSON load failed — {e}")
            bad_files += 1
            continue

        disease = str(obj.get("title") or path.stem).strip()
        front = parse_frontmatter(str(obj.get("content") or ""))
        url = front.get("url", "")
        source = front.get("source", "Asan Medical Center")

        for sub in iter_subsections(obj):
            total_subs += 1
            section = sub["section"] or "본문"
            content = sub["content"]

            if len(content) < MIN_SECTION_CHARS:
                skip_reasons["too_short"] = skip_reasons.get("too_short", 0) + 1
                continue
            # 이름 한 줄만 들어있는 stub (예: 치료: "김대열") 제거
            if "\n" not in content and len(content) < 60 and " " not in content:
                skip_reasons["name_stub"] = skip_reasons.get("name_stub", 0) + 1
                continue

            page_content = f"[{disease}] {section}\n\n{content}"
            docs.append(Document(
                page_content=page_content,
                metadata={
                    "disease": disease,
                    "section": section,
                    "level": sub["level"],
                    "url": url,
                    "source": source,
                    "file": path.name,
                    # RagRetriever 호환 필드
                    "category": "질환백과",
                    "title": f"{disease} — {section}",
                    "link": url,
                },
            ))
            kept += 1

    print(f"\n[PARSE] files={len(files)} ({bad_files} bad) "
          f"subsections={total_subs} kept={kept}")
    for k, v in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"  - {k}: {v}")
    if not docs:
        print("[ERROR] no documents to embed", file=sys.stderr)
        sys.exit(1)

    # --- Embeddings ---
    # bge-m3 기본 max_seq_length=8192 → CPU 6 s/doc. 질환 백과 섹션 평균 800-1200 자
    # (≈ 200-400 token) 이므로 512 truncate 가 안전 + 5-10x 가속.
    MAX_SEQ_LEN = int(os.environ.get("MAX_SEQ_LEN", 512))
    DEVICE = os.environ.get("DEVICE", "cuda")
    print(f"\n[EMBED] loading {MODEL_NAME} on {DEVICE} (max_seq_len={MAX_SEQ_LEN}) ...")
    embedder = HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": DEVICE},
        encode_kwargs={"normalize_embeddings": True, "batch_size": BATCH_SIZE},
    )
    _st = getattr(embedder, "_client", None) or getattr(embedder, "client", None)
    if _st is not None and hasattr(_st, "max_seq_length"):
        _st.max_seq_length = MAX_SEQ_LEN
        print(f"[EMBED] max_seq_length applied = {_st.max_seq_length}")
    else:
        print("[WARN] could not access SentenceTransformer to set max_seq_length")

    # --- Chroma ---
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    if RESET:
        try:
            tmp = Chroma(
                collection_name=COLLECTION,
                persist_directory=str(CHROMA_DIR),
                embedding_function=embedder,
            )
            tmp.delete_collection()
            print("[CHROMA] existing collection deleted")
        except Exception as exc:
            print(f"[CHROMA] reset skipped ({exc})")

    print(f"[CHROMA] upserting {len(docs)} docs to {CHROMA_DIR}", flush=True)
    t0 = time.time()
    store: Chroma | None = None
    for i in range(0, len(docs), ADD_BATCH):
        batch = docs[i:i + ADD_BATCH]
        # batch 별로 가장 긴 doc 길이 출력 — pathological doc 진단
        max_len = max(len(d.page_content) for d in batch)
        avg_len = sum(len(d.page_content) for d in batch) / len(batch)
        b_t0 = time.time()
        print(f"  → batch {i // ADD_BATCH + 1} starting ({len(batch)} docs, "
              f"avg={avg_len:.0f} chars, max={max_len})", flush=True)
        if store is None:
            store = Chroma.from_documents(
                batch,
                embedding=embedder,
                collection_name=COLLECTION,
                persist_directory=str(CHROMA_DIR),
            )
        else:
            store.add_documents(batch)
        b_dur = time.time() - b_t0
        elapsed = time.time() - t0
        done = min(i + ADD_BATCH, len(docs))
        rate = done / max(elapsed, 0.1)
        eta = (len(docs) - done) / max(rate, 0.1)
        print(f"  [{done}/{len(docs)}] batch took {b_dur:.0f}s, "
              f"total {elapsed:.0f}s, {rate:.1f} docs/s, ETA {eta:.0f}s",
              flush=True)

    dur = time.time() - t0
    print(f"\n[DONE] {len(docs)} docs in {dur:.0f}s "
          f"({len(docs)/max(dur,0.1):.1f} docs/s)")

    stats = {
        "model": MODEL_NAME,
        "collection": COLLECTION,
        "persist_dir": str(CHROMA_DIR),
        "files": len(files),
        "bad_files": bad_files,
        "subsections": total_subs,
        "kept": kept,
        "skip_reasons": skip_reasons,
        "embed_duration_s": round(dur, 1),
    }
    with open(CHROMA_DIR / f"_build_stats_{COLLECTION}.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # Smoke test
    if store is not None:
        try:
            for q in ("무릎이 아파요", "치매 초기 증상", "고혈압 관리"):
                probe = store.similarity_search(q, k=3)
                print(f"\n[SMOKE] '{q}' → {len(probe)} hits")
                for i, d in enumerate(probe, 1):
                    head = d.page_content.split("\n", 1)[0][:80]
                    print(f"  [{i}] {head}")
        except Exception as exc:
            print(f"[SMOKE] failed: {exc}")


if __name__ == "__main__":
    main()
