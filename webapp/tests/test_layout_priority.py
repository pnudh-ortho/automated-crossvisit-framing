"""
기존 PPT 레이아웃 우선 검증.

템플릿의 십자뷰 좌표는 이상적이지만, 이미 만들어진 환자 PPT는 상단 여백·좌우
여백·사진 간격이 조금씩 다를 수 있다. 새 차수만 템플릿 좌표로 넣으면 그
슬라이드만 어긋나므로, 기존 PPT가 쓰던 창을 우선으로 삼아야 한다.

여기서 검증하는 것:
  - BACKDROP_<슬롯> 도형에서 창을 정확히 되읽는가
  - 사진이 없는 앵커뿐인 슬라이드는 기준으로 삼지 않는가
  - 레이아웃이 도중에 바뀐 PPT는 '가장 마지막 차수'를 따르는가

실행: cd webapp && python -m pytest tests/test_layout_priority.py -q
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ppt_writer as W  # noqa: E402
import template as T  # noqa: E402
from coords import WindowCm  # noqa: E402

SLOTS = ["SLOT_UPPER", "SLOT_LEFT", "SLOT_FRONT", "SLOT_RIGHT", "SLOT_LOWER"]


def _photo(tmp_path, name="p.jpg", size=(400, 300)):
    p = tmp_path / name
    Image.new("RGB", size, (120, 160, 200)).save(p)
    return p, size


def _shifted(base: dict[str, WindowCm], dx: float, dy: float, shrink: float = 0.0):
    """레이아웃을 통째로 옮기고 줄인다 — '조금 다른 PPT'를 흉내낸다."""
    return {
        k: WindowCm(x=v.x + dx, y=v.y + dy, w=v.w - shrink, h=v.h - shrink)
        for k, v in base.items()
    }


def _template_windows():
    """템플릿을 열어 (프레젠테이션, 슬롯 창)을 준다. 매 테스트마다 새로 연다."""
    import config as C
    cfg = C.load_config()
    prs = T.load_presentation(cfg.resolve(cfg.paths.template_pptx))
    return prs, T.slot_windows(prs.slides[0], SLOTS)


def test_read_slot_windows_recovers_backdrop_geometry(tmp_path):
    """사진을 넣은 뒤에도 창을 그대로 되읽을 수 있어야 한다."""
    prs, base = _template_windows()
    slide = prs.slides[0]
    img, wh = _photo(tmp_path)

    moved = _shifted(base, dx=0.35, dy=0.20, shrink=0.10)
    for slot in SLOTS:
        W.place_photo_in_window(slide, slot, moved[slot], img, wh)

    got = W.read_slot_windows(slide, SLOTS)
    assert set(got) == set(SLOTS)
    for slot in SLOTS:
        assert got[slot].x == round(moved[slot].x, 4) or abs(got[slot].x - moved[slot].x) < 1e-3
        assert abs(got[slot].y - moved[slot].y) < 1e-3
        assert abs(got[slot].w - moved[slot].w) < 1e-3
        assert abs(got[slot].h - moved[slot].h) < 1e-3


def test_empty_slot_falls_back_to_anchor(tmp_path):
    """그 차수에 비어 있던 슬롯은 앵커가 남아 있으니 그걸로 읽힌다."""
    prs, base = _template_windows()
    slide = prs.slides[0]
    img, wh = _photo(tmp_path)

    moved = _shifted(base, dx=0.5, dy=0.0)
    # LOWER만 비워 둔다 → 앵커가 그대로 남는다
    for slot in SLOTS:
        if slot != "SLOT_LOWER":
            W.place_photo_in_window(slide, slot, moved[slot], img, wh)

    got = W.read_slot_windows(slide, SLOTS)
    assert set(got) == set(SLOTS)
    assert abs(got["SLOT_FRONT"].x - moved["SLOT_FRONT"].x) < 1e-3   # 옮긴 대로
    assert abs(got["SLOT_LOWER"].x - base["SLOT_LOWER"].x) < 1e-3    # 앵커(템플릿) 그대로


def test_layout_from_ppt_prefers_last_photo_slide(tmp_path):
    """
    사진이 든 슬라이드만 기준이 되고, 여러 장이면 마지막 것을 따른다.
    (앵커만 있는 빈 슬라이드는 무시)
    """
    import main as M

    prs, base = _template_windows()
    img, wh = _photo(tmp_path)

    first = prs.slides[0]
    early = _shifted(base, dx=0.9, dy=0.9)
    for slot in SLOTS:
        W.place_photo_in_window(first, slot, early[slot], img, wh)

    # 두 번째 차수 슬라이드 — 레이아웃이 살짝 달라졌다
    second = W.import_template_slide(prs, M.TEMPLATE_PRS, len(prs.slides._sldIdLst))
    late = _shifted(base, dx=0.15, dy=0.40, shrink=0.05)
    for slot in SLOTS:
        W.place_photo_in_window(second, slot, late[slot], img, wh)

    # 세 번째 — 앵커만 있고 사진은 없다. 기준이 되면 안 된다.
    W.import_template_slide(prs, M.TEMPLATE_PRS, len(prs.slides._sldIdLst))

    out = tmp_path / "patient.pptx"
    prs.save(str(out))

    got = M._layout_from_ppt(T.load_presentation(out))
    for slot in SLOTS:
        assert abs(got[slot].x - late[slot].x) < 1e-3, slot
        assert abs(got[slot].y - late[slot].y) < 1e-3, slot
        assert abs(got[slot].w - late[slot].w) < 1e-3, slot


def test_layout_from_ppt_falls_back_to_template(tmp_path):
    """차수 슬라이드가 하나도 없으면 템플릿 좌표 그대로."""
    import main as M

    prs, base = _template_windows()
    out = tmp_path / "empty.pptx"
    prs.save(str(out))

    got = M._layout_from_ppt(T.load_presentation(out))
    for slot in SLOTS:
        assert abs(got[slot].x - M.SLOT_WINDOWS[slot].x) < 1e-6, slot
        assert abs(got[slot].h - M.SLOT_WINDOWS[slot].h) < 1e-6, slot
