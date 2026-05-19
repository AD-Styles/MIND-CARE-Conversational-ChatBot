#!/usr/bin/env python3
"""prepare_models.py — Phase 2 모델 준비 도우미.

다음 두 단계를 한 번에 처리한다:

  1) ONNX 준비
     - face: ultralytics 의 yolov8n-face.pt → ONNX export
     - emotion: Mini-Xception 정의 → ONNX export
       * fer_weights.pth 가 있으면 로드, 없으면 랜덤 초기화 (테스트용)
  2) TensorRT 엔진 빌드 (trtexec, FP16 기본)

전제
  - DS 8.0 + TensorRT 10.x 설치
  - 가상환경에 ultralytics, torch, onnx 가 있어야 ONNX 단계가 동작
    (없어도 TRT 단계만 단독으로 돌릴 수 있음)

사용
  python3 prepare_models.py                 # ONNX + TRT 전체 (FP16)
  python3 prepare_models.py --skip-trt      # ONNX 만
  python3 prepare_models.py --trt-only      # 이미 ONNX 가 있을 때 TRT 만
  python3 prepare_models.py --face          # face 만
  python3 prepare_models.py --emotion       # emotion 만
  python3 prepare_models.py --fp32          # FP32 빌드 (기본 FP16)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("prepare_models")

# 이 스크립트 위치: release/vision/mind_care_perception/scripts/prepare_models.py
#   parents[0] = scripts/
#   parents[1] = mind_care_perception/
#   parents[2] = release/vision/    ← 여기가 우리가 원하는 루트
REPO_ROOT  = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"

FACE_DIR    = MODELS_DIR / "face_detector"
EMO_DIR     = MODELS_DIR / "emotion_classifier"
POSE_DIR    = MODELS_DIR / "pose_estimator"

FACE_ONNX   = FACE_DIR / "yolov8n_face.onnx"
FACE_ENGINE = FACE_DIR / "yolov8n_face.engine"
EMO_ONNX    = EMO_DIR  / "mini_xception.onnx"
EMO_ENGINE  = EMO_DIR  / "mini_xception.engine"
POSE_ONNX   = POSE_DIR / "yolov8n_pose.onnx"
POSE_ENGINE = POSE_DIR / "yolov8n_pose.engine"


# ----------------------------------------------------------------------
# 1. ONNX 단계
# ----------------------------------------------------------------------
# yolov8n-face — akanametov/yolo-face 1.0.0 릴리스 (라이선스: AGPL-3.0).
# ONNX 가 릴리스에 직접 올라와 있어 ultralytics export 단계를 건너뛸 수 있다.
YOLOV8N_FACE_ONNX_URL = (
    "https://github.com/akanametov/yolo-face/releases/download/1.0.0/yolov8n-face.onnx"
)
YOLOV8N_FACE_PT_URL = (
    "https://github.com/akanametov/yolo-face/releases/download/1.0.0/yolov8n-face.pt"
)


def _http_download(url: str, dst: Path) -> bool:
    import urllib.request
    dst.parent.mkdir(parents=True, exist_ok=True)
    log.info("  download: %s", url)
    try:
        urllib.request.urlretrieve(url, dst)
    except Exception as exc:
        log.error("  다운로드 실패: %s", exc)
        return False
    log.info("  → %s  (%.1f MB)", dst, dst.stat().st_size / 1e6)
    return True


def export_yolov8n_face_onnx(out: Path, *, prefer_pt_export: bool = False) -> bool:
    """yolov8n-face ONNX 확보.

    기본: 릴리스의 ``yolov8n-face.onnx`` 를 그대로 받는다 (가장 빠른 길).
    ``prefer_pt_export=True`` 면 ``.pt`` 를 받아 ultralytics 로 직접 export 한다
    (입력 해상도 변경 등 커스터마이즈가 필요할 때).
    """
    if out.is_file():
        log.info("  ONNX 캐시 사용: %s", out)
        return True
    out.parent.mkdir(parents=True, exist_ok=True)

    if not prefer_pt_export:
        if _http_download(YOLOV8N_FACE_ONNX_URL, out):
            return True
        log.warning("  ONNX 직접 받기 실패 — .pt + ultralytics export 폴백")

    # 폴백: .pt → ultralytics export
    try:
        from ultralytics import YOLO
    except ImportError:
        log.error("ultralytics 미설치. `pip install ultralytics` 후 재시도.")
        return False

    pt_cache = out.parent / "yolov8n-face.pt"
    if not pt_cache.is_file():
        if not _http_download(YOLOV8N_FACE_PT_URL, pt_cache):
            return False

    log.info("ultralytics export: %s → ONNX", pt_cache)
    model = YOLO(str(pt_cache))
    exported = model.export(format="onnx", imgsz=640, opset=12, simplify=True)
    src = Path(str(exported))
    if not src.is_file():
        log.error("ONNX export 실패")
        return False
    if src.resolve() != out.resolve():
        shutil.move(str(src), str(out))
    log.info(f"  → {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return True


def export_yolov8n_pose_onnx(out: Path) -> bool:
    """ultralytics 공식 yolov8n-pose.pt → NMS-baked ONNX export.

    .pt 캐시는 ``release/vision/models/pose_estimator/yolov8n-pose.pt``.
    ``nms=True`` 로 export 하면 출력은 ``[1, max_det, 6+17×3] = [1, 300, 57]``
    형식 (face 와 동일한 NMS-baked 패턴) — DS 커스텀 파서가 단순해진다.
    """
    if out.is_file():
        log.info("  ONNX 캐시 사용: %s", out)
        return True

    try:
        from ultralytics import YOLO
    except ImportError:
        log.error("ultralytics 미설치. `pip install ultralytics` 후 재시도.")
        return False

    out.parent.mkdir(parents=True, exist_ok=True)
    pt_cache = out.parent / "yolov8n-pose.pt"

    log.info("ultralytics: %s 자동 다운로드 + ONNX export …", pt_cache.name)
    # ultralytics 가 캐시 위치를 정해 두므로 cwd 를 잠깐 옮김
    cwd_was = os.getcwd()
    os.chdir(out.parent)
    try:
        model = YOLO("yolov8n-pose.pt")   # 자동 다운로드
        # nms=True 면 NMS 가 ONNX 그래프 안에 박혀 [batch, max_det, 57] 형식
        exported = model.export(
            format="onnx", imgsz=640, opset=12,
            simplify=True, nms=True,
        )
    finally:
        os.chdir(cwd_was)

    # ultralytics 는 cwd 에 'yolov8n-pose.onnx' (hyphen) 형태로 저장하기도 함
    candidates = [Path(str(exported))]
    candidates += list(out.parent.glob("yolov8n*pose*.onnx"))
    src = next((c for c in candidates if c.is_file() and c.resolve() != out.resolve()),
               None)
    if src is None and out.is_file():
        log.info(f"  → {out}  (already in place)")
        return True
    if src is None:
        log.error("ONNX export 실패 — 후보 파일 없음")
        return False
    shutil.move(str(src), str(out))
    log.info(f"  → {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return True


def export_mini_xception_onnx(out: Path, weights: Path | None = None,
                              model_name: str = "vgg") -> bool:
    """`emotion_models.build_model` 모델 (1×48×48 → 7-class) ONNX export.

    weights 파일이 있으면 로드, 없으면 랜덤 가중치 (구조 검증용).
    출력 파일 이름은 호환을 위해 그대로 ``mini_xception.onnx`` 사용.
    """
    try:
        import torch
    except ImportError:
        log.error("torch 미설치. `pip install torch` 후 재시도.")
        return False

    # 같은 디렉터리의 emotion_models 를 import
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from emotion_models import build_model  # type: ignore

    out.parent.mkdir(parents=True, exist_ok=True)
    model = build_model(model_name).eval()
    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"  model={model.__class__.__name__}  params={n_params/1e6:.3f} M")

    if weights and weights.is_file():
        state = torch.load(weights, map_location="cpu")
        # head 크기 mismatch (작은 → 큰 모델 또는 그 반대) 대비
        own = model.state_dict()
        loaded = 0
        for k, v in state.items():
            if k in own and own[k].shape == v.shape:
                own[k] = v
                loaded += 1
        model.load_state_dict(own)
        log.info(f"  가중치 로드: {weights}  ({loaded}/{len(state)} 텐서)")
    else:
        log.warning(
            "  사전학습 가중치 없음 — 랜덤 가중치로 export (구조 검증용)."
            f"\n  실서비스용 가중치는 {weights} 에 두세요."
        )

    dummy = torch.zeros(1, 1, 48, 48)
    torch.onnx.export(
        model, dummy, str(out),
        input_names=["input"], output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=12,
    )
    log.info(f"  → {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return True


# ----------------------------------------------------------------------
# 2. TensorRT 엔진 빌드 (trtexec, TRT 10 호환)
# ----------------------------------------------------------------------
def find_trtexec() -> str:
    for c in (shutil.which("trtexec"),
              "/usr/src/tensorrt/bin/trtexec",
              "/opt/nvidia/deepstream/deepstream/lib/trtexec",
              "/usr/bin/trtexec"):
        if c and os.path.isfile(c):
            return c
    sys.exit("trtexec 없음. TensorRT 설치 확인.")


def build_engine(onnx: Path, engine: Path, fp16: bool, *,
                 dynamic_shapes: dict[str, tuple[str, str, str]] | None = None,
                 workspace_mib: int = 2048) -> None:
    """TRT 10.x 호환 trtexec 호출.

    TRT 10 부터 ``--workspace`` 가 deprecated → ``--memPoolSize=workspace:N``.
    ``--explicitBatch`` 도 더 이상 필요/지원되지 않음.
    """
    if not onnx.is_file():
        sys.exit(f"ONNX 파일 없음: {onnx}\n  먼저 --skip-trt 옵션으로 ONNX 단계를 돌리세요.")

    trtexec = find_trtexec()
    cmd = [
        trtexec,
        f"--onnx={onnx}",
        f"--saveEngine={engine}",
        f"--memPoolSize=workspace:{workspace_mib}",
    ]
    if fp16:
        cmd.append("--fp16")
    if dynamic_shapes:
        for name, (mn, opt, mx) in dynamic_shapes.items():
            cmd += [f"--minShapes={name}:{mn}",
                    f"--optShapes={name}:{opt}",
                    f"--maxShapes={name}:{mx}"]

    log.info("[trtexec] %s", " ".join(cmd))
    subprocess.check_call(cmd)


# ----------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Phase 2/4 모델 준비 (ONNX + TRT)")
    p.add_argument("--face",     action="store_true", help="얼굴 모델만")
    p.add_argument("--emotion",  action="store_true", help="감정 모델만")
    p.add_argument("--pose",     action="store_true", help="포즈(낙상) 모델만")
    p.add_argument("--skip-trt", action="store_true", help="ONNX 만 (TRT 변환 생략)")
    p.add_argument("--trt-only", action="store_true", help="TRT 만 (ONNX 단계 생략)")
    p.add_argument("--fp32",     action="store_true", help="FP32 (기본 FP16)")
    p.add_argument("--emotion-weights", type=Path, default=EMO_DIR / "fer_weights.pth",
                   help="(선택) Mini-Xception 사전학습 가중치 .pth 경로")
    args = p.parse_args()

    selected = (args.face, args.emotion, args.pose)
    do_all = not any(selected)
    do_face    = args.face    or do_all
    do_emotion = args.emotion or do_all
    do_pose    = args.pose    or do_all
    fp16       = not args.fp32

    log.info("=" * 60)
    log.info(" 모델 준비 — face=%s, emotion=%s, pose=%s, fp16=%s",
             do_face, do_emotion, do_pose, fp16)
    log.info(" 출력 디렉터리: %s", MODELS_DIR)
    log.info("=" * 60)

    # 1) ONNX
    if not args.trt_only:
        if do_face:
            if FACE_ONNX.is_file():
                log.info("face ONNX 이미 존재 → 건너뜀: %s", FACE_ONNX)
            else:
                export_yolov8n_face_onnx(FACE_ONNX)
        if do_emotion:
            if EMO_ONNX.is_file():
                log.info("emotion ONNX 이미 존재 → 건너뜀: %s", EMO_ONNX)
            else:
                export_mini_xception_onnx(EMO_ONNX, args.emotion_weights)
        if do_pose:
            if POSE_ONNX.is_file():
                log.info("pose ONNX 이미 존재 → 건너뜀: %s", POSE_ONNX)
            else:
                export_yolov8n_pose_onnx(POSE_ONNX)

    # 2) TRT
    if not args.skip_trt:
        if do_face:
            build_engine(FACE_ONNX, FACE_ENGINE, fp16, workspace_mib=2048)
        if do_emotion:
            build_engine(
                EMO_ONNX, EMO_ENGINE, fp16,
                dynamic_shapes={
                    "input": ("1x1x48x48", "8x1x48x48", "16x1x48x48"),
                },
                workspace_mib=512,
            )
        if do_pose:
            build_engine(POSE_ONNX, POSE_ENGINE, fp16, workspace_mib=2048)

    log.info("\n완료.")
    for f in (FACE_ONNX, FACE_ENGINE, EMO_ONNX, EMO_ENGINE, POSE_ONNX, POSE_ENGINE):
        if f.is_file():
            log.info("  %s  (%.1f MB)", f, f.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
