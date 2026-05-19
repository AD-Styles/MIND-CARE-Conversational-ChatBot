"""build_chroma_index.py

~/마음돌봄/med_data/*.jsonl 15개 파일을 읽어
각 JSON 객체(총 ~15k개)를 langchain Document로 변환하고
Chroma(persistent) 벡터스토어에 임베딩하여 저장한다.

JSONL 스키마: {title, description, link}

출력:
  ~/마음돌봄/med_data/chroma_db/  (Chroma persistent)
    collection 이름: med_blog
  ~/마음돌봄/med_data/chroma_db/_build_stats.json

사용:
  ~/마음돌봄/.venv-ros/bin/python tools/build_chroma_index.py
  RESET=1 ...                  : 기존 collection 삭제 후 재빌드
  EMBED_MODEL=... ...          : 임베딩 모델 변경
  COLLECTION=... ...           : 컬렉션 이름 변경

필터 (광고성/부적합 판정):
  - description 이 너무 짧음 (< 30자)
  - 강한 광고 키워드 1개 이상 (해외직구, 판매 링크 등)
  - 약한 광고 키워드 2개 이상
  - 전화번호 패턴
  - 판매 유도 어휘 누적

짧은 doc(title+desc)이라 chunking은 생략 — 각 row = 1 Document.
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
COLLECTION = os.environ.get("COLLECTION", "med_blog")
# MiniLM 384dim — WSL CPU 빠름 (~10분). Xavier 이전 후 BAAI/bge-m3 (1024d) 로 재빌드.
# bge-m3 는 max_seq_length 512 권장, langchain v2 의 _client 속성으로 설정.
MODEL_NAME = os.environ.get(
    "EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 64))
RESET = os.environ.get("RESET", "0") == "1"
MIN_DESC_CHARS = int(os.environ.get("MIN_DESC_CHARS", 30))

# 파일명 → 한글 카테고리 매핑
CATEGORY_MAP = {
    "angina_pectoris_blog_data": "협심증",
    "bone_blog_data": "근골격",
    "brain_blog_data": "뇌졸중",
    "cholesterol_blog_data": "콜레스테롤",
    "dementia_blog_data": "치매",
    "diabetes_blog_data": "당뇨",
    "eye_blog_data": "안과",
    "health_info_blog_data": "건강칼럼",
    "heart_blog_data": "심근경색",
    "hypertension_blog_data": "고혈압",
    "lng_disorder_blog_data": "언어장애",
    "mental_blog_data": "정신건강",
    "osteoporosis_blog_data": "골다공증",
    "stroke_blog_data": "뇌출혈",
    "urology_blog_data": "비뇨기",
}

STRONG_AD_KEYWORDS = [
    "해외직구", "공구 진행", "판매 링크", "쇼핑몰", "네이버 스토어",
    "쿠팡", "오픈마켓", "공식몰", "구독 신청",
]
AD_KEYWORDS = [
    "구매", "직구", "할인", "쿠폰", "이벤트", "공동구매",
    "영양제", "보충제", "건강기능식품", "제품 후기", "특가",
    "무료 배송", "홍보", "광고",
    "문의 주세요", "상담 문의", "예약 문의", "가격 문의",
]

PHONE_RE = re.compile(r"0\d{1,2}[\-\s]?\d{3,4}[\-\s]?\d{4}")
URL_RE = re.compile(r"https?://\S+")


def is_ad(title: str, desc: str) -> tuple[bool, str]:
    text = f"{title} {desc}"
    if len(desc) < MIN_DESC_CHARS:
        return True, "too_short"
    for kw in STRONG_AD_KEYWORDS:
        if kw in text:
            return True, f"strong({kw})"
    weak = [kw for kw in AD_KEYWORDS if kw in text]
    if len(weak) >= 2:
        return True, f"weak({len(weak)})"
    if PHONE_RE.search(text):
        return True, "phone"
    if len(URL_RE.findall(desc)) >= 2:
        return True, "urls"
    # 판매 어휘 누적
    sell = sum(text.count(w) for w in ["구매", "복용", "추천", "영양제", "제품"])
    if sell >= 4:
        return True, f"sell_heavy({sell})"
    return False, ""


def iter_jsonl(path: Path) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [WARN] {path.name}:{i} invalid JSON — {e}")


def main() -> None:
    print(f"[CONFIG] DATA_DIR  = {DATA_DIR}")
    print(f"[CONFIG] CHROMA    = {CHROMA_DIR}  (collection={COLLECTION})")
    print(f"[CONFIG] MODEL     = {MODEL_NAME}")
    print(f"[CONFIG] RESET     = {RESET}")

    files = sorted(DATA_DIR.rglob("*.jsonl"))  # 카테고리 하위까지
    if not files:
        print(f"[ERROR] no JSONL in {DATA_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"[INPUT] {len(files)} JSONL files")

    # langchain imports (초기 import 오래 걸릴 수 있음)
    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    docs: list[Document] = []
    total = 0
    kept = 0
    reasons: dict[str, int] = {}

    for path in files:
        stem = path.stem
        category = CATEGORY_MAP.get(stem, stem)
        per_file = 0
        for obj in iter_jsonl(path):
            total += 1
            title = str(obj.get("title") or "").strip()
            desc = str(obj.get("description") or "").strip()
            link = str(obj.get("link") or "").strip()
            if not title and not desc:
                reasons["empty"] = reasons.get("empty", 0) + 1
                continue
            ad, why = is_ad(title, desc)
            if ad:
                key = why.split("(")[0]
                reasons[key] = reasons.get(key, 0) + 1
                continue

            page_content = f"[{category}] {title}\n\n{desc}"
            doc = Document(
                page_content=page_content,
                metadata={
                    "category": category,
                    "title": title[:300],
                    "link": link,
                    "source": path.name,
                },
            )
            docs.append(doc)
            kept += 1
            per_file += 1
        print(f"  {path.name}: kept {per_file} docs")

    print(f"\n[FILTER] {total} rows → {kept} kept ({total - kept} filtered)")
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  - {k}: {v}")
    if not docs:
        print("[ERROR] no documents to embed", file=sys.stderr)
        sys.exit(1)

    # --- Embeddings ---
    # bge-m3 의 기본 max_seq_length=8192 면 CPU 추론이 6 s/doc 까지 느려짐.
    # 의료 블로그 description 은 평균 100-300 자 (≈ 50-150 token) 이므로
    # 512 토큰으로 truncate 해도 의미 손실 거의 없고 5-10x 가속.
    MAX_SEQ_LEN = int(os.environ.get("MAX_SEQ_LEN", 512))
    DEVICE = os.environ.get("DEVICE", "cuda")
    print(f"\n[EMBED] loading {MODEL_NAME} on {DEVICE} (max_seq_len={MAX_SEQ_LEN}) ...")
    embedder = HuggingFaceEmbeddings(
        model_name=MODEL_NAME,
        model_kwargs={"device": DEVICE},
        encode_kwargs={"normalize_embeddings": True, "batch_size": BATCH_SIZE},
    )
    # SentenceTransformer 인스턴스에 직접 max_seq_length 적용
    # langchain_huggingface 가 pydantic v2 로 가면서 client → _client 변경
    _st = getattr(embedder, "_client", None) or getattr(embedder, "client", None)
    if _st is not None and hasattr(_st, "max_seq_length"):
        _st.max_seq_length = MAX_SEQ_LEN
        print(f"[EMBED] max_seq_length applied = {_st.max_seq_length}")
    else:
        print("[WARN] could not access SentenceTransformer to set max_seq_length")

    # --- Chroma ---
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    if RESET:
        # 기존 컬렉션 삭제
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

    # 대량 add를 청크 단위로 — 메모리 안전
    ADD_BATCH = int(os.environ.get("ADD_BATCH", 512))
    store: Chroma | None = None
    for i in range(0, len(docs), ADD_BATCH):
        batch = docs[i:i + ADD_BATCH]
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
        print(f"  [{done}/{len(docs)}] batch {b_dur:.0f}s, total {elapsed:.0f}s, "
              f"{rate:.1f} docs/s, ETA {eta:.0f}s", flush=True)

    dur = time.time() - t0
    print(f"\n[DONE] {len(docs)} docs in {dur:.0f}s "
          f"({len(docs)/max(dur,0.1):.1f} docs/s)")

    stats = {
        "model": MODEL_NAME,
        "collection": COLLECTION,
        "persist_dir": str(CHROMA_DIR),
        "total_rows": total,
        "kept": kept,
        "filter_reasons": reasons,
        "embed_duration_s": round(dur, 1),
    }
    with open(CHROMA_DIR / "_build_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # --- Smoke test ---
    if store is not None:
        try:
            probe = store.similarity_search("무릎이 아파요", k=3)
            print(f"\n[SMOKE] query='무릎이 아파요' → {len(probe)} hits")
            for i, d in enumerate(probe, 1):
                meta = d.metadata
                head = d.page_content.split("\n", 1)[0][:80]
                print(f"  [{i}] ({meta.get('category')}) {head}")
        except Exception as exc:
            print(f"[SMOKE] failed: {exc}")


if __name__ == "__main__":
    main()
