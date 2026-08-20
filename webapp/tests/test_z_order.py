"""겹치는 수제 덱에서 **어느 사진이 위로 오는가** — 직전 차수에서 물려받는다.

수제 십자뷰는 사진끼리 조금씩 포개진다(실측 덱 대부분이 상악과 정면이 1.6mm).
그 겹침에서 위로 오는 사진은 사람이 정한 것인데, 예전에는 새 차수만 `slot_names`
순으로 넣어 **상악이 맨 위**가 됐다 — 사람이 만든 장은 정면이 맨 위였으므로 같은
덱 안에서 겹침 방향이 갈렸다.

실행: cd webapp && python -m pytest tests/test_z_order.py -q
"""
import io
import os
import sys

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Cm
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402
import template as T  # noqa: E402
import ppt_writer as W  # noqa: E402
from coords import emu_to_cm  # noqa: E402

client = TestClient(M.app)

NAME, HOSP, ORTHO = "겹침검사", "123456789", "12345"
FOLDER = f"{NAME}_{HOSP}_{ORTHO}"
DECK = f"{NAME}({ORTHO}).pptx"

W_CM, H_CM = 8.4, 6.3
OVERLAP = 0.16          # 실측 덱에서 상악과 정면이 포개져 있던 만큼
# 놓는 순서 = 그리는 순서(아래 → 위). 정면을 **맨 나중**에 놓아 맨 위로 올린다.
HAND_MADE_ORDER = ["SLOT_LEFT", "SLOT_RIGHT", "SLOT_UPPER", "SLOT_LOWER", "SLOT_FRONT"]


def _png(tmp_path):
    p = tmp_path / "dot.png"
    Image.new("RGB", (8, 6), (90, 90, 90)).save(p)
    return str(p)


def _jpg(seed: int) -> bytes:
    im = Image.new("RGB", (1200, 900), (40 + seed * 30, 90, 200 - seed * 30))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _hand_made_deck(path, png):
    """사진이 서로 조금씩 포개진 수제 십자뷰 한 장짜리 덱."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Cm(33.87), Cm(19.05)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    cx = emu_to_cm(prs.slide_width) / 2 - W_CM / 2
    cy = emu_to_cm(prs.slide_height) / 2 - H_CM / 2
    # 겹치도록 간격을 음수로 준다 — 상악/하악이 정면과 OVERLAP 만큼 포개진다
    at = {"SLOT_FRONT": (cx, cy),
          "SLOT_UPPER": (cx, cy - H_CM + OVERLAP),
          "SLOT_LOWER": (cx, cy + H_CM - OVERLAP),
          "SLOT_LEFT":  (cx - W_CM + OVERLAP, cy),
          "SLOT_RIGHT": (cx + W_CM - OVERLAP, cy)}
    for slot in HAND_MADE_ORDER:                     # 놓는 순서가 곧 z-순서다
        x, y = at[slot]
        slide.shapes.add_picture(png, Cm(x), Cm(y), Cm(W_CM), Cm(H_CM))
    tb = slide.shapes.add_textbox(Cm(0.5), Cm(0.2), Cm(10), Cm(0.9))
    tb.text_frame.text = "24.06.05 (초진 A)"
    prs.save(str(path))
    return prs


def _slide_ctr(prs):
    return (emu_to_cm(prs.slide_width) / 2, emu_to_cm(prs.slide_height) / 2)


def test_수제_장의_z순서를_읽는다(_isolate_paths, tmp_path):
    """자리로 사진을 가른 뒤, 그리는 순서 그대로 돌려줘야 한다."""
    deck = tmp_path / DECK
    prs = _hand_made_deck(deck, _png(tmp_path))
    got = M._slide_slot_z_order(prs.slides[0], _slide_ctr(prs))
    assert got == HAND_MADE_ORDER, got
    assert got[-1] == "SLOT_FRONT", "사람이 맨 위에 둔 것은 정면이다"


def test_앱이_쓴_장은_도형_이름으로_읽는다(_isolate_paths, tmp_path):
    """이름(PHOTO_SLOT_*)이 있으면 자리 추정보다 그쪽이 진실이다."""
    png = _png(tmp_path)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Cm(33.87), Cm(19.05)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    order = ["SLOT_LOWER", "SLOT_RIGHT", "SLOT_FRONT", "SLOT_LEFT", "SLOT_UPPER"]
    for i, slot in enumerate(order):                 # 자리는 뒤죽박죽이어도 된다
        pic = slide.shapes.add_picture(png, Cm(1 + i * 0.1), Cm(1), Cm(W_CM), Cm(H_CM))
        pic.name = W.PHOTO_NAME_PREFIX + slot
    assert M._slide_slot_z_order(slide, _slide_ctr(prs)) == order


def test_새_차수가_직전_장의_겹침_방향을_잇는다(_isolate_paths, tmp_path):
    """확정한 슬라이드의 z-순서가 직전 차수와 같아야 한다."""
    root = _isolate_paths
    d = root / FOLDER
    d.mkdir(parents=True)
    _hand_made_deck(d / DECK, _png(tmp_path))

    r = client.post("/api/session", json={"folder": FOLDER})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    s = M.SESSIONS[sid]
    assert s.slot_z == HAND_MADE_ORDER, s.slot_z

    files = [("files", (f"p{i}.jpg", io.BytesIO(_jpg(i)), "image/jpeg"))
             for i in range(5)]
    assert client.post(f"/api/photos/{sid}", files=files).status_code == 200
    # 분류기에 기대지 않는다 — 여기서 보려는 것은 배정이 아니라 쌓는 순서다
    for slot, photo in zip(M.cfg.ppt.slot_names, s.photos):
        M._put(s, photo, slot, at=0)

    r = client.post(f"/api/commit/{sid}?allow_missing=true")
    assert r.status_code == 200, r.text

    prs = T.load_presentation(d / r.json()["ppt"])
    fresh = [sl for sl in prs.slides
             if any(str(sh.name).startswith(W.PHOTO_NAME_PREFIX) for sh in sl.shapes)]
    assert fresh, "사진이 들어간 장이 없다"
    for sl in fresh:
        assert M._slide_slot_z_order(sl, _slide_ctr(prs)) == HAND_MADE_ORDER
