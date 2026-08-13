"""직전 차수 슬라이드 복사 — 선만 / 전부 / 복사 안 함.

정중선 같은 기준선은 매 차수 같은 자리를 가리키므로 따라오는 편이 맞다(기본값).
"전부"는 글상자와 그 안의 글까지 가져온다 — 지난 차수 내용을 이어 고쳐 쓰는
방식이다. "복사 안 함"은 상속으로 딸려 온 자유 기입 글도 지운다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import case_deck as CD                                            # noqa: E402
import main as M                                                  # noqa: E402
import template as T                                              # noqa: E402


def _slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _textbox(slide, name, text=""):
    from pptx.util import Emu
    tb = slide.shapes.add_textbox(Emu(500000), Emu(500000), Emu(2000000), Emu(800000))
    tb.name = name
    tb.text_frame.text = text
    return tb


def _line(slide, name="정중선"):
    from pptx.util import Emu
    from pptx.enum.shapes import MSO_CONNECTOR
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                    Emu(1000000), Emu(0), Emu(1000000), Emu(4000000))
    ln.name = name
    return ln


@pytest.fixture
def deck():
    """직전 장(선 + 자유 메모 + 규약 상자) / 새 장(빈 규약 상자)."""
    from pptx import Presentation
    prs = Presentation()
    src, dst = _slide(prs), _slide(prs)
    _line(src)
    _textbox(src, "메모", "발치 부위 확인")
    _textbox(src, CD.NOTE_LL, "지난 차수 좌하단")
    _textbox(src, CD.NOTE_NEXT, "지난 차수 우하단")
    _textbox(src, M.cfg.ppt.info_box_name, "24.08.12 (재진 C)")
    for nm in (CD.NOTE_LL, CD.NOTE_NEXT):
        _textbox(dst, nm, "")
    return src, dst


def _names(slide):
    return [str(sh.name) for sh in slide.shapes]


def test_선만_복사(deck):
    src, dst = deck
    M._inherit_shapes(src, dst, "lines")
    assert "정중선" in _names(dst)
    assert "메모" not in _names(dst)                  # 글상자는 안 온다
    assert T.find_shape(dst, CD.NOTE_LL).text_frame.text.strip() == ""


def test_전부_복사는_글까지(deck):
    src, dst = deck
    M._inherit_shapes(src, dst, "all")
    assert "정중선" in _names(dst) and "메모" in _names(dst)
    # 자유 기입 상자는 **글 내용**이 물려 내려온다
    assert T.find_shape(dst, CD.NOTE_LL).text_frame.text == "지난 차수 좌하단"
    assert T.find_shape(dst, CD.NOTE_NEXT).text_frame.text == "지난 차수 우하단"
    # 날짜/차수 상자는 이 경로로 오지 않는다 — 매 차수 새로 쓴다
    assert _names(dst).count(M.cfg.ppt.info_box_name) == 0


def test_복사_안_함(deck):
    src, dst = deck
    _textbox(dst, CD.NOTE_SOAP, "상속으로 딸려 온 지난 글")
    M._inherit_shapes(src, dst, "none")
    assert "정중선" not in _names(dst) and "메모" not in _names(dst)
    # 상속으로 딸려 온 글도 지운다 — 안 지우면 설정이 거짓말이 된다
    assert T.find_shape(dst, CD.NOTE_SOAP).text_frame.text.strip() == ""


def test_기본값은_선만_복사(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "SETTINGS_FILE", tmp_path / "settings.json", raising=False)
    assert M._copy_shapes() == "lines"
