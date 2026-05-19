#!/usr/bin/env bash
# download_urfdd.sh — URFDD (Univ. of Rzeszow Fall Detection Dataset) 자동 다운로드.
#
# 다운로드 패키지: cam0 RGB 만 (depth/accel 무시)
#   - 30 falls + 40 ADLs (PNG 시퀀스 zip)
#   - urfall-cam0-falls.csv (frame 단위 라벨)
#
# 디스크 사용 (다운만): ~3 GB
# 사이트: http://fenix.ur.edu.pl/~mkepski/ds/uf.html (학술 용도)
#
# 사용
#   bash download_urfdd.sh                     # ~/eval/urfdd 에
#   bash download_urfdd.sh /custom/path        # 다른 위치

set -e

DEST="${1:-$HOME/eval/urfdd}"
BASE="https://fenix.ur.edu.pl/~mkepski/ds/data"
mkdir -p "$DEST/zips"
cd "$DEST/zips"

UA="Mozilla/5.0"

echo "=== 1) frame 라벨 CSV ==="
for f in urfall-cam0-falls.csv urfall-cam0-adls.csv; do
    if [[ -f "$f" ]]; then
        echo "  cached  $f"
    else
        echo "  fetch   $f"
        curl -ksL --max-time 30 -A "$UA" -o "$f" "$BASE/$f" || true
    fi
done

echo
echo "=== 2) 30 falls ==="
for i in $(seq -f "%02g" 1 30); do
    name="fall-${i}-cam0-rgb.zip"
    if [[ -f "$name" && $(stat -c%s "$name") -gt 1000 ]]; then
        echo "  cached  $name"
        continue
    fi
    echo -n "  fetch   $name … "
    if curl -ksLf --max-time 600 -A "$UA" -o "$name" "$BASE/$name"; then
        echo "$(stat -c%s "$name") bytes"
    else
        rm -f "$name"
        echo "FAIL"
    fi
done

echo
echo "=== 3) 40 ADLs ==="
for i in $(seq -f "%02g" 1 40); do
    name="adl-${i}-cam0-rgb.zip"
    if [[ -f "$name" && $(stat -c%s "$name") -gt 1000 ]]; then
        echo "  cached  $name"
        continue
    fi
    echo -n "  fetch   $name … "
    if curl -ksLf --max-time 600 -A "$UA" -o "$name" "$BASE/$name"; then
        echo "$(stat -c%s "$name") bytes"
    else
        rm -f "$name"
        echo "FAIL"
    fi
done

echo
echo "=== 다운로드 요약 ==="
ls -la "$DEST/zips" | tail -n +2 | awk '{ s+=$5 } END { printf "  %d files, %.1f GB\n", NR-1, s/1e9 }'
echo
echo "다음: python convert_urfdd_gt.py --src \"$DEST\""
