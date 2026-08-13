"""
기준영상 복원 검증 (Stage 4b).
cover-fit(무회전)으로 삽입한 이미지를 복원하면 창에 원본 전체가 보이므로,
복원 결과가 원본을 창 크기로 리사이즈한 것과 일치해야 한다.

실행: cd webapp && python tests/test_ppt_reader.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import config as C  # noqa: E402
import template as T  # noqa: E402
import ppt_writer as W  # noqa: E402
import ppt_reader as Rd  # noqa: E402
from coords import editor_to_placement, cover_base_ext_cm, EditorState  # noqa: E402

# 산출물 확인용 폴더. 기본은 임시 폴더 — 특정 PC 의 경로를 박으면 다른 데서 못 돈다.
OUT = Path(os.environ.get("READER_OUT", Path(tempfile.gettempdir()) / "reader_out"))


def make_asym_image(path, size=(1600, 1200)):
    """비대칭 패턴(회전/좌우 구분 가능)."""
    img = np.full((size[1], size[0], 3), 20, np.uint8)
    cv2.rectangle(img, (0, 0), (size[0] // 2, size[1] // 2), (0, 0, 200), -1)      # 좌상 빨강
    cv2.circle(img, (size[0] - 300, 300), 150, (0, 200, 0), -1)                    # 우상 초록
    cv2.putText(img, "R", (size[0] - 260, 340), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
    cv2.rectangle(img, (100, size[1] - 300), (500, size[1] - 100), (200, 200, 0), -1)  # 좌하
    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(path)
    return size


def test_cover_fit_reconstruction_matches_original():
    cfg = C.load_config()
    OUT.mkdir(parents=True, exist_ok=True)
    tmpl = cfg.resolve(cfg.paths.template_pptx)
    prs = T.load_presentation(tmpl)
    wins = T.slot_windows(prs.slides[0], cfg.ppt.slot_names)
    Rd.set_slot_windows(wins)
    ppc = cfg.geometry.render_px_per_cm

    img_path = OUT / "asym.jpg"
    size = make_asym_image(img_path)

    dest = OUT / "recon_test.pptx"
    out = W.new_ppt_from_template(tmpl, dest)
    slide = out.slides[0]
    W.place_photo_in_slot(slide, "SLOT_FRONT", img_path, size)  # cover-fit
    out.save(str(dest))

    v = T.load_presentation(dest)
    visits = Rd.read_all_visits(v, cfg, ppc)
    assert len(visits) == 1
    ref = visits[0].slots["SLOT_FRONT"].image

    win = wins["SLOT_FRONT"]
    exp_w = int(round(win.w * ppc))
    exp_h = int(round(win.h * ppc))
    assert ref.shape[1] == exp_w and ref.shape[0] == exp_h, ref.shape

    original = cv2.imread(str(img_path))
    expected = cv2.resize(original, (exp_w, exp_h))
    mad = np.mean(np.abs(ref.astype(np.int32) - expected.astype(np.int32)))
    print(f"복원 창 크기={ref.shape[1]}x{ref.shape[0]}, 원본대비 평균절대차={mad:.2f}")
    assert mad < 6.0, f"복원이 원본과 어긋남 (MAD={mad})"
    print("PASS cover-fit 복원 = 원본 일치")


def test_rotated_reconstruction_runs_and_shapes():
    """회전·이동 배치도 오류 없이 창 크기로 복원되는지(스모크)."""
    cfg = C.load_config()
    tmpl = cfg.resolve(cfg.paths.template_pptx)
    prs = T.load_presentation(tmpl)
    wins = T.slot_windows(prs.slides[0], cfg.ppt.slot_names)
    Rd.set_slot_windows(wins)
    ppc = cfg.geometry.render_px_per_cm

    img_path = OUT / "asym.jpg"
    size = (1600, 1200)
    win = wins["SLOT_FRONT"]
    bw, bh = cover_base_ext_cm(size[0], size[1], win)
    pl = editor_to_placement(EditorState(dx_px=30, dy_px=-10, scale=1.2, angle_deg=5.0),
                             win, bw, bh, ppc)

    dest = OUT / "recon_rot.pptx"
    out = W.new_ppt_from_template(tmpl, dest)
    slide = out.slides[0]
    W.place_photo_in_slot(slide, "SLOT_FRONT", img_path, size, placement=pl)
    out.save(str(dest))

    v = T.load_presentation(dest)
    visits = Rd.read_all_visits(v, cfg, ppc)
    ref = visits[0].slots["SLOT_FRONT"]
    assert ref.image.shape[1] == int(round(win.w * ppc))
    assert abs(ref.rot_deg - 5.0) < 0.01, ref.rot_deg
    print(f"PASS 회전 복원 스모크 (rot={ref.rot_deg}°, shape={ref.image.shape})")


if __name__ == "__main__":
    test_cover_fit_reconstruction_matches_original()
    test_rotated_reconstruction_runs_and_shapes()
    print("\n✅ ppt_reader 테스트 통과")
