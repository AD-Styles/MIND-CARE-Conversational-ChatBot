"""build_rag_index.py

~/마음돌봄/med_data/*.csv 를 읽어 한국어 임베딩 + FAISS 인덱스를 구축한다.
광고성 블로그 행은 휴리스틱 필터로 제거한다.

출력:
  INDEX_DIR/
    index.faiss      : FAISS IndexFlatIP (정규화 벡터 → 코사인 유사도)
    meta.jsonl       : 각 벡터에 대응하는 메타데이터 (category, title, link, text)
    stats.json       : 빌드 통계 (필터/청크 수)

사용:
  ~/마음돌봄/.venv-ros/bin/python tools/build_rag_index.py
  # 옵션
  DATA_DIR=/path/to/csvs INDEX_DIR=/path/to/out \\
      ~/마음돌봄/.venv-ros/bin/python tools/build_rag_index.py

필터 규칙 (광고성/부적합 판정):
  - 판매 유도 키워드 3개 이상 동시 등장
  - 블로그/쇼핑 URL 본문 내 3개 이상
  - 전화번호·병원 주소 반복 (5회 이상)
  - 내용이 너무 짧음 (< 200자)

청킹:
  - full_content 를 600자 단위, 100자 overlap
  - title + description 은 첫 청크에 prefix
"""
from __future__ import annotations  # JP5 Py3.8 PEP 585 호환


import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path.home() / "마음돌봄/med_data")))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(Path.home() / "마음돌봄/rag_index")))
# 기본은 속도 우선 — MiniLM 다국어(384dim, 120MB, CPU에서 빠름).
# 품질 더 원하면 EMBED_MODEL=jhgan/ko-sroberta-multitask 로 오버라이드.
MODEL_NAME = os.environ.get(
    "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 900))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 120))
MIN_CHARS = int(os.environ.get("MIN_CHARS", 200))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 64))

# -------- 광고성 필터 --------
# 판매 유도 어휘 — 하나만 있으면 괜찮지만 여러 개 겹치면 홍보성
AD_KEYWORDS = [
    "구매", "직구", "할인", "쿠폰", "이벤트", "공동구매",
    "영양제", "보충제", "건강기능식품", "제품 후기", "구독",
    "홍보", "광고", "추천 상품", "특가", "무료 배송",
    "홈페이지", "공식몰", "블로그 댓글", "문의 주세요",
    "상담 문의", "예약 문의", "가격 문의",
]

# 강한 광고 신호: 하나만 있어도 바로 탈락
STRONG_AD_KEYWORDS = [
    "해외직구", "공구 진행", "판매 링크", "결제", "입금",
    "쇼핑몰", "네이버 스토어", "쿠팡", "오픈마켓",
]

PHONE_RE = re.compile(r"0\d{1,2}[\-\s]?\d{3,4}[\-\s]?\d{4}")
URL_RE = re.compile(r"https?://\S+")


def is_ad_row(title: str, desc: str, content: str) -> tuple[bool, str]:
    """(광고성 여부, 이유) 반환."""
    text = f"{title} {desc} {content}"

    if len(content) < MIN_CHARS:
        return True, f"too_short({len(content)})"

    # 강한 신호 — 1개로 탈락
    for kw in STRONG_AD_KEYWORDS:
        if kw in text:
            return True, f"strong_ad({kw})"

    # 약한 신호 — 3개 이상 겹치면 탈락
    weak_hits = [kw for kw in AD_KEYWORDS if kw in text]
    if len(weak_hits) >= 3:
        return True, f"weak_ad({'/'.join(weak_hits[:4])})"

    # URL 다수 — 블로그 간 링크 교환형 광고
    urls = URL_RE.findall(content)
    if len(urls) >= 3:
        return True, f"many_urls({len(urls)})"

    # 전화번호 반복 — 병원 홍보성
    phones = PHONE_RE.findall(content)
    if len(phones) >= 2:
        return True, f"phone_repeat({len(phones)})"

    # "구매"·"복용"·"추천"이 치료 용어보다 자주 언급되는 경우
    sell_count = sum(text.count(w) for w in ["구매", "복용", "추천", "영양제", "제품"])
    if sell_count >= 8:
        return True, f"sell_heavy({sell_count})"

    return False, ""


