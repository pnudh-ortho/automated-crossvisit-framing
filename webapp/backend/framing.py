"""
자동 프레이밍 (Stage 3.5) — 초진 사진을 어디를 어떤 각도·배율로 자를지 예측 (문서 §12).

지금까지 초진은 `coords.cover_fit_placement()`(회전 0, 중심 정렬)로 놓고 사람이 전부
손으로 잡았다. 여기 모델이 그 초기값을 사람이 잘랐을 법한 사각형으로 바꿔 준다.

**인터페이스를 정합(`registration_teeth.py`)과 같은 모양으로 맞춘다**(문서 §12.1):
변환 + 신뢰도 + ok 플래그를 돌려주면 `main.py` 가 초진·재진을 같은 코드 경로로 다룰
수 있고, 신뢰도가 미달이면 양쪽 다 cover-fit 으로 물러난다.

## 좌표계

    raw        업로드된 사진 픽셀
    canonical  잘라낸 결과물 (crop_aspect 비율의 사각형)
    T          raw → canonical 유사변환 ← 모델이 예측하는 것

`coords.placement_from_photo_affine()` 이 "사진 픽셀 → 슬라이드 cm" 을 받으므로,
T 에 고정 변환(canonical → 슬롯) 하나만 합성하면 기존 배치 코드에 그대로 들어간다.
새 좌표 개념이 필요 없다 — 문제를 4자유도로 못박은 것의 실질적 보상이다.

## 학습 쪽과 반드시 같아야 하는 것

전처리(letterbox → ImageNet 정규화)와 기하 풀이(Umeyama)는 학습 코드
(`train_move_rotate_crop/framing/{data,geometry}.py`)와 **비트 단위로 같은 규약**이어야
한다. 어긋나면 예외가 아니라 조용한 정확도 저하로만 나타난다. 웹앱은 PyTorch 를 반입하지
않으므로 그 저장소를 import 할 수 없어 아래에 다시 구현했고, 대신
  · 상수(mean/std/입력크기/crop_aspect)는 `framing_meta.json` 으로 모델과 함께 온다
  · `tests/test_framing.py` 가 학습 쪽 값과 대조해 고정한다
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ── 학습 쪽과 공유되는 규약 (framing_meta.json 이 없을 때의 기본값) ──────────────
DEFAULT_INPUT_W, DEFAULT_INPUT_H = 336, 224
DEFAULT_MEAN = (0.485, 0.456, 0.406)
DEFAULT_STD = (0.229, 0.224, 0.225)


@dataclass
class FramingResult:
    """`registration_teeth.RegistrationResult` 와 같은 모양 — 상위에서 같이 다루기 위해."""

    ok: bool
    method: str            # 'onnx(final)' | 'onnx(5 folds)' | 실패 사유
    scale: float           # T 의 배율 (canonical 폭 / raw 에서의 crop 폭)
    angle_deg: float       # 회전 (시계방향 +)
    crop_frac: float       # crop 폭 / raw 폭 — 1.0 이면 원본 전체를 쓴 것
    n_models: int          # 평균에 참여한 모델 수
    spread_pct: float      # 모델 간 꼭짓점 불일치 (crop 폭 대비 %). 1개면 nan
    score: float           # 높을수록 신뢰
    matrix: list           # 2x3 raw → canonical (= T)
    canon_wh: tuple        # matrix 가 보내는 canonical 사각형 크기 (T 해석에 필요)
    corners_raw: list      # 4x2, raw 픽셀에서의 crop 사각형 (좌상→우상→우하→좌하)

    def summary(self) -> str:
        sp = "n/a" if self.spread_pct != self.spread_pct else f"{self.spread_pct:.2f}%"
        return (f"[{'OK ' if self.ok else 'LOW'}] {self.method:16s} "
                f"angle={self.angle_deg:+.2f}° crop={self.crop_frac:.3f} "
                f"spread={sp} score={self.score:.1f}")


# ── 기본 임계값 (config.thresholds.framing 으로 override) ────────────────────────
# spread(모델 간 불일치)는 fold 앙상블일 때만 의미가 있다. 배포본 1개면 nan 이라
# 이 게이트는 자동으로 통과되고, 아래 crop/angle 상식 검사만 남는다.
DEFAULT_THRESH = dict(
    max_spread_pct=3.0,    # CV 실측 꼭짓점 오차가 3~6%다. 그보다 크게 갈리면 못 믿는다.
    min_crop_frac=0.15,    # raw 폭의 15% 미만을 잘라내겠다 = 확대율 6.7배. 비정상.
    max_crop_frac=1.60,    # 원본보다 60% 넘게 밖으로 나가면 비정상.
    max_angle_deg=20.0,    # 편집기 회전 슬라이더가 ±10°다. 그 2배까지만 허용.
)


# ── 기하 (train_move_rotate_crop/framing/geometry.py 와 같은 규약) ───────────────
def _similarity(scale: float, angle_deg: float, tx: float, ty: float) -> np.ndarray:
    th = math.radians(angle_deg)
    c, s = math.cos(th) * scale, math.sin(th) * scale
    return np.array([[c, -s, tx], [s, c, ty]], dtype=np.float64)


def _to3(M) -> np.ndarray:
    return np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]])


def _invert(M) -> np.ndarray:
    return np.linalg.inv(_to3(M))[:2, :]


def _compose(*mats) -> np.ndarray:
    """compose(A, B) = A∘B (먼저 B, 그 다음 A)."""
    out = np.eye(3, dtype=np.float64)
    for M in mats:
        out = out @ _to3(M)
    return out[:2, :]


def _apply(M, pts) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    M = np.asarray(M, dtype=np.float64)
    return pts @ M[:, :2].T + M[:, 2]


def _rect_corners(w: float, h: float) -> np.ndarray:
    """좌상 → 우상 → 우하 → 좌하."""
    return np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]], dtype=np.float64)


def _decompose(M) -> tuple[float, float, float, float]:
    M = np.asarray(M, dtype=np.float64)
    a, b = M[0, 0], M[1, 0]
    return (math.hypot(a, b), math.degrees(math.atan2(b, a)),
            float(M[0, 2]), float(M[1, 2]))


def letterbox_affine(raw_w: int, raw_h: int, dst_w: int, dst_h: int) -> np.ndarray:
    """종횡비를 지키며 (dst_w,dst_h)에 맞추는 유사변환(균등배율 + 중앙 정렬).

    정사각형으로 늘려 넣지 않는 이유(문서 §7.1): 비등방 스케일은 회귀 대상인 각도 θ를
    비선형으로 왜곡시킨다. letterbox 는 유사변환이라 안전하다.
    """
    s = min(dst_w / raw_w, dst_h / raw_h)
    return _similarity(s, 0.0, (dst_w - s * raw_w) / 2.0, (dst_h - s * raw_h) / 2.0)


def umeyama_similarity(src, dst) -> np.ndarray:
    """src → dst 로 보내는 최소제곱 유사변환(2x3). 닫힌형 (Umeyama, 1991).

    모델이 낸 8개 값은 4자유도에 대해 과잉 표현인데, 여기서 4자유도로 사영되며
    잡음이 평균된다 — 과잉분이 오히려 안정성으로 돌아온다.
    """
    src = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    dst = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    if len(src) < 2:
        raise ValueError("점이 2개 미만이면 유사변환을 정할 수 없습니다")
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    C = D.T @ S / len(src)
    U, sig, Vt = np.linalg.svd(C)
    d = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ np.diag([1.0, d]) @ Vt
    var_s = (S ** 2).sum() / len(src)
    if var_s <= 1e-12:
        raise ValueError("src 점들이 한 점에 모여 있어 배율을 정할 수 없습니다")
    s = float((sig * np.array([1.0, d])).sum() / var_s)
    out = np.empty((2, 3), dtype=np.float64)
    out[:, :2] = s * R
    out[:, 2] = mu_d - s * R @ mu_s
    return out


# ── 모델 ─────────────────────────────────────────────────────────────────────────
class FramingModel:
    """클래스별 ONNX 를 들고 crop 사각형을 예측한다.

    세션은 **클래스를 처음 쓸 때** 만든다. 배포본이라도 6개를 전부 올리면 100MB,
    fold 앙상블이면 480MB 라, 그 세션에서 안 쓴 클래스까지 상주시킬 이유가 없다.
    """

    def __init__(self, models_dir: Path, meta: dict, thresholds: dict | None = None):
        self.dir = Path(models_dir)
        self.meta = meta
        self.thresholds = {**DEFAULT_THRESH, **(thresholds or {})}
        self.iw = int(meta.get("input_w", DEFAULT_INPUT_W))
        self.ih = int(meta.get("input_h", DEFAULT_INPUT_H))
        self.mean = np.array(meta.get("mean", DEFAULT_MEAN), np.float32)
        self.std = np.array(meta.get("std", DEFAULT_STD), np.float32)
        self.crop_aspect = {k: tuple(v) for k, v in meta.get("crop_aspect", {}).items()}
        self.files = self._scan()
        self._sessions: dict[str, list] = {}

    # ── 파일 탐색 ────────────────────────────────────────────────────────────
    def _scan(self) -> dict[str, list[Path]]:
        """클래스 → ONNX 파일들. `_final` 이 있으면 그것만, 없으면 fold 전부."""
        out: dict[str, list[Path]] = {}
        for cls in self.meta.get("classes", []):
            fin = self.dir / f"framing_{cls}_final.onnx"
            if fin.exists():
                out[cls] = [fin]
            else:
                folds = sorted(self.dir.glob(f"framing_{cls}_fold*.onnx"))
                if folds:
                    out[cls] = folds
        return out

    @property
    def placeholder(self) -> dict | None:
        """임시 대역(fold 체크포인트를 배포본 이름으로 쓴 것)이면 그 사유."""
        p = self.meta.get("placeholder")
        return p if isinstance(p, dict) else None

    def has(self, cls: str) -> bool:
        return cls in self.files

    def _get(self, cls: str) -> list:
        if cls not in self._sessions:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3          # 경고 억제 (기동 로그를 깨끗하게)
            self._sessions[cls] = [
                ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
                for p in self.files[cls]]
        return self._sessions[cls]

    # ── 전처리 ───────────────────────────────────────────────────────────────
    def preprocess(self, bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """raw BGR → ((1,3,ih,iw) float32, raw→입력 유사변환 A).

        학습(`framing/data.py:__getitem__`)과 같은 순서다:
        letterbox warp(검정 패딩) → BGR2RGB → /255 → ImageNet 정규화 → CHW.
        """
        h, w = bgr.shape[:2]
        A = letterbox_affine(w, h, self.iw, self.ih)
        img = cv2.warpAffine(bgr, np.asarray(A, np.float32), (self.iw, self.ih),
                             flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        x = img.astype(np.float32)[:, :, ::-1] / 255.0        # BGR→RGB
        x = (x - self.mean) / self.std
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None, ...], dtype=np.float32)
        return x, A

    def canon_wh(self, cls: str) -> tuple[float, float]:
        """예측 꼭짓점을 되돌릴 canonical 사각형 크기.

        추론에는 GT 가 없으므로 학습 때처럼 표본별 canon_wh 를 쓸 수 없다. 대신 규정
        종횡비(`data.crop_aspect`: 구내 4:3, FACE 3:4)의 사각형으로 되돌린다 — 그래서
        종횡비가 어긋난 라벨은 그 어긋남이 그대로 배포 오차의 하한이 된다(README).
        절대 크기는 무엇이든 상관없다(뒤에서 슬롯 크기로 다시 맞춰진다).
        """
        aw, ah = self.crop_aspect.get(cls, (4, 3))
        return 1200.0, 1200.0 * ah / aw

    # ── 추론 ─────────────────────────────────────────────────────────────────
    def predict(self, bgr: np.ndarray, cls: str) -> FramingResult:
        if not self.has(cls):
            return _fail(f"모델 없음({cls})")
        if bgr is None or bgr.size == 0:
            return _fail("이미지 없음")

        x, A = self.preprocess(bgr)
        sessions = self._get(cls)
        cw, ch = self.canon_wh(cls)
        Ainv = _invert(A)

        # 각 모델의 예측을 **raw 좌표 꼭짓점**까지 각각 끌고 온 뒤 평균한다. 정규화
        # 좌표에서 평균하는 것과 수학적으로 같지만(A 가 모델마다 같으니 선형), 이렇게
        # 두면 모델 간 불일치를 raw 픽셀 단위로 그대로 잴 수 있다.
        per_model = []
        for s in sessions:
            pred = s.run(None, {s.get_inputs()[0].name: x})[0]
            pts = np.asarray(pred, np.float64).reshape(4, 2) * [self.iw, self.ih]
            per_model.append(_apply(Ainv, pts))
        stack = np.stack(per_model)                  # (n,4,2)
        raw_pts = stack.mean(axis=0)

        try:
            T = umeyama_similarity(raw_pts, _rect_corners(cw, ch))
        except ValueError as e:
            return _fail(f"기하 풀이 실패({e})")

        scale, angle, _, _ = _decompose(T)
        if not np.isfinite([scale, angle]).all() or scale <= 0:
            return _fail("변환이 비정상")

        # **실제로 잘리는 사각형**은 예측 꼭짓점 8개가 아니라 그것을 4자유도로 적합한
        # 결과다(예측 4점은 정확한 직사각형이 아니다). 오버레이·배치 모두 이쪽을 쓴다.
        fitted = _apply(_invert(T), _rect_corners(cw, ch))

        # crop 폭 = 사각형 위쪽 변의 raw 픽셀 길이. 오차·산포를 여기에 정규화하면
        # 학습 리포트의 corner_mean_pct 와 같은 단위(%)가 되어 바로 비교된다.
        crop_w = float(np.linalg.norm(fitted[1] - fitted[0]))
        crop_frac = crop_w / max(1, bgr.shape[1])
        spread = float("nan")
        if len(stack) > 1 and crop_w > 0:
            spread = float(np.linalg.norm(stack - raw_pts, axis=2).mean()) / crop_w * 100

        th = self.thresholds
        why = []
        if spread == spread and spread > th["max_spread_pct"]:
            why.append(f"모델 간 불일치 {spread:.1f}%")
        if not (th["min_crop_frac"] <= crop_frac <= th["max_crop_frac"]):
            why.append(f"crop 비율 {crop_frac:.2f}")
        if abs(angle) > th["max_angle_deg"]:
            why.append(f"회전 {angle:+.1f}°")

        n = len(sessions)
        kind = "final" if self.files[cls][0].name.endswith("_final.onnx") else f"{n} folds"
        return FramingResult(
            ok=not why,
            method=f"onnx({kind})" + ("" if not why else f" — {', '.join(why)}"),
            scale=scale, angle_deg=angle, crop_frac=crop_frac, n_models=n,
            spread_pct=spread,
            score=100.0 / (1.0 + (0.0 if spread != spread else spread)),
            matrix=T.tolist(), canon_wh=(cw, ch), corners_raw=fitted.tolist())


def _fail(reason: str) -> FramingResult:
    return FramingResult(
        ok=False, method=reason, scale=1.0, angle_deg=0.0, crop_frac=1.0,
        n_models=0, spread_pct=float("nan"), score=0.0,
        matrix=[[1, 0, 0], [0, 1, 0]], canon_wh=(4.0, 3.0), corners_raw=[])


# ── 로딩 ─────────────────────────────────────────────────────────────────────────
# 코드와 함께 배포되는 메타 사본. models/ 쪽 사본은 가중치 번들을 붙여넣을 때
# 덮어써질 수 있어 git 추적에서 뺐다 — 번들 메타가 있으면 그쪽이 이기고,
# 없으면 이 사본이 쓰인다. 어느 쪽이든 코드와 짝이 맞는 값이 존재한다.
SHIPPED_META = Path(__file__).with_name("framing_meta.json")


def find_framing_dir(models_dir: str | Path) -> Path | None:
    """프레이밍 모델이 있는 폴더 — 메타 또는 framing_*.onnx 가 있는 곳."""
    base = Path(models_dir)
    for d in (base / "framing", base):
        if (d / "framing_meta.json").is_file() or any(d.glob("framing_*.onnx")):
            return d
    return None


def load_framer(cfg) -> FramingModel | None:
    """모델이 있으면 FramingModel, 없으면 None (호출자는 cover-fit 으로 물러난다).

    분류기와 달리 Mock 을 두지 않는다. 프레이밍은 "없으면 cover-fit" 이라는 멀쩡한
    기본 동작이 이미 있어서, 가짜 예측을 끼워 넣으면 그 기본 동작보다 나빠질 뿐이다.
    """
    d = find_framing_dir(cfg.resolve(cfg.paths.models_dir))
    if d is None:
        return None
    meta_p = d / "framing_meta.json"
    if not meta_p.is_file():
        meta_p = SHIPPED_META            # 번들에 메타가 없으면 배포 사본
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:
        return None
    th = getattr(cfg.thresholds, "framing", None)
    m = FramingModel(d, meta, th.model_dump() if th is not None else None)
    return m if m.files else None
