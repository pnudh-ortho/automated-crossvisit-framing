"""분할 추론 — **torch 와 onnxruntime 을 같은 얼굴로** 감싼다.

    seg = Segmenter("models/_installed/seg-1.0.0-20260811.onnx")
    r = seg.infer(img_bgr)          # r.inst · r.fg_prob · r.sem

### 왜 두 가지인가

    연구(train_revisit)    torch — 학습·시각화·실험이 같은 코드를 써야 한다
    배포(webapp)           onnxruntime — torch(휠 200MB+)·sam2·hydra 를 안 깐다

내보낸 ONNX 는 torch 와 **수치가 같다** (최대 상대오차 1.6e-05, float32 잡음 수준).
그리고 `torch.onnx.export` 가 결정적이라 같은 체크포인트에서 같은 해시가 나온다 —
재배포할 때 해시를 다시 계산할 필요가 없다.

### 배포본은 torch 를 안 깐다 — 지켜야 할 불변식

    ONNX 경로   runtime → constants · segment · instances      cv2 · numpy 뿐
    torch 경로  runtime 의 else 분기 → model → segmodel        torch · sam2 필요

`orthoreg/__init__.py` 는 `build`·`build_from_ckpt` 를 `__getattr__` 로 늦게 준다.
**최상단에서 `from .model import ...` 를 하면 배포본이 `import orthoreg` 에서 죽는다.**
`segment.py`·`instances.py`·`register.py` 에도 torch 를 들이면 안 된다.

### 전처리·후처리를 여기 둔다

예전에는 호출부(`registration_teeth.centers`)가 리사이즈·정규화·softmax 를 직접 했다.
백엔드가 둘이 되면 그 코드가 양쪽에 복사되고, 한쪽만 고치는 사고가 난다.

`stride` 는 **출력 형상에서 유도한다** — ONNX 에는 체크포인트 메타데이터가 없다.
로짓이 1024², 히트맵이 256² 이면 stride 4 다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .constants import CLS_APPLIANCE, CLS_TOOTH
from .segment import instances_from

SIZE = 1024
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


@dataclass
class SegResult:
    sem: np.ndarray        # (H,W) argmax 클래스
    fg_prob: np.ndarray    # (H,W) 전경 확률 = 치아 + 장치
    inst: np.ndarray       # (H,W) 인스턴스 맵 (0 = 배경)
    stride: int


def _softmax(x: np.ndarray, axis: int = 0) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class Segmenter:
    """가중치 파일 하나를 받아 백엔드를 스스로 고른다 (`.onnx` / `.pt`)."""

    def __init__(self, weights: str | Path, device: str = "auto"):
        self.path = Path(weights)
        self.kind = "onnx" if self.path.suffix.lower() == ".onnx" else "torch"
        if self.kind == "onnx":
            import onnxruntime as ort                            # noqa: PLC0415
            prov = ["CPUExecutionProvider"]
            if device in ("auto", "cuda") and "CUDAExecutionProvider" in ort.get_available_providers():
                prov = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            self.sess = ort.InferenceSession(str(self.path), providers=prov)
            self.out_names = [o.name for o in self.sess.get_outputs()]
        else:
            import torch                                          # noqa: PLC0415
            from .model import build_from_ckpt                    # noqa: PLC0415
            self.torch = torch
            dev = device
            if dev == "auto":
                dev = "cuda" if torch.cuda.is_available() else "cpu"
            self.device = dev
            self.net, self.ck = build_from_ckpt(str(self.path), dev)

    # ── 원시 출력 ────────────────────────────────────────────────────────
    def _raw(self, x: np.ndarray):
        """(1,3,1024,1024) float32 → (logits, heat, offset) 넘파이 로짓."""
        if self.kind == "onnx":
            out = self.sess.run(None, {self.sess.get_inputs()[0].name: x})
            d = dict(zip(self.out_names, out))
            return d["logits"][0], d["heat"][0], d["offset"][0]
        t = self.torch
        with t.no_grad():
            xt = t.from_numpy(x).to(self.device)
            if self.device == "cuda":
                with t.autocast("cuda", t.bfloat16):
                    lg, hl, op, *_ = self.net(xt)
            else:
                lg, hl, op, *_ = self.net(xt)
        return (lg.float()[0].cpu().numpy(), hl.float()[0].cpu().numpy(),
                op.float()[0].cpu().numpy())

    def infer(self, img_bgr: np.ndarray) -> SegResult:
        """사진 한 장 → 시맨틱·전경확률·인스턴스 맵 (전부 1024² 격자)."""
        im = cv2.resize(img_bgr, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        x = ((im[:, :, ::-1].astype(np.float32) / 255.0 - MEAN) / STD)
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        lg, hl, op = self._raw(x)

        prob = _softmax(lg, axis=0)
        sem = lg.argmax(0)
        heat = _sigmoid(hl[0] if hl.ndim == 3 else hl)
        # 출력 형상에서 stride 를 얻는다 — ONNX 에는 체크포인트 메타데이터가 없다
        stride = max(1, int(round(lg.shape[-1] / heat.shape[-1])))
        fg = prob[CLS_TOOTH] + prob[CLS_APPLIANCE]
        inst = instances_from((sem == CLS_TOOTH) | (sem == CLS_APPLIANCE),
                              heat, op, stride=stride, prob=prob[CLS_TOOTH])
        return SegResult(sem=sem, fg_prob=fg, inst=inst, stride=stride)

    def centers(self, img_bgr: np.ndarray, *, use_gate: bool = False) -> np.ndarray:
        """치아 중심점을 **원본 픽셀 좌표**로. 정합이 실제로 쓰는 것.

        1024² 가 아니라 각 사진 자신의 좌표로 돌려준다 — 두 사진의 종횡비가
        달라도 리사이즈 왜곡이 변환에 섞이지 않는다.
        """
        from . import register as RG                              # noqa: PLC0415
        h, w = img_bgr.shape[:2]
        sg = RG.tooth_signals(self.infer(img_bgr).inst)
        if not sg:
            return np.zeros((0, 2), np.float64)
        keys = sorted(RG.usable(sg)) if use_gate else sorted(sg)
        if not keys:
            return np.zeros((0, 2), np.float64)
        C = np.array([sg[k]["center"] for k in keys], np.float64)
        C[:, 0] *= w / SIZE
        C[:, 1] *= h / SIZE
        return C