# -------- 청킹 --------
def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]


def category_from_filename(path: Path) -> str:
    return path.stem  # e.g. "치매"


# -------- 메인 --------
def main():
    print(f"[CONFIG] DATA_DIR  = {DATA_DIR}")
    print(f"[CONFIG] INDEX_DIR = {INDEX_DIR}")
    print(f"[CONFIG] MODEL     = {MODEL_NAME}")
    print(f"[CONFIG] chunk={CHUNK_SIZE} overlap={CHUNK_OVERLAP} min={MIN_CHARS}")

    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print(f"[ERROR] no CSV files in {DATA_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"[INPUT] {len(csv_files)} CSV files")

    total_rows = 0
    kept_rows = 0
    filtered_reasons: dict[str, int] = {}
    chunks: list[dict] = []

    for path in csv_files:
        cat = category_from_filename(path)
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="utf-8")

        for _, row in df.iterrows():
            total_rows += 1
            title = str(row.get("title", "") or "").strip()
            desc = str(row.get("description", "") or "").strip()
            content = str(row.get("full_content", "") or "").strip()
            link = str(row.get("link", "") or "").strip()

            if not content:
                filtered_reasons["empty"] = filtered_reasons.get("empty", 0) + 1
                continue

            is_ad, reason = is_ad_row(title, desc, content)
            if is_ad:
                key = reason.split("(")[0]
                filtered_reasons[key] = filtered_reasons.get(key, 0) + 1
                continue

            kept_rows += 1
            # 첫 청크는 title/desc prefix로 문맥 강화
            prefix = f"[{cat}] {title}\n"
            if desc:
                prefix += f"요약: {desc}\n\n"

            pieces = chunk_text(content, CHUNK_SIZE, CHUNK_OVERLAP)
            for i, piece in enumerate(pieces):
                text = (prefix + piece) if i == 0 else f"[{cat}] {title}\n\n{piece}"
                chunks.append({
                    "category": cat,
                    "title": title[:200],
                    "link": link,
                    "chunk_id": i,
                    "text": text,
                })
        print(f"  {path.name}: kept rows so far={kept_rows}")

    print(f"\n[FILTER] {total_rows} rows → {kept_rows} kept "
          f"({total_rows - kept_rows} filtered)")
    for k, v in sorted(filtered_reasons.items(), key=lambda x: -x[1]):
        print(f"  - {k}: {v}")
    print(f"[CHUNK ] total chunks = {len(chunks)}")

    if not chunks:
        print("[ERROR] no chunks to index", file=sys.stderr)
        sys.exit(1)

    # -------- 임베딩 --------
    print(f"\n[EMBED] loading {MODEL_NAME} ...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME, device="cpu")
    dim = model.get_sentence_embedding_dimension()
    print(f"[EMBED] dim={dim}, batch={BATCH_SIZE}")

    texts = [c["text"] for c in chunks]
    t0 = time.time()
    embs = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype("float32")
    print(f"[EMBED] {len(embs)} vectors in {time.time()-t0:.1f}s")

    # -------- FAISS --------
    import faiss

    index = faiss.IndexFlatIP(dim)
    index.add(embs)
    print(f"[FAISS] index.ntotal = {index.ntotal}")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_DIR / "index.faiss"))

    with open(INDEX_DIR / "meta.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    with open(INDEX_DIR / "stats.json", "w", encoding="utf-8") as f:
        json.dump({
            "model": MODEL_NAME,
            "dim": dim,
            "total_rows": total_rows,
            "kept_rows": kept_rows,
            "chunks": len(chunks),
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "filtered_reasons": filtered_reasons,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] {INDEX_DIR}")
    print(f"  index.faiss : {(INDEX_DIR / 'index.faiss').stat().st_size / 1e6:.1f} MB")
    print(f"  meta.jsonl  : {(INDEX_DIR / 'meta.jsonl').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
