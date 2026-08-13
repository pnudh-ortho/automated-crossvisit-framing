"""
창에 보이는 그림을 픽셀로 굽는다 (검수 화면 → PPT).

PPT 에는 **원본을 통째로** 넣고 삐져나온 부분을 양식 마스크가 가리는 구조였다.
그런데 십자뷰의 슬롯 사이 마스크는 0.5mm 짜리 실선뿐이라, 자동 프레이밍이 사진을
확대해 잡으면(배율 1.8 안팎) 좌우로 3cm 넘게 번져 **옆 슬롯을 덮어 버린다**.
z-순서상 LEFT 가 FRONT 를, FRONT 가 RIGHT 를 가린다.

그래서 창에 보이는 만큼만 미리 잘라서(회전·반전까지 구워서) 넣는다. 그러면

  · 사진이 창과 정확히 같은 크기라 삐져나올 것이 없다 → 마스크·z-순서와 무관
  · 도형 회전이 0 이라 얼굴 슬라이드(마스크 없음)에서도 이웃을 침범하지 않는다
  · PPT 에 박히는 화소가 원본의 1/10 수준으로 줄어든다

원본 사진 파일은 환자 폴더에 그대로 저장된다 — 굽는 것은 PPT 안의 사본뿐이다.

## 편집기와 같은 변환이어야 한다

여기 산식은 `frontend/app.js: drawComposite + coverDraw` 와 한 몸이다. 어긋나면
화면과 PPT 가 조용히 달라지므로 `tests/test_crop.py` 가 두 모델을 대조해 고정한다.

    사진픽셀 → (중심 이동) → cover 배율 k → 반전 → 배율 s → 회전 θ → 창 중심 + (dx,dy)
"""

from __future__ import annotations

import cv2
import numpy as np

from coords import EditorState, WindowCm


def cover_factor(img_w: int, img_h: int, canvas_w: float, canvas_h: float) -> float:
    """창을 빈틈없이 덮는 최소 배율 — app.js 의 coverDraw 와 같은 규약."""
    return max(canvas_w / img_w, canvas_h / img_h)


def window_affine(img_w: int, img_h: int, win: WindowCm, st: EditorState,
                  flip_v: bool, out_w: int, out_h: int, editor_ppc: float) -> np.ndarray:
    """사진 픽셀 → 출력 픽셀 아핀(2x3).

    편집기는 창을 `editor_ppc` px/cm 로 그리고 dx·dy 도 그 단위다. 출력은 그보다
    촘촘할 수 있으므로(export_px_per_cm) 마지막에 배율 r 로 옮긴다.
    """
    cw, ch = win.w * editor_ppc, win.h * editor_ppc      # 편집기 캔버스 크기(px)
    r = out_w / cw                                        # 편집기 px → 출력 px
    k = cover_factor(img_w, img_h, cw, ch)

    th = np.radians(st.angle_deg)
    cos, sin = np.cos(th), np.sin(th)
    R = np.array([[cos, -sin], [sin, cos]])               # y-아래에서 시계방향(+)
    F = np.array([[1.0, 0.0], [0.0, -1.0 if flip_v else 1.0]])
    A = r * (R @ (st.scale * (F * k)))                    # 선형부
    # 사진 중심이 창 중심 + (dx,dy) 로 간다
    center = np.array([cw / 2 + st.dx_px, ch / 2 + st.dy_px])
    b = r * center - A @ np.array([img_w / 2, img_h / 2])
    return np.hstack([A, b.reshape(2, 1)])


def render_window(img_bgr: np.ndarray, win: WindowCm, st: EditorState, flip_v: bool,
                  px_per_cm: float, editor_ppc: float,
                  letterbox_bgr=(0, 0, 0)) -> np.ndarray:
    """검수 화면의 창에 보이는 그림을 그대로 만들어 낸다 (BGR)."""
    ih, iw = img_bgr.shape[:2]
    out_w = max(1, int(round(win.w * px_per_cm)))
    out_h = max(1, int(round(win.h * px_per_cm)))

    src = img_bgr
    M = window_affine(iw, ih, win, st, flip_v, out_w, out_h, editor_ppc)
    # 많이 줄일 때는 warpAffine 의 선형보간만으로 계단이 생긴다. 면적 평균으로
    # 미리 줄여 두면(INTER_AREA) 같은 값에서 훨씬 깨끗하다. cover 배율 k 가 사진
    # 크기에서 나오므로, 줄인 사진으로 아핀을 다시 구하면 그대로 맞는다.
    shrink = 1.0 / max(1e-9, np.sqrt(abs(np.linalg.det(M[:, :2]))))
    if shrink >= 2.0:
        f = 1.0 / np.floor(shrink)
        src = cv2.resize(img_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        M = window_affine(src.shape[1], src.shape[0], win, st, flip_v,
                          out_w, out_h, editor_ppc)

    return cv2.warpAffine(src, M, (out_w, out_h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=letterbox_bgr)


def hex_to_bgr(rgb_hex: str) -> tuple[int, int, int]:
    """'000000' → (B, G, R). 못 읽으면 검정."""
    h = (rgb_hex or "").strip().lstrip("#")
    if len(h) != 6:
        return (0, 0, 0)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)
    return (b, g, r)
