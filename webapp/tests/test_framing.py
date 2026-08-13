"""
자동 프레이밍 테스트 (문서 §12)

여기서 고정하려는 것은 두 가지다.

1. **학습 코드와 같은 규약인가.** webapp은 PyTorch를 반입하지 않아
   `train_move_rotate_crop/framing/` 을 import 할 수 없고, 전처리·기하를 다시 구현해
   두었다. 어긋나면 예외가 아니라 조용한 정확도 저하로만 나타나므로 값으로 못박는다.

2. **예측 → 배치 환산이 맞는가.** 핵심 테스트는 "모델이 cover-fit 과 똑같은 사각형을
   예측하면 편집기 상태가 정확히 기본값(EditorState())이어야 한다" 이다. 이게 맞으면
   좌표계 사슬(raw → canonical → 창 px → cm → EMU) 전체가 맞는 것이다.

실행:  cd webapp && python -m pytest tests/test_framing.py -q
"""
import json
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import coords as C  # noqa: E402
import framing as F  # noqa: E402

MODELS = os.path.join(os.path.dirname(__file__), "..", "backend", "models", "framing")
FRONT = C.WindowCm(x=8.5, y=6.375, w=8.4, h=6.3)
PXCM = 100.0


# ── 1. 학습 코드와 같은 규약인가 ──────────────────────────────────────────────
def test_letterbox_matches_training_formula():
    """framing/geometry.py:letterbox_affine 과 같은 식이어야 한다.

    raw 6000x4000(3:2) → 336x224(3:2)는 종횡비가 같아 패딩이 0이고 배율이 딱 떨어진다.
    """
    A = F.letterbox_affine(6000, 4000, 336, 224)
    assert A[0, 0] == pytest.approx(336 / 6000)
    assert A[1, 1] == pytest.approx(224 / 4000)
    assert A[0, 2] == pytest.approx(0.0)   # 종횡비가 같으니 남는 공간이 없다
    assert A[1, 2] == pytest.approx(0.0)


def test_letterbox_pads_not_stretches():
    """4:3 raw 를 3:2 입력에 넣으면 **좌우에 패딩**이 생기고 배율은 등방이어야 한다.

    비등방으로 늘리면 회귀 대상인 각도 θ가 비선형으로 왜곡된다(문서 §7.1).
    """
    A = F.letterbox_affine(4000, 3000, 336, 224)
    s = min(336 / 4000, 224 / 3000)
    assert A[0, 0] == pytest.approx(s)
    assert A[1, 1] == pytest.approx(s)          # x배율 == y배율
    assert A[0, 2] == pytest.approx((336 - s * 4000) / 2)
    assert A[1, 2] == pytest.approx(0.0, abs=1e-9)


def test_umeyama_recovers_known_similarity():
    """유사변환을 정확히 되찾는가 (닫힌형이므로 오차 ~1e-12)."""
    th = math.radians(17.0)
    s = 0.37
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]]) * s
    T = np.hstack([R, np.array([[123.0], [-45.0]])])
    src = np.array([[0.0, 0.0], [900.0, 0.0], [900.0, 700.0], [0.0, 700.0]])
    dst = src @ T[:, :2].T + T[:, 2]
    got = F.umeyama_similarity(src, dst)
    assert np.allclose(got, T, atol=1e-9)


def test_umeyama_rejects_degenerate():
    with pytest.raises(ValueError):
        F.umeyama_similarity(np.zeros((4, 2)), np.ones((4, 2)))


def test_preprocess_constants_match_training():
    """ImageNet 상수와 입력 크기는 학습(framing/data.py)과 같아야 한다."""
    assert F.DEFAULT_MEAN == (0.485, 0.456, 0.406)
    assert F.DEFAULT_STD == (0.229, 0.224, 0.225)
    # raw 가 3:2 로 확정되어 336x224 를 쓴다 (문서 §7.1, 320x240 은 4:3 raw 전용값)
    assert (F.DEFAULT_INPUT_W, F.DEFAULT_INPUT_H) == (336, 224)


