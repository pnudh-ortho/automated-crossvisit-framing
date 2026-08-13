"""
상하반전(교합면) 검증.

이 기능의 전제는 단 하나다 — **반사는 (dx,dy,scale,angle) 로 표현할 수 없다**
(det=−1). 그래서 반전은 화면(canvas)과 PPT(`a:xfrm/@flipV`)가 따로 책임지고,
편집기 값은 "반전된 화면 기준"으로 저장한다. 그 규약이 writer·reader·coords
셋 사이에서 어긋나지 않는지 여기서 고정한다.

실행: cd webapp && python tests/test_flip_v.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import config as C  # noqa: E402
import template as T  # noqa: E402
import ppt_writer as W  # noqa: E402
import ppt_reader as Rd  # noqa: E402
from coords import (EditorState, cover_base_ext_cm, editor_to_placement,  # noqa: E402
                    flip_editor_v)

from test_ppt_reader import OUT, make_asym_image  # noqa: E402


# ── 순수 수학 ────────────────────────────────────────────────────────────────
def test_flip_editor_v_negates_dy_and_angle():
    st = EditorState(dx_px=37.0, dy_px=-21.0, scale=1.24, angle_deg=8.5)
    f = flip_editor_v(st)
    assert f.dx_px == st.dx_px, "가로 이동은 상하반전에 영향받지 않는다"
    assert f.scale == st.scale, "배율은 상하반전에 영향받지 않는다"
    assert f.dy_px == -st.dy_px
    assert f.angle_deg == -st.angle_deg
    print("PASS flip_editor_v: dy·angle 만 부호 반전")


def test_flip_editor_v_is_involution():
    st = EditorState(dx_px=-3.5, dy_px=12.0, scale=0.93, angle_deg=-4.25)
    back = flip_editor_v(flip_editor_v(st))
    assert back == st, back
    # cover-fit(항등)은 반전해도 항등 — 초기값에 부작용이 없다는 뜻
    assert flip_editor_v(EditorState()) == EditorState()
    print("PASS flip_editor_v: 자기역원, cover-fit 불변")


# ── PPT 왕복 ─────────────────────────────────────────────────────────────────
def _setup():
    cfg = C.load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    tmpl = cfg.resolve(cfg.paths.template_pptx)
    prs = T.load_presentation(tmpl)
    wins = T.slot_windows(prs.slides[0], cfg.ppt.slot_names)
    Rd.set_slot_windows(wins)
    return cfg, tmpl, wins, cfg.geometry.render_px_per_cm


def _render(tmpl, dest, win, img_path, size, state, flip_v, ppc):
    """placement 로 한 장 넣고 PPT 로 저장한 뒤, 창에서 보였던 픽셀을 복원."""
    bw, bh = cover_base_ext_cm(size[0], size[1], win)
    pl = editor_to_placement(state, win, bw, bh, ppc)
    out = W.new_ppt_from_template(tmpl, dest)
    W.place_photo_in_slot(out.slides[0], "SLOT_UPPER", img_path, size,
                          placement=pl, flip_v=flip_v)
    out.save(str(dest))
    cfg = C.load_config()
    return Rd.read_all_visits(T.load_presentation(dest), cfg, ppc)[0].slots["SLOT_UPPER"]


def test_writer_sets_and_reader_reads_flip_v():
    cfg, tmpl, wins, ppc = _setup()
    img_path = OUT / "asym.jpg"
    size = make_asym_image(img_path)
    win = wins["SLOT_UPPER"]

    on = _render(tmpl, OUT / "flip_on.pptx", win, img_path, size,
                 EditorState(), True, ppc)
    off = _render(tmpl, OUT / "flip_off.pptx", win, img_path, size,
                  EditorState(), False, ppc)
    assert on.flip_v is True, "flipV 를 썼는데 읽히지 않는다"
    assert off.flip_v is False, "flipV 를 안 썼는데 읽힌다"
    print("PASS flipV writer→reader 왕복")


def test_flipped_cover_fit_is_vertical_mirror():
    """cover-fit + flipV 복원 = 반전 없는 복원을 상하로 뒤집은 것."""
    cfg, tmpl, wins, ppc = _setup()
    img_path = OUT / "asym.jpg"
    size = make_asym_image(img_path)
    win = wins["SLOT_UPPER"]

    on = _render(tmpl, OUT / "flip_on.pptx", win, img_path, size,
                 EditorState(), True, ppc)
    off = _render(tmpl, OUT / "flip_off.pptx", win, img_path, size,
                  EditorState(), False, ppc)
    mad = np.mean(np.abs(on.image.astype(np.int32)
                         - cv2.flip(off.image, 0).astype(np.int32)))
    print(f"  cover-fit 반전 복원 평균절대차={mad:.2f}")
    assert mad < 3.0, f"반전 복원이 상하 미러와 어긋남 (MAD={mad})"
    print("PASS cover-fit + flipV = 상하 미러")


def test_rotated_flip_matches_flip_editor_v():
    """이 기능의 핵심 불변식.

        복원(flipV=1, S)  ==  상하미러( 복원(flipV=0, flip_editor_v(S)) )

    좌변은 "사용자가 반전 화면에서 S 로 조정한 결과", 우변은 "같은 사진 영역을
    원본 프레임 값으로 놓고 뒤집어 본 것"이다. 회전이 섞여도 같아야 한다 —
    여기가 어긋나면 검수 화면과 PPT 결과가 달라진다.
    """
    cfg, tmpl, wins, ppc = _setup()
    img_path = OUT / "asym.jpg"
    size = make_asym_image(img_path)
    win = wins["SLOT_UPPER"]

    st = EditorState(dx_px=24.0, dy_px=-13.0, scale=1.18, angle_deg=6.5)
    on = _render(tmpl, OUT / "flip_rot_on.pptx", win, img_path, size, st, True, ppc)
    off = _render(tmpl, OUT / "flip_rot_off.pptx", win, img_path, size,
                  flip_editor_v(st), False, ppc)

    mad = np.mean(np.abs(on.image.astype(np.int32)
                         - cv2.flip(off.image, 0).astype(np.int32)))
    print(f"  회전 포함 불변식 평균절대차={mad:.2f}")
    assert mad < 3.0, f"flip_editor_v 규약이 PPT 렌더와 어긋남 (MAD={mad})"
    print("PASS 회전 포함 flip_editor_v ↔ flipV 일치")


def test_flip_v_slots_configured():
    cfg = C.load_config()
    assert "SLOT_UPPER" in cfg.flip_v_slots and "SLOT_LOWER" in cfg.flip_v_slots
    # 교합면 외에는 절대 반전되면 안 된다
    for k in ("SLOT_FRONT", "SLOT_LEFT", "SLOT_RIGHT"):
        assert k not in cfg.flip_v_slots, f"{k} 가 반전 대상에 들어 있다"
    print(f"PASS 반전 슬롯 = {cfg.flip_v_slots}")


if __name__ == "__main__":
    test_flip_editor_v_negates_dy_and_angle()
    test_flip_editor_v_is_involution()
    test_flip_v_slots_configured()
    test_writer_sets_and_reader_reads_flip_v()
    test_flipped_cover_fit_is_vertical_mirror()
    test_rotated_flip_matches_flip_editor_v()
    print("\n✅ 상하반전 테스트 통과")
