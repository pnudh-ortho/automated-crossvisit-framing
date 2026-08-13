"""
사진 분류 (Stage 3)

- 교체 가능한 인터페이스: Classifier.predict(image) -> Prediction
- ONNXClassifier: models_dir의 *.onnx 로드 (CPU, onnxruntime)
- MockClassifier: 모델이 없을 때 개발용. 파일명 힌트가 있으면 반영,
  없으면 이미지 해시로 결정적(재현가능) 예측.

전처리(리사이즈/정규화)는 학습(train_classify/train.py)과 반드시 일치해야 한다.
labels(=클래스 순서)는 config.classes 를 단일 진실원천으로 사용.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# 학습과 공유되는 전처리 상수 (train_classify/train.py와 동일하게 유지)
INPUT_SIZE = 224
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass
class Prediction:
    label: str
    confidence: float
    probs: dict[str, float]

    @property
    def is_intraoral(self) -> bool:
        return self.label.startswith("IO_")

    @property
    def is_face(self) -> bool:
        return self.label == "FACE"

    @property
    def is_other(self) -> bool:
        """어느 카테고리에도 해당하지 않는 사진 — 슬롯/얼굴에 배정하지 않는다."""
        return self.label == "OTHERS"


def preprocess(img: Image.Image) -> np.ndarray:
    """PIL RGB → (1,3,224,224) float32 정규화 텐서 (NCHW)."""
    img = img.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    x = np.asarray(img, dtype=np.float32) / 255.0
    x = (x - MEAN) / STD
    x = np.transpose(x, (2, 0, 1))[None, ...]  # NCHW
    return np.ascontiguousarray(x, dtype=np.float32)


def softmax(v: np.ndarray) -> np.ndarray:
    e = np.exp(v - v.max())
    return e / e.sum()


class Classifier:
    def __init__(self, labels: list[str]):
        self.labels = labels

    def _pred_from_logits(self, logits: np.ndarray) -> Prediction:
        probs = softmax(np.asarray(logits, dtype=np.float32).ravel())
        order = np.argsort(probs)[::-1]
        pd = {self.labels[i]: float(probs[i]) for i in order}
        top = order[0]
        return Prediction(self.labels[top], float(probs[top]), pd)

    def predict(self, img: Image.Image, filename: str | None = None) -> Prediction:
        raise NotImplementedError

    def predict_file(self, path: str | Path) -> Prediction:
        p = Path(path)
        with Image.open(p) as im:
            return self.predict(im.copy(), filename=p.name)


class ONNXClassifier(Classifier):
    def __init__(self, model_path: str | Path, labels: list[str]):
        super().__init__(labels)
        import onnxruntime as ort
        self.session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.model_path = str(model_path)

    def predict(self, img: Image.Image, filename: str | None = None) -> Prediction:
        x = preprocess(img)
        logits = self.session.run(None, {self.input_name: x})[0]
        return self._pred_from_logits(logits)


class MockClassifier(Classifier):
    """
    개발용. 실제 모델이 없을 때 UI/워크플로우를 돌리기 위한 스텁.
    - 파일명에 클래스 코드(IO_FRONT 등)나 키워드가 있으면 그 클래스를 높은 확률로.
    - 없으면 이미지 픽셀 해시로 결정적 분포 생성(재현 가능).
    """

    KEYWORDS = {
        # FACE를 먼저 검사한다: "frontal"이 IO_FRONT의 "front"에 먼저 걸리는 것을 방지.
        # (정확한 라벨명이 파일명에 있으면 아래 _hint의 첫 루프에서 이미 처리됨)
        "FACE": ["frontal", "lateral", "얼굴", "측면", "eo_"],
        "IO_FRONT": ["front", "정면", "io_front"],
        "IO_LEFT": ["left", "좌", "io_left"],
        "IO_RIGHT": ["right", "우", "io_right"],
        "IO_UPPER": ["upper", "상악", "교합", "io_upper"],
        "IO_LOWER": ["lower", "하악", "io_lower"],
        "OTHERS": ["other", "기타", "etc"],
    }

    def _hint(self, filename: str | None) -> str | None:
        if not filename:
            return None
        low = filename.lower()
        for label in self.labels:
            if label.lower() in low:
                return label
        for label, kws in self.KEYWORDS.items():
            if label in self.labels and any(k in low for k in kws):
                return label
        return None

    def predict(self, img: Image.Image, filename: str | None = None) -> Prediction:
        n = len(self.labels)
        logits = np.zeros(n, dtype=np.float32)
        hint = self._hint(filename)
        if hint is not None:
            logits[self.labels.index(hint)] = 4.0  # 높은 확신
        else:
            # 이미지 해시 → 결정적 편향
            small = img.convert("L").resize((16, 16))
            h = int(hashlib.md5(small.tobytes()).hexdigest(), 16)
            rng = np.random.default_rng(h % (2**32))
            logits = rng.normal(0, 1, n).astype(np.float32)
            logits[h % n] += 2.0
        return self._pred_from_logits(logits)


def find_onnx_model(models_dir: str | Path) -> Path | None:
    models_dir = Path(models_dir)
    if not models_dir.exists():
        return None
    onnx = sorted(models_dir.glob("*.onnx"))
    return onnx[0] if onnx else None


def load_classifier(cfg) -> Classifier:
    """models_dir에 .onnx가 있으면 ONNX, 없으면 Mock."""
    model = find_onnx_model(cfg.resolve(cfg.paths.models_dir))
    if model is not None:
        return ONNXClassifier(model, cfg.classes)
    return MockClassifier(cfg.classes)
