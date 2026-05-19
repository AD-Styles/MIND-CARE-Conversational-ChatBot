#!/usr/bin/env python3
"""train_emotion.py — 표정 분류기(`EmotionVGG`) 학습.

기본 데이터셋: HuggingFace `deanngkl/ferplus-7cls` (FER+, 35,481 imgs, 7-class)
  - FER2013 이미지를 5인 multi-rater 로 재라벨한 것이라 노이즈 ↓ 정확도 ↑
  - 라벨 순서가 ours 와 동일 (anger=angry, …, sadness=sad)

CLI 로 다른 데이터셋(FER2013, CK+)도 호환 가능.

사용
  # 메인: FER+ + EmotionVGG, 50 epoch
  python train_emotion.py

  # FER2013
  python train_emotion.py --dataset fer2013

  # CK+ (lab-controlled, 보조 정확도 보고용 — train/val 8:2 split)
  python train_emotion.py --dataset ckplus --epochs 30 --out ckplus_weights.pth

  # 작은 모델 비교용
  python train_emotion.py --model mini --out mini_weights.pth
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, random_split
from torchvision import transforms as T
from tqdm import tqdm

from emotion_models import LABEL_NAMES, NUM_CLASSES, build_model

REPO_ROOT  = Path(__file__).resolve().parents[2]
EMO_DIR    = REPO_ROOT / "models" / "emotion_classifier"
DEFAULT_OUT = EMO_DIR / "fer_weights.pth"


# ----------------------------------------------------------------------
# 데이터셋 로더
# ----------------------------------------------------------------------
class HFEmotion(Dataset):
    """HuggingFace dataset → tensor.

    label 이 int 면 그대로, str (CK+ 의 'anger', 'happy' 등) 이면 미리 만든
    name→idx 매핑으로 변환.
    """
    def __init__(self, hf_split, transform, str_label_map=None):
        self.ds = hf_split
        self.tf = transform
        self.str_map = str_label_map

    def __len__(self):
        return len(self.ds)

    def __getitem__(self, i):
        row = self.ds[i]
        img = row["image"]
        if self.str_map is not None:
            lbl = self.str_map[row["label"]]
        else:
            lbl = int(row["label"])
        return self.tf(img), lbl


def _train_tf():
    return T.Compose([
        T.Grayscale(num_output_channels=1),
        T.Resize((48, 48)),
        T.RandomHorizontalFlip(),
        T.RandomAffine(degrees=10, translate=(0.05, 0.05), scale=(0.95, 1.05)),
        T.ToTensor(),
    ])


def _val_tf():
    return T.Compose([
        T.Grayscale(num_output_channels=1),
        T.Resize((48, 48)),
        T.ToTensor(),
    ])


def build_loaders(name: str, batch: int, workers: int):
    from datasets import load_dataset

    if name == "ferplus":
        print("[data] FER+ (deanngkl/ferplus-7cls) 로드 …")
        ds = load_dataset("deanngkl/ferplus-7cls")
        # train split 만 있음 → 90/10 으로 split (val) + 그대로 test 도 같이
        full = ds["train"]
        n = len(full)
        n_val = max(1000, n // 10)
        n_train = n - n_val
        gen = torch.Generator().manual_seed(0)
        tr, va = random_split(full, [n_train, n_val], generator=gen)
        tr_loader = DataLoader(HFEmotion(tr, _train_tf()), batch_size=batch,
                               shuffle=True, num_workers=workers, pin_memory=True, drop_last=True)
        va_loader = DataLoader(HFEmotion(va, _val_tf()),  batch_size=batch,
                               shuffle=False, num_workers=workers, pin_memory=True)
        # FER+ 는 별도 test split 가 없음 — val 을 final test 로도 사용
        return tr_loader, va_loader, va_loader

    if name == "fer2013":
        print("[data] FER2013 (AutumnQiu/fer2013) 로드 …")
        ds = load_dataset("AutumnQiu/fer2013")
        # FER2013 라벨 순서를 ours 로 remap
        remap = {0: 0, 1: 1, 2: 2, 3: 3, 4: 5, 5: 6, 6: 4}

        class _Remapped(Dataset):
            def __init__(self, hf, tf):
                self.hf = hf; self.tf = tf
            def __len__(self): return len(self.hf)
            def __getitem__(self, i):
                r = self.hf[i]
                return self.tf(r["image"]), remap[int(r["label"])]

        tr_loader = DataLoader(_Remapped(ds["train"], _train_tf()), batch_size=batch,
                               shuffle=True, num_workers=workers, pin_memory=True, drop_last=True)
        va_loader = DataLoader(_Remapped(ds["valid"], _val_tf()), batch_size=batch,
                               shuffle=False, num_workers=workers, pin_memory=True)
        te_loader = DataLoader(_Remapped(ds["test"],  _val_tf()), batch_size=batch,
                               shuffle=False, num_workers=workers, pin_memory=True)
        return tr_loader, va_loader, te_loader

    if name == "multi":
        # FER+ + RAF-DB (+ optional AffectNet) — 라벨은 features.names 기반 자동 매핑
        from torch.utils.data import ConcatDataset
        import os

        SOURCES = [
            "deanngkl/ferplus-7cls",
            "deanngkl/raf-db-7emotions",
        ]
        # AffectNet 미러는 큰 경우 디스크/메모리 부담 → 환경변수로 opt-in
        if os.environ.get("INCLUDE_AFFECTNET", "0") == "1":
            SOURCES.append("deanngkl/affectnet_no_contempt")

        OURS = {n: i for i, n in enumerate(LABEL_NAMES)}
        ALIAS = {
            "anger": "angry", "happiness": "happy", "sadness": "sad",
            "happy": "happy", "surprise": "surprise", "fear": "fear",
            "disgust": "disgust", "neutral": "neutral",
            "angry": "angry", "sad": "sad",
            "contempt": "neutral",
        }

        def _build_remap(feat):
            if hasattr(feat, "names"):
                return {i: OURS.get(ALIAS.get(n.lower(), n.lower()), OURS["neutral"])
                        for i, n in enumerate(feat.names)}
            return None

        class _Mapped(Dataset):
            def __init__(self, hf, tf, remap_int=None):
                self.hf = hf; self.tf = tf; self.m = remap_int
            def __len__(self): return len(self.hf)
            def __getitem__(self, i):
                r = self.hf[i]
                raw = r["label"]
                if isinstance(raw, str):
                    lbl = OURS.get(ALIAS.get(raw.lower(), raw.lower()), OURS["neutral"])
                else:
                    lbl = self.m[int(raw)] if self.m is not None else int(raw)
                return self.tf(r["image"]), lbl

        train_parts, val_parts = [], []
        first_val_part = None
        for repo in SOURCES:
            print(f"[data] {repo} 로드 …")
            try:
                ds = load_dataset(repo)
            except Exception as e:
                print(f"  SKIP — load 실패: {str(e).splitlines()[0][:120]}")
                continue
            split_name = "train" if "train" in ds else list(ds.keys())[0]
            full = ds[split_name]
            remap = _build_remap(full.features.get("label"))
            n = len(full)
            n_val = max(500, n // 10)
            n_train = n - n_val
            gen = torch.Generator().manual_seed(0)
            tr, va = random_split(full, [n_train, n_val], generator=gen)
            train_parts.append(_Mapped(tr, _train_tf(), remap))
            val_parts.append(_Mapped(va, _val_tf(), remap))
            print(f"  + {repo}: train={n_train}  val={n_val}  remap={remap}")
            if first_val_part is None:
                first_val_part = val_parts[-1]   # FER+ val → final test 지표

        if not train_parts:
            raise RuntimeError("multi: 사용 가능한 데이터셋이 없습니다.")

        train_ds = ConcatDataset(train_parts)
        val_ds   = ConcatDataset(val_parts)
        print(f"[data] 합산: train={len(train_ds)}  val={len(val_ds)}  test(=FER+ val)={len(first_val_part)}")
        tr_loader = DataLoader(train_ds, batch_size=batch, shuffle=True,
                               num_workers=workers, pin_memory=True, drop_last=True)
        va_loader = DataLoader(val_ds,   batch_size=batch, shuffle=False,
                               num_workers=workers, pin_memory=True)
        te_loader = DataLoader(first_val_part, batch_size=batch, shuffle=False,
                               num_workers=workers, pin_memory=True)
        return tr_loader, va_loader, te_loader

    if name == "ckplus":
        print("[data] CK+ (AlirezaF138/ckplus-dataset) 로드 …")
        ds = load_dataset("AlirezaF138/ckplus-dataset")
        full = ds["train"]
        # 라벨 → idx (CK+ 가 contempt 같은 8번째 클래스 가지면 무시)
        labels = sorted({row["label"].lower() for row in full})
        canonical = {n: i for i, n in enumerate(LABEL_NAMES)}
        # CK+ 별칭 정규화
        alias = {
            "anger": "angry", "happiness": "happy", "sadness": "sad",
            "contempt": "neutral",   # 7-class 호환
        }
        str_map = {}
        for s in labels:
            key = alias.get(s, s)
            if key in canonical:
                str_map[s] = canonical[key]
            else:
                # 모르는 라벨은 neutral
                str_map[s] = canonical["neutral"]
        print(f"[data] CK+ label map = {str_map}")
        n = len(full)
        n_val = max(50, n // 5)   # 20%
        n_train = n - n_val
        gen = torch.Generator().manual_seed(0)
        tr, va = random_split(full, [n_train, n_val], generator=gen)
        tr_loader = DataLoader(HFEmotion(tr, _train_tf(), str_map), batch_size=batch,
                               shuffle=True, num_workers=workers, pin_memory=True, drop_last=False)
        va_loader = DataLoader(HFEmotion(va, _val_tf(), str_map), batch_size=batch,
                               shuffle=False, num_workers=workers, pin_memory=True)
        return tr_loader, va_loader, va_loader

    raise ValueError(f"unknown dataset: {name}")


# ----------------------------------------------------------------------
# 학습/검증 루프
# ----------------------------------------------------------------------
def run_epoch(model, loader, criterion, optimizer, device, *, train: bool):
    model.train(train)
    total = correct = 0
    loss_sum = 0.0
    bar = tqdm(loader, desc="train" if train else "val ", leave=False)
    for x, y in bar:
        x = x.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)
        with torch.set_grad_enabled(train):
            logits = model(x)
            loss = criterion(logits, y)
        if train:
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        loss_sum += loss.item() * x.size(0)
        correct += (logits.argmax(1) == y).sum().item()
        total += x.size(0)
        bar.set_postfix(loss=loss_sum/total, acc=correct/total)
    return loss_sum / total, correct / total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="ferplus",
                    choices=["ferplus", "fer2013", "ckplus", "multi"])
    ap.add_argument("--model",   default="vgg",
                    choices=["vgg", "mini"])
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch",  type=int, default=128)
    ap.add_argument("--lr",     type=float, default=1e-3)
    ap.add_argument("--wd",     type=float, default=1e-4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--label-smoothing", type=float, default=0.05)
    ap.add_argument("--init-from", type=Path, default=None,
                    help="Pretrained .pth 에서 transfer learning 시작")
    ap.add_argument("--out",    type=Path, default=DEFAULT_OUT)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    device = (torch.device("cuda" if torch.cuda.is_available() else "cpu")
              if args.device == "auto" else torch.device(args.device))
    print(f"[setup] device={device}, dataset={args.dataset}, model={args.model}")
    if device.type == "cuda":
        print(f"        {torch.cuda.get_device_name(0)}  free={torch.cuda.mem_get_info()[0]/1e9:.2f} GB")

    torch.manual_seed(0)
    train_loader, val_loader, test_loader = build_loaders(
        args.dataset, args.batch, args.workers)

    model = build_model(args.model).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"[model] {model.__class__.__name__}  params={n_params/1e6:.3f} M ({n_params/1e3:.1f} K)")

    if args.init_from is not None and args.init_from.is_file():
        sd = torch.load(args.init_from, map_location=device)
        # head 차원이 다르면 head 만 빼고 로드 (transfer learning)
        own = model.state_dict()
        loaded = 0
        for k, v in sd.items():
            if k in own and own[k].shape == v.shape:
                own[k] = v
                loaded += 1
        model.load_state_dict(own)
        print(f"[init ] {args.init_from} 에서 {loaded}/{len(sd)} 텐서 복사")

    criterion = nn.CrossEntropyLoss(label_smoothing=args.label_smoothing)
    optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    best_acc = 0.0
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        tr_loss, tr_acc = run_epoch(model, train_loader, criterion, optimizer, device, train=True)
        va_loss, va_acc = run_epoch(model, val_loader,   criterion, optimizer, device, train=False)
        scheduler.step()
        print(f"[ep {epoch:>2}/{args.epochs}] "
              f"train loss={tr_loss:.3f} acc={tr_acc:.3f} | "
              f"val loss={va_loss:.3f} acc={va_acc:.3f} | "
              f"lr={optimizer.param_groups[0]['lr']:.5f}")
        if va_acc > best_acc:
            best_acc = va_acc
            torch.save(model.state_dict(), args.out)
            print(f"           ★ best val_acc={va_acc:.4f} → {args.out}")

    elapsed = time.time() - t0
    print(f"\n[done] best val_acc={best_acc:.4f}  in {elapsed/60:.1f} min")
    print(f"       weights: {args.out}")

    if test_loader is not val_loader:
        model.load_state_dict(torch.load(args.out, map_location=device))
        te_loss, te_acc = run_epoch(model, test_loader, criterion, optimizer, device, train=False)
        print(f"[test] loss={te_loss:.3f}  acc={te_acc:.4f}")


if __name__ == "__main__":
    main()
