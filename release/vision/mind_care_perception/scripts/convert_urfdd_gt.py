#!/usr/bin/env python3
"""convert_urfdd_gt.py — URFDD zip → mp4 변환 + ground truth CSV 생성.

입력 (download_urfdd.sh 가 만들어둔 것)
    <src>/zips/fall-01-cam0-rgb.zip … fall-30…
    <src>/zips/adl-01-cam0-rgb.zip … adl-40…
    <src>/zips/urfall-cam0-falls.csv

출력
    <src>/videos/fall-XX.mp4, adl-XX.mp4
    <src>/gt.csv                ← eval_fall.py 입력 형식

URFDD 라벨 코딩 (col 3 of urfall-cam0-falls.csv):
    -1  not lying
     0  temporary lying (transitional)
     1  lying on ground (fall)

GT 변환 규칙:
    fall 영상 → label=1 인 frame 들의 (min, max) frame 번호 → ÷ FPS 로 (start_ts, end_ts)
    adl 영상 → is_fall=0, start_ts/end_ts 비움
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

URFDD_FPS = 30   # 사이트 명시


def ensure_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        sys.exit("ffmpeg 미설치 — `sudo apt install -y ffmpeg` 후 재시도.")
    return p


def zip_to_video(zip_path: Path, out_mp4: Path, ffmpeg: str,
                 fps: int = URFDD_FPS, work: Path | None = None) -> None:
    """unzip → ffmpeg image2video → mp4. 작업 폴더는 반환 후 정리."""
    work = work or zip_path.parent / f"_unzip_{zip_path.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)

    # 풀린 디렉터리 안에서 PNG 시퀀스 찾기
    pngs = sorted(work.rglob("*.png"))
    if not pngs:
        shutil.rmtree(work)
        raise RuntimeError(f"PNG 시퀀스 없음: {zip_path}")

    # ffmpeg 의 -i 패턴 — 같은 디렉터리·같은 prefix 가정
    parent = pngs[0].parent
    prefix = pngs[0].stem.rsplit("-", 1)[0]   # "fall-01-cam0-rgb"
    pattern = str(parent / f"{prefix}-%03d.png")

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-framerate", str(fps), "-i", pattern,
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-r", str(fps), str(out_mp4)]
    subprocess.check_call(cmd)
    shutil.rmtree(work)


def parse_falls_csv(csv_path: Path) -> Dict[str, Tuple[int, int]]:
    """video_id → (min_fall_frame, max_fall_frame). label=1 만 사용."""
    fall_frames: Dict[str, List[int]] = defaultdict(list)
    with csv_path.open() as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            vid, frame, label = row[0], row[1], row[2]
            try:
                if int(label) == 1:
                    fall_frames[vid].append(int(frame))
            except ValueError:
                continue
    return {v: (min(fs), max(fs)) for v, fs in fall_frames.items() if fs}


def main() -> None:
    p = argparse.ArgumentParser(description="URFDD → mp4 + gt.csv 변환")
    p.add_argument("--src", type=Path, default=Path.home() / "eval" / "urfdd",
                    help="download_urfdd.sh 가 만든 디렉터리 (default ~/eval/urfdd)")
    p.add_argument("--keep-zips", action="store_true",
                    help="변환 후 zip 삭제하지 않음 (재실행 빨라짐, 디스크 ↑)")
    p.add_argument("--skip-existing", action="store_true",
                    help="이미 mp4 있는 영상 건너뜀")
    args = p.parse_args()

    ffmpeg = ensure_ffmpeg()

    src = args.src
    zips_dir = src / "zips"
    videos_dir = src / "videos"
    gt_csv = src / "gt.csv"

    falls_label_csv = zips_dir / "urfall-cam0-falls.csv"
    if not falls_label_csv.is_file():
        sys.exit(f"label CSV 없음: {falls_label_csv}\n  먼저 download_urfdd.sh 실행.")

    fall_ranges = parse_falls_csv(falls_label_csv)
    print(f"[gt] falls.csv 에서 {len(fall_ranges)} 개 fall 영상 라벨 파싱")

    # GT CSV 작성 (영상 변환 후 — 변환 실패 한 건 제외)
    gt_rows: List[Dict[str, str]] = []

    # ---- fall ----
    for fz in sorted(zips_dir.glob("fall-*-cam0-rgb.zip")):
        vid_short = fz.stem.replace("-cam0-rgb", "")   # "fall-01"
        out_mp4 = videos_dir / f"{vid_short}.mp4"
        if args.skip_existing and out_mp4.is_file():
            print(f"  skip (already): {out_mp4.name}")
        else:
            print(f"  convert {fz.name} → {out_mp4.name}")
            try:
                zip_to_video(fz, out_mp4, ffmpeg)
            except Exception as exc:
                print(f"    FAIL: {exc}")
                continue
        rng = fall_ranges.get(vid_short)
        if rng is None:
            print(f"    [warn] {vid_short} 의 fall 라벨이 CSV 에 없음 → start_ts 비움")
            gt_rows.append({"video_id": out_mp4.name, "start_ts": "",
                             "end_ts": "", "is_fall": "1"})
        else:
            start_ts = rng[0] / URFDD_FPS
            end_ts   = rng[1] / URFDD_FPS
            gt_rows.append({"video_id": out_mp4.name,
                             "start_ts": f"{start_ts:.3f}",
                             "end_ts":   f"{end_ts:.3f}",
                             "is_fall":  "1"})
        if not args.keep_zips:
            fz.unlink(missing_ok=True)

    # ---- adl ----
    for az in sorted(zips_dir.glob("adl-*-cam0-rgb.zip")):
        vid_short = az.stem.replace("-cam0-rgb", "")
        out_mp4 = videos_dir / f"{vid_short}.mp4"
        if args.skip_existing and out_mp4.is_file():
            print(f"  skip (already): {out_mp4.name}")
        else:
            print(f"  convert {az.name} → {out_mp4.name}")
            try:
                zip_to_video(az, out_mp4, ffmpeg)
            except Exception as exc:
                print(f"    FAIL: {exc}")
                continue
        gt_rows.append({"video_id": out_mp4.name, "start_ts": "",
                         "end_ts": "", "is_fall": "0"})
        if not args.keep_zips:
            az.unlink(missing_ok=True)

    # ---- gt.csv ----
    with gt_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "start_ts", "end_ts", "is_fall"])
        w.writeheader()
        w.writerows(gt_rows)

    print()
    print(f"[done] {gt_csv}  rows={len(gt_rows)}  "
          f"fall={sum(1 for r in gt_rows if r['is_fall']=='1')} "
          f"adl={sum(1 for r in gt_rows if r['is_fall']=='0')}")
    print(f"       videos: {videos_dir}")
    print()
    print("다음:")
    print(f"  python eval_fall.py \\")
    print(f"      --videos-dir {videos_dir} \\")
    print(f"      --gt {gt_csv} \\")
    print(f"      --output-json {src}/results_v1.json")


if __name__ == "__main__":
    main()
