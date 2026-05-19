"""build_chroma_by_category.py

med_data/ 하위 카테고리 디렉터리(17개)를 각각 별도 Chroma 컬렉션으로 저장.

  {category}/*.json  → 아산병원 질환백과 (subsection 단위 분할)
  {category}/*.jsonl → 블로그 포스트 (row 단위, 광고 필터 적용)

컬렉션 이름 = 디렉터리명 (예: cardiovascular, neurology, mental_health …)

사용:
  .venv-ros/bin/python tools/build_chroma_by_category.py
  RESET=1          : 기존 컬렉션 삭제 후 재빌드
  CATEGORY=cardiovascular : 특정 카테고리 1개만 처리
  EMBED_MODEL=...  : 임베딩 모델 변경 (기본 paraphrase-multilingual-MiniLM-L12-v2)
  BATCH_SIZE=64    : 임베딩 배치 크기
  ADD_BATCH=512    : Chroma add 배치 크기
"""
from __future__ import annotations  # JP5 Py3.8 PEP 585 호환


import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR  = Path(os.environ.get("DATA_DIR",  str(Path.home() / "마음돌봄/med_data")))
CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", str(DATA_DIR / "chroma_db")))
MODEL_NAME = os.environ.get(
    "EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
BATCH_SIZE       = int(os.environ.get("BATCH_SIZE", 64))
ADD_BATCH        = int(os.environ.get("ADD_BATCH", 512))
MAX_SEQ_LEN      = int(os.environ.get("MAX_SEQ_LEN", 512))
RESET            = os.environ.get("RESET", "0") == "1"
ONLY_CATEGORY    = os.environ.get("CATEGORY", "").strip()
MIN_SECTION_CHARS = int(os.environ.get("MIN_SECTION_CHARS", 30))
MIN_DESC_CHARS   = int(os.environ.get("MIN_DESC_CHARS", 30))

# ---------------------------------------------------------------------------
# JSON (질환백과) 파싱
# ---------------------------------------------------------------------------
_FRONT_URL_RE     = re.compile(r"^url:\s*(\S+)",    re.MULTILINE)
_FRONT_SOURCE_RE  = re.compile(r"^source:\s*(.+)$", re.MULTILINE)
_FRONT_DISEASE_RE = re.compile(r"^disease:\s*(.+)$",re.MULTILINE)


def _parse_frontmatter(content: str) -> dict:
    info: dict = {}
    content = content.lstrip("﻿")
    for pattern, key in [(_FRONT_URL_RE, "url"), (_FRONT_SOURCE_RE, "source"),
                         (_FRONT_DISEASE_RE, "disease")]:
        m = pattern.search(content)
        if m:
            info[key] = m.group(1).strip()
    return info


def _walk(node: dict, parent_title: str = "") -> Iterable[dict]:
    title   = str(node.get("title")   or "").strip()
    content = str(node.get("content") or "").strip()
    if content:
        yield {"section": title or parent_title,
               "level":   int(node.get("level") or 0),
               "content": content}
    for child in node.get("sections") or []:
        yield from _walk(child, parent_title=title or parent_title)


def iter_disease_docs(path: Path, category: str) -> Iterable["Document"]:
    """JSON 1개 → subsection 단위 Document 목록."""
    from langchain_core.documents import Document

    try:
        with open(path, encoding="utf-8-sig") as f:
            obj = json.load(f)
    except Exception as e:
        print(f"  [WARN] {path.name}: {e}")
        return

    disease = str(obj.get("title") or path.stem).strip()
    front   = _parse_frontmatter(str(obj.get("content") or ""))
    url     = front.get("url", "")
    source  = front.get("source", "Asan Medical Center")

    for top in obj.get("sections") or []:
        for sub in top.get("sections") or []:
            for item in _walk(sub):
                content = item["content"]
                if len(content) < MIN_SECTION_CHARS:
                    continue
                if "\n" not in content and len(content) < 60 and " " not in content:
                    continue  # 이름 한 줄 stub 제거
                section = item["section"] or "본문"
                yield Document(
                    page_content=f"[{disease}] {section}\n\n{content}",
                    metadata={
                        "disease":  disease,
                        "section":  section,
                        "level":    item["level"],
                        "url":      url,
                        "source":   source,
                        "file":     path.name,
                        "category": category,
                        "title":    f"{disease} — {section}",
                        "link":     url,
                    },
                )


# ---------------------------------------------------------------------------
# JSONL (블로그) 파싱 + 광고 필터
# ---------------------------------------------------------------------------
_STRONG_AD = ["해외직구", "공구 진행", "판매 링크", "쇼핑몰", "네이버 스토어",
               "쿠팡", "오픈마켓", "공식몰", "구독 신청"]
_WEAK_AD   = ["구매", "직구", "할인", "쿠폰", "이벤트", "공동구매",
               "영양제", "보충제", "건강기능식품", "제품 후기", "특가",
               "무료 배송", "홍보", "광고",
               "문의 주세요", "상담 문의", "예약 문의", "가격 문의"]
_PHONE_RE  = re.compile(r"0\d{1,2}[\-\s]?\d{3,4}[\-\s]?\d{4}")
_URL_RE    = re.compile(r"https?://\S+")


def _is_ad(title: str, desc: str) -> bool:
    if len(desc) < MIN_DESC_CHARS:
        return True
    text = f"{title} {desc}"
    if any(kw in text for kw in _STRONG_AD):
        return True
    if sum(kw in text for kw in _WEAK_AD) >= 2:
        return True
    if _PHONE_RE.search(text):
        return True
    if len(_URL_RE.findall(desc)) >= 2:
        return True
    if sum(text.count(w) for w in ["구매", "복용", "추천", "영양제", "제품"]) >= 4:
        return True
    return False


def iter_blog_docs(path: Path, category: str) -> Iterable["Document"]:
    """JSONL 1개 → 광고 필터 후 Document 목록."""
    from langchain_core.documents import Document

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [WARN] {path.name}:{i} {e}")
                continue
            title = str(obj.get("title")       or "").strip()
            desc  = str(obj.get("description") or "").strip()
            link  = str(obj.get("link")        or "").strip()
            if not title and not desc:
                continue
            if _is_ad(title, desc):
                continue
            yield Document(
                page_content=f"[{category}] {title}\n\n{desc}",
                metadata={
                    "category": category,
                    "title":    title[:300],
                    "link":     link,
                    "source":   path.name,
                },
            )


# ---------------------------------------------------------------------------
# Chroma upsert
# ---------------------------------------------------------------------------
def upsert_collection(collection_name: str, docs: list, embedder) -> int:
    from langchain_chroma import Chroma

    if not docs:
        print(f"  [SKIP] no docs for collection '{collection_name}'")
        return 0

    if RESET:
        try:
            tmp = Chroma(collection_name=collection_name,
                         persist_directory=str(CHROMA_DIR),
                         embedding_function=embedder)
            tmp.delete_collection()
            print(f"  [RESET] '{collection_name}' deleted")
        except Exception as e:
            print(f"  [RESET] skip ({e})")

    store = None
    t0 = time.time()
    for i in range(0, len(docs), ADD_BATCH):
        batch = docs[i:i + ADD_BATCH]
        if store is None:
            store = Chroma.from_documents(
                batch, embedding=embedder,
                collection_name=collection_name,
                persist_directory=str(CHROMA_DIR),
            )
        else:
            store.add_documents(batch)
        done = min(i + ADD_BATCH, len(docs))
        elapsed = time.time() - t0
        rate = done / max(elapsed, 0.1)
        eta  = (len(docs) - done) / max(rate, 0.1)
        print(f"    [{done}/{len(docs)}] {elapsed:.0f}s elapsed, "
              f"{rate:.1f} docs/s, ETA {eta:.0f}s", flush=True)

    return len(docs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # 카테고리 목록 = DATA_DIR 직속 서브디렉터리 (chroma_db 제외)
    skip_dirs = {"chroma_db", ".git"}
    categories = sorted(
        d for d in DATA_DIR.iterdir()
        if d.is_dir() and d.name not in skip_dirs
    )
    if ONLY_CATEGORY:
        categories = [d for d in categories if d.name == ONLY_CATEGORY]
        if not categories:
            print(f"[ERROR] CATEGORY='{ONLY_CATEGORY}' not found", file=sys.stderr)
            sys.exit(1)

    print(f"[CONFIG] DATA_DIR  = {DATA_DIR}")
    print(f"[CONFIG] CHROMA    = {CHROMA_DIR}")
    print(f"[CONFIG] MODEL     = {MODEL_NAME}")
    print(f"[CONFIG] RESET     = {RESET}")
    print(f"[CONFIG] categories ({len(categories)}): {[d.name for d in categories]}\n")

    from langchain_huggingface import HuggingFaceEmbeddings

    print(f"[EMBED] loading {MODEL_NAME} ...")
    embedder = HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True, "batch_size": BATCH_SIZE},
    )
    _st = getattr(embedder, "_client", None) or getattr(embedder, "client", None)
    if _st is not None and hasattr(_st, "max_seq_length"):
        _st.max_seq_length = MAX_SEQ_LEN
        print(f"[EMBED] max_seq_length = {_st.max_seq_length}\n")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    grand_total = 0
    stats_all   = {}
    t_global    = time.time()

    for cat_dir in categories:
        cat = cat_dir.name
        print(f"{'='*60}")
        print(f"[CAT] {cat}")

        docs = []

        # --- JSON (질환백과) ---
        json_files = sorted(
            p for p in cat_dir.glob("*.json")
            if "Zone.Identifier" not in p.name
        )
        for path in json_files:
            docs.extend(iter_disease_docs(path, cat))

        # --- JSONL (블로그) ---
        for path in sorted(cat_dir.glob("*.jsonl")):
            docs.extend(iter_blog_docs(path, cat))

        print(f"  docs to embed: {len(docs)}")
        n = upsert_collection(cat, docs, embedder)
        grand_total += n
        stats_all[cat] = n
        print(f"  [DONE] {cat}: {n} docs\n")

    elapsed = time.time() - t_global
    print(f"{'='*60}")
    print(f"[SUMMARY] {grand_total} docs total in {elapsed:.0f}s")
    for cat, n in stats_all.items():
        print(f"  {cat:<30} {n:>5} docs")

    stats_path = CHROMA_DIR / "_build_stats_by_category.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({
            "model":      MODEL_NAME,
            "chroma_dir": str(CHROMA_DIR),
            "categories": stats_all,
            "total_docs": grand_total,
            "duration_s": round(elapsed, 1),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n[STATS] saved → {stats_path}")

    # --- Smoke test (첫 번째 카테고리) ---
    if categories:
        from langchain_chroma import Chroma
        first = categories[0].name
        try:
            store = Chroma(collection_name=first,
                           persist_directory=str(CHROMA_DIR),
                           embedding_function=embedder)
            for q in ("무릎이 아파요", "치매 초기 증상", "고혈압 관리"):
                hits = store.similarity_search(q, k=2)
                if hits:
                    print(f"\n[SMOKE '{first}'] '{q}' → {hits[0].page_content[:80]}")
        except Exception as e:
            print(f"[SMOKE] {e}")


if __name__ == "__main__":
    main()
