"""
좌표 변환 모듈 (Stage 5.2)

세 좌표계를 오간다:
  - cm  : PPT 물리 단위 (슬라이드 25.4×19.05, 창 8.4×6.3)
  - EMU : PPT 내부 정수 단위 (1cm = 360000 EMU)
  - px  : 검수 편집기 화면 (창을 render_px_per_cm 배율로 렌더)

사진 배치를 두 방식으로 표현하고 서로 왕복 변환한다:
  - Placement   : PPT `a:xfrm` 그대로 (off, ext, rot) — 디스크에 저장되는 진실
  - EditorState : 편집기 조작값 (dx, dy, scale, angle) — cover-fit 기준의 상대값

이 모듈은 의존성이 없어야 한다(순수 math). 웹 화면에서 본 모습과
PPT 결과가 반드시 일치하도록 test_coords.py 왕복 테스트로 고정한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EMU_PER_CM = 360000
EMU_PER_INCH = 914400
ROT_PER_DEG = 60000  # PPT rot 단위: 1/60000 도, 양수 = 시계방향


# ── 스칼라 변환 ───────────────────────────────────────────────────────────────
def cm_to_emu(cm: float) -> int:
    return int(round(cm * EMU_PER_CM))


def emu_to_cm(emu: float) -> float:
    return emu / EMU_PER_CM


def deg_to_rot(deg: float) -> int:
    """도 → PPT rot(1/60000도). 0~360으로 정규화."""
    r = int(round(deg * ROT_PER_DEG))
    return r % (360 * ROT_PER_DEG)


def rot_to_deg(rot: int) -> float:
    """PPT rot → 도. −180~+180 범위로 반환(편집기 슬라이더 친화)."""
    deg = (rot % (360 * ROT_PER_DEG)) / ROT_PER_DEG
    if deg > 180:
        deg -= 360
    return deg


# ── 기하 자료구조 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WindowCm:
    """창(슬롯)의 slide 좌표 사각형 (cm)."""
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


@dataclass(frozen=True)
class Placement:
    """PPT a:xfrm 표현 (EMU / rot 단위). 디스크에 저장되는 진실값."""
    off_x: int
    off_y: int
    ext_cx: int
    ext_cy: int
    rot: int = 0  # 1/60000 도

    @property
    def center_emu(self) -> tuple[float, float]:
        return (self.off_x + self.ext_cx / 2.0, self.off_y + self.ext_cy / 2.0)


@dataclass(frozen=True)
class EditorState:
    """편집기 조작값. cover-fit 기준(scale=1.0, dx=dy=0, angle=0)에 대한 상대값."""
    dx_px: float = 0.0     # 창 중심 대비 사진 중심 이동 (px, +x=오른쪽)
    dy_px: float = 0.0     # (px, +y=아래)
    scale: float = 1.0     # cover-fit 배율의 배수 (1.0 = 창을 꽉 채우는 최소)
    angle_deg: float = 0.0 # 회전 (도, +시계방향)


# ── cover-fit 기준 크기 ───────────────────────────────────────────────────────
def cover_base_ext_cm(photo_w: int, photo_h: int, window: WindowCm) -> tuple[float, float]:
    """
    회전 0일 때 창을 완전히 덮는 최소 사진 크기(cm). 사진 비율 유지.
    4:3 사진 → 4:3 창이면 창 크기와 동일.
    """
    if photo_w <= 0 or photo_h <= 0:
        raise ValueError("photo dimensions must be positive")
    a = photo_w / photo_h            # 사진 가로세로비
    win_a = window.w / window.h
    if a >= win_a:
        # 사진이 창보다 가로로 넓음 → 높이가 창을 결정
        h = window.h
        w = h * a
    else:
        w = window.w
        h = w / a
    return (w, h)


def min_cover_scale(angle_deg: float, base_w: float, base_h: float, window: WindowCm) -> float:
    """
    사진 중심이 창 중심에 있고 angle_deg 회전했을 때, 창(WxH)을 검은 귀퉁이
    없이 덮기 위한 최소 scale(>=... ). 창의 네 꼭짓점을 회전 좌표계로 옮겨
    사진 반폭/반높이 안에 들어오도록 요구한다.
    """
    th = math.radians(angle_deg)
    c, s = abs(math.cos(th)), abs(math.sin(th))
    hw, hh = window.w / 2.0, window.h / 2.0
    # 창 꼭짓점을 −angle 회전한 좌표에서의 최대 |x|,|y| (대칭이라 꼭짓점 하나로 충분)
    need_w = c * hw + s * hh   # 필요한 사진 반폭
    need_h = s * hw + c * hh   # 필요한 사진 반높이
    scale_w = (2 * need_w) / base_w
    scale_h = (2 * need_h) / base_h
    return max(scale_w, scale_h)


# ── EditorState ↔ Placement ──────────────────────────────────────────────────
def editor_to_placement(
    state: EditorState,
    window: WindowCm,
    base_w_cm: float,
    base_h_cm: float,
    px_per_cm: float,
) -> Placement:
    """편집기 조작값 → PPT xfrm."""
    ext_w_cm = base_w_cm * state.scale
    ext_h_cm = base_h_cm * state.scale
    cx_cm = window.cx + state.dx_px / px_per_cm
    cy_cm = window.cy + state.dy_px / px_per_cm
    off_x_cm = cx_cm - ext_w_cm / 2.0
    off_y_cm = cy_cm - ext_h_cm / 2.0
    return Placement(
        off_x=cm_to_emu(off_x_cm),
        off_y=cm_to_emu(off_y_cm),
        ext_cx=cm_to_emu(ext_w_cm),
        ext_cy=cm_to_emu(ext_h_cm),
        rot=deg_to_rot(state.angle_deg),
    )


def placement_to_editor(
    p: Placement,
    window: WindowCm,
    base_w_cm: float,
    base_h_cm: float,
    px_per_cm: float,
) -> EditorState:
    """PPT xfrm → 편집기 조작값 (editor_to_placement의 역변환)."""
    ext_w_cm = emu_to_cm(p.ext_cx)
    scale = ext_w_cm / base_w_cm
    cx_emu, cy_emu = p.center_emu
    dx_px = (emu_to_cm(cx_emu) - window.cx) * px_per_cm
    dy_px = (emu_to_cm(cy_emu) - window.cy) * px_per_cm
    return EditorState(
        dx_px=dx_px,
        dy_px=dy_px,
        scale=scale,
        angle_deg=rot_to_deg(p.rot),
    )


def cover_fit_placement(photo_w: int, photo_h: int, window: WindowCm) -> Placement:
    """초진/기본: 회전·이동 없이 창을 덮는 cover-fit 배치."""
    bw, bh = cover_base_ext_cm(photo_w, photo_h, window)
    return editor_to_placement(EditorState(), window, bw, bh, px_per_cm=1.0)


def placement_from_photo_affine(A, photo_w: int, photo_h: int) -> Placement:
    """
    사진 픽셀 → 슬라이드 cm 유사변환(2x3, [[a,b,c],[d,e,f]])을 Placement로.
    정합(new_px→창_px)에 창_px→cm를 합성한 결과를 넣는다.
    A는 유사변환(회전+균등배율+이동)이어야 한다.
    """
    a, b, c = A[0]
    d, e, f = A[1]
    scale = math.hypot(a, d)
    angle = math.degrees(math.atan2(d, a))
    ext_w = photo_w * scale
    ext_h = photo_h * scale
    # 사진 중심(px) → cm
    cx = a * (photo_w / 2) + b * (photo_h / 2) + c
    cy = d * (photo_w / 2) + e * (photo_h / 2) + f
    return Placement(
        off_x=cm_to_emu(cx - ext_w / 2),
        off_y=cm_to_emu(cy - ext_h / 2),
        ext_cx=cm_to_emu(ext_w),
        ext_cy=cm_to_emu(ext_h),
        rot=deg_to_rot(angle),
    )


def flip_editor_v(state: EditorState) -> EditorState:
    """상하반전 프레임 ↔ 원본 프레임 사이의 편집기 값 변환 (자기역원).

    교합면은 사용자가 상하반전된 화면에서 조정한다. 그 조작값은 원본 픽셀 기준
    값과 dy·angle 의 부호만 다르다 — F 를 사진 중심 기준 상하반전이라 할 때

        T(dx, dy)·R(θ)·S(k)·F  ==  F·T(dx, −dy)·R(−θ)·S(k)

    이고, 양변이 가리키는 **사진 영역은 같다**(보이는 방향만 뒤집힌다). 그래서
    슬롯을 옮겨 반전 여부가 바뀔 때 이 함수로 옮기면 잘린 영역이 보존된다.

    반사는 det=−1 이라 (dx,dy,scale,angle) 만으로는 절대 표현할 수 없다. 반전
    자체는 화면(canvas)과 PPT(`a:xfrm/@flipV`)가 따로 책임진다.
    """
    return EditorState(state.dx_px, -state.dy_px, state.scale, -state.angle_deg)


def apply_cover_clamp(state: EditorState, window: WindowCm,
                      base_w: float, base_h: float) -> EditorState:
    """회전으로 검은 귀퉁이가 생기지 않도록 scale 하한 적용(§5.1.7)."""
    need = min_cover_scale(state.angle_deg, base_w, base_h, window)
    if state.scale < need:
        return EditorState(state.dx_px, state.dy_px, need, state.angle_deg)
    return state
