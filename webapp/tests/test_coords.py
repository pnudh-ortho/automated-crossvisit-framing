"""
좌표 왕복 테스트 (사양 §5.2)
"화면에서 본 모습 ↔ PPT 결과"가 반드시 일치해야 함을 고정한다.

실행:  cd webapp && python -m pytest tests/test_coords.py -q
또는:  cd webapp && python tests/test_coords.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import coords as C  # noqa: E402

FRONT = C.WindowCm(x=8.5, y=6.375, w=8.4, h=6.3)
PXCM = 100.0


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ── cover-fit ────────────────────────────────────────────────────────────────
def test_cover_fit_4x3_fills_window_exactly():
    p = C.cover_fit_placement(1600, 1200, FRONT)
    assert approx(C.emu_to_cm(p.ext_cx), 8.4, 1e-4)
    assert approx(C.emu_to_cm(p.ext_cy), 6.3, 1e-4)
    assert approx(C.emu_to_cm(p.off_x), 8.5, 1e-4)
    assert approx(C.emu_to_cm(p.off_y), 6.375, 1e-4)
    assert p.rot == 0


def test_cover_base_ext_wide_photo_binds_on_height():
    # 16:9 사진은 창(4:3)보다 넓다 → 높이가 창 높이로 고정, 폭은 넘침
    bw, bh = C.cover_base_ext_cm(1920, 1080, FRONT)
    assert approx(bh, 6.3, 1e-9)
    assert bw > 8.4


# ── 스칼라 왕복 ───────────────────────────────────────────────────────────────
def test_deg_rot_roundtrip():
    for deg in [-10.0, -3.7, 0.0, 0.1, 5.5, 9.9]:
        assert approx(C.rot_to_deg(C.deg_to_rot(deg)), deg, 1e-6)


# ── EditorState → Placement → EditorState ────────────────────────────────────
def test_editor_placement_roundtrip():
    bw, bh = C.cover_base_ext_cm(1600, 1200, FRONT)
    states = [
        C.EditorState(),
        C.EditorState(dx_px=40, dy_px=-25, scale=1.15, angle_deg=3.2),
        C.EditorState(dx_px=-120, dy_px=60, scale=0.95, angle_deg=-7.5),
        C.EditorState(dx_px=5.5, dy_px=5.5, scale=1.333, angle_deg=0.1),
    ]
    for st in states:
        p = C.editor_to_placement(st, FRONT, bw, bh, PXCM)
        back = C.placement_to_editor(p, FRONT, bw, bh, PXCM)
        # EMU 정수 반올림 오차만 허용: 1 EMU ≈ 2.8e-4 px, 넉넉히 0.01px/0.001배율
        assert approx(back.dx_px, st.dx_px, 0.01), (back, st)
        assert approx(back.dy_px, st.dy_px, 0.01), (back, st)
        assert approx(back.scale, st.scale, 1e-3), (back, st)
        assert approx(back.angle_deg, st.angle_deg, 1e-4), (back, st)


# ── Placement → EditorState → Placement (앱이 저장하는 값의 안정성) ───────────
def test_placement_editor_placement_roundtrip():
    bw, bh = C.cover_base_ext_cm(1600, 1200, FRONT)
    p0 = C.editor_to_placement(
        C.EditorState(dx_px=33, dy_px=-17, scale=1.08, angle_deg=-2.4), FRONT, bw, bh, PXCM
    )
    st = C.placement_to_editor(p0, FRONT, bw, bh, PXCM)
    p1 = C.editor_to_placement(st, FRONT, bw, bh, PXCM)
    assert abs(p1.off_x - p0.off_x) <= 2
    assert abs(p1.off_y - p0.off_y) <= 2
    assert abs(p1.ext_cx - p0.ext_cx) <= 2
    assert abs(p1.ext_cy - p0.ext_cy) <= 2
    assert p1.rot == p0.rot


# ── 회전 시 cover 하한 ────────────────────────────────────────────────────────
def test_min_cover_scale_grows_with_rotation():
    bw, bh = C.cover_base_ext_cm(1600, 1200, FRONT)  # = 8.4 × 6.3
    assert approx(C.min_cover_scale(0.0, bw, bh, FRONT), 1.0, 1e-9)
    s5 = C.min_cover_scale(5.0, bw, bh, FRONT)
    s10 = C.min_cover_scale(10.0, bw, bh, FRONT)
    assert s5 > 1.0 and s10 > s5


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} coords tests passed.")