# ── 2. 예측 → 배치 환산 ───────────────────────────────────────────────────────
def _cover_fit_T(raw_w, raw_h, canon_w, canon_h):
    """framing/geometry.py:cover_fit_T 와 같은 식 — 회전 0, 중심, 창을 덮는 최소 배율."""
    s = max(canon_w / raw_w, canon_h / raw_h)
    return np.array([[s, 0.0, canon_w / 2 - s * raw_w / 2],
                     [0.0, s, canon_h / 2 - s * raw_h / 2]])


def _editor_from(T, canon_wh, raw_w, raw_h, win=FRONT):
    """main.framing_to_editor 와 같은 환산 (main 을 import 하면 모델·템플릿까지 끌려온다)."""
    cw, ch = canon_wh
    Wpx, Hpx = win.w * PXCM, win.h * PXCM
    k = min(Wpx / cw, Hpx / ch)
    Ccan = np.array([[k, 0.0, (Wpx - k * cw) / 2.0],
                     [0.0, k, (Hpx - k * ch) / 2.0]])
    M = (np.vstack([Ccan, [0, 0, 1]]) @ np.vstack([T, [0, 0, 1]]))[:2, :]
    # main.registration_to_editor 와 같은 사슬
    Twin = np.array([[1.0 / PXCM, 0.0, win.x], [0.0, 1.0 / PXCM, win.y]])
    A = (np.vstack([Twin, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]]))[:2, :]
    pl = C.placement_from_photo_affine(A.tolist(), raw_w, raw_h)
    bw, bh = C.cover_base_ext_cm(raw_w, raw_h, win)
    return C.placement_to_editor(pl, win, bw, bh, PXCM)


def test_cover_fit_prediction_gives_identity_editor():
    """**핵심 테스트.** 모델이 cover-fit 사각형을 예측하면 편집기는 기본값이어야 한다.

    좌표계 사슬(raw → canonical → 창 px → cm → EMU)이 한 군데라도 어긋나면 깨진다.
    raw 는 실측값 6000x4000(3:2), canonical 은 구내 규정 4:3.
    """
    T = _cover_fit_T(6000, 4000, 1200, 900)
    st = _editor_from(T, (1200, 900), 6000, 4000)
    assert st.dx_px == pytest.approx(0.0, abs=1e-6)
    assert st.dy_px == pytest.approx(0.0, abs=1e-6)
    assert st.scale == pytest.approx(1.0, abs=1e-9)
    assert st.angle_deg == pytest.approx(0.0, abs=1e-9)


def test_zoom_in_prediction_raises_scale():
    """더 좁게 자르라는 예측(확대)은 scale > 1 로 나와야 한다."""
    # cover-fit 은 폭 5333px 를 쓴다. 절반만 쓰면 2배로 확대된 셈이다.
    base = _cover_fit_T(6000, 4000, 1200, 900)
    tighter = base.copy()
    tighter[:, :2] *= 2.0
    # 같은 중심을 유지하도록 이동 성분을 다시 잡는다
    center_raw = np.array([3000.0, 2000.0])
    tighter[:, 2] = np.array([600.0, 450.0]) - tighter[:, :2] @ center_raw
    st = _editor_from(tighter, (1200, 900), 6000, 4000)
    assert st.scale == pytest.approx(2.0, rel=1e-6)
    assert st.dx_px == pytest.approx(0.0, abs=1e-6)


def test_rotated_prediction_carries_angle():
    """회전 예측이 편집기 각도로 그대로 넘어와야 한다 (부호 포함)."""
    deg = 6.0
    th = math.radians(deg)
    s = 0.225
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]]) * s
    T = np.hstack([R, (np.array([600.0, 450.0]) - R @ np.array([3000.0, 2000.0]))[:, None]])
    st = _editor_from(T, (1200, 900), 6000, 4000)
    assert st.angle_deg == pytest.approx(deg, abs=1e-6)


