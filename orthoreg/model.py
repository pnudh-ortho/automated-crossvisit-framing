"""모델 구축 — 학습 코드에서 **추론에 필요한 부분만** 떼어냈다.

`train_seg.py` 를 통째로 import 하면 학습 루프·증강·DataLoader 가 딸려온다.
배포물에는 그게 없어야 한다.

### 원본 SAM2 가중치는 필요 없다

`build_sam2(cfg, ckpt_path=None)` 로 **구조만** 짓는다. 미세조정 체크포인트가 trunk
가중치까지 전부 담고 있어서(키 422개 중 `encoder.*` 306개) 어차피 바로 덮어써진다.

예전 코드는 200MB 원본을 **학습 PC 의 하드코딩된 절대경로**에서 읽었다. 배포본에는
그 경로가 없다. 이 변경으로 배포물이 받아야 할 것은 미세조정 체크포인트 하나뿐이다.
"""

from __future__ import annotations

from pathlib import Path

import torch

from .segmodel import ToothSegNet

# SAM2.1 인코더 크기 — 네 개 전부 **FPN 출력이 같다** (3레벨 × 256채널 @ 256²/128²/64²).
# 그래서 디코더(`ToothSegNet`)를 손대지 않고 trunk 만 갈아 끼울 수 있다.
# 가중치 파일명은 학습 때만 쓰던 것이라 뺐다 — 추론은 구조만 필요하다.
SIZES = {
    "tiny":      "configs/sam2.1/sam2.1_hiera_t.yaml",
    "small":     "configs/sam2.1/sam2.1_hiera_s.yaml",
    "base_plus": "configs/sam2.1/sam2.1_hiera_b+.yaml",
    "large":     "configs/sam2.1/sam2.1_hiera_l.yaml",
}


def build(device: str, width: int, unfreeze_trunk: bool = False,
          size: str = "large", arch: bool = False, **kw) -> ToothSegNet:
    """구조만 짓는다. 가중치는 `build_from_ckpt` 가 얹는다."""
    from sam2.build_sam import build_sam2                      # noqa: PLC0415
    sam = build_sam2(SIZES[size], None, device="cpu", apply_postprocessing=False)
    net = ToothSegNet(sam, width=width, freeze_trunk=not unfreeze_trunk, arch=arch, **kw)
    return net.to(device)


def build_from_ckpt(ckpt, device: str = "cuda"):
    """체크포인트가 담은 구성으로 모델을 짓고 가중치를 올린다.

    호출부가 `build(device, 128)` 처럼 **인자를 손으로 적으면** 구성이 바뀔 때마다
    형상 불일치로 죽는다. 체크포인트가 자기 구성을 담고 있으므로 그걸 쓴다.
    """
    ck = torch.load(ckpt, map_location=device) if isinstance(ckpt, (str, Path)) else ckpt
    net = build(device, int(ck.get("width", 128)), size=ck.get("sam_size", "large"),
                arch=bool(ck.get("arch", False)),
                inst_stride=int(ck.get("inst_stride", 1)),
                inst_width=int(ck.get("inst_width", 256)),
                scale_head=bool(ck.get("scale_head", False)),
                inst_src=str(ck.get("inst_src", "fpn0")),
                stem_mult=int(ck.get("stem_mult", 1)),
                up4_mult=int(ck.get("up4_mult", 1)))
    net.load_state_dict(ck["model"])
    net.eval()
    return net, ck