def test_face_aspect_is_portrait():
    """FACE 는 3:4 세로다 — 구내 4:3 과 뒤집히면 안 된다."""
    meta = {"crop_aspect": {"FACE": [3, 4], "IO_FRONT": [4, 3]}, "classes": []}
    m = F.FramingModel(MODELS, meta)
    fw, fh = m.canon_wh("FACE")
    iw, ih = m.canon_wh("IO_FRONT")
    assert fw / fh == pytest.approx(3 / 4)
    assert iw / ih == pytest.approx(4 / 3)


# ── 3. 신뢰도 게이트 ──────────────────────────────────────────────────────────
def test_missing_class_fails_softly():
    """모르는 클래스는 예외가 아니라 ok=False 로 — 상위가 cover-fit 으로 물러난다."""
    m = F.FramingModel(MODELS, {"classes": []})
    r = m.predict(np.zeros((100, 150, 3), np.uint8), "NOPE")
    assert r.ok is False
    assert r.n_models == 0
    assert "모델 없음" in r.method


def test_thresholds_default_matches_config_defaults():
    """framing.py 기본값과 config.py 의 FramingThresholds 기본값이 갈리면 안 된다."""
    import config as CFG
    d = CFG.FramingThresholds().model_dump()
    assert d == F.DEFAULT_THRESH


# ── 4. 실제 모델 (없으면 건너뜀) ──────────────────────────────────────────────
def _have_models():
    return os.path.isfile(os.path.join(MODELS, "framing_meta.json"))


@pytest.mark.skipif(not _have_models(), reason="framing ONNX 없음")
def test_meta_declares_training_constants():
    meta = json.load(open(os.path.join(MODELS, "framing_meta.json"), encoding="utf-8"))
    assert (meta["input_w"], meta["input_h"]) == (336, 224)
    assert tuple(np.round(meta["mean"], 3)) == F.DEFAULT_MEAN
    assert tuple(np.round(meta["std"], 3)) == F.DEFAULT_STD
    assert meta["crop_aspect"]["FACE"] == [3, 4]
    for c in ("IO_FRONT", "IO_LEFT", "IO_RIGHT", "IO_UPPER", "IO_LOWER"):
        assert meta["crop_aspect"][c] == [4, 3]


def test_fitted_rect_is_exact_rectangle():
    """배포되는 사각형은 **정확한 직각·정확한 규정 종횡비**여야 한다.

    모델이 내는 꼭짓점 4개는 직사각형이 아니다(8개 값 = 4자유도에 대한 과잉 표현).
    실제로 잘리는 것은 그것을 Umeyama 로 적합한 결과이고, 그 결과는 어떤 예측이
    들어와도 직사각형이어야 한다 — 유사변환은 각도와 비율을 보존하기 때문이다.
    이건 표본으로 확인할 성질이 아니라 항등식이므로 이미지 없이 검사한다.
    """
    rng = np.random.default_rng(0)
    for _ in range(20):
        # 임의의 '예측 꼭짓점' (일부러 직사각형이 아니게 흔든다)
        pred = np.array([[0.1, 0.1], [0.9, 0.12], [0.88, 0.85], [0.12, 0.9]])
        pred = pred + rng.normal(0, 0.03, pred.shape)
        raw_pts = pred * [6000, 4000]
        cw, ch = 1200.0, 900.0
        T = F.umeyama_similarity(raw_pts, F._rect_corners(cw, ch))
        fitted = F._apply(F._invert(T), F._rect_corners(cw, ch))

        top, bottom = fitted[1] - fitted[0], fitted[2] - fitted[3]
        left = fitted[3] - fitted[0]
        assert np.allclose(top, bottom, atol=1e-6)                 # 마주보는 변이 평행
        assert abs(float(np.dot(top, left))) < 1e-6                # 직각
        assert (np.linalg.norm(top) / np.linalg.norm(left)
                == pytest.approx(cw / ch, rel=1e-9))               # 규정 종횡비


@pytest.mark.skipif(not _have_models(), reason="framing ONNX 없음")
def test_real_model_all_classes_load():
    meta = json.load(open(os.path.join(MODELS, "framing_meta.json"), encoding="utf-8"))
    m = F.FramingModel(MODELS, meta)
    for cls in meta["classes"]:
        assert m.has(cls), f"{cls} 모델 없음"
        assert len(m._get(cls)) >= 1
