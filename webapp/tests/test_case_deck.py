"""
케이스 덱 조립 검증 (초진).

template.pptx(30장 케이스 양식)에서 앞 16장을 남기고, 빈 노트 슬라이드를
십자뷰로 만들어 17장짜리 초진 덱을 조립하는 과정을 검증한다.

핵심 확인 두 가지:
  - 앵커를 좌표 하드코딩 없이 템플릿 도형에서 읽는가 (자리 종류가 맞는가)
  - 버려지는 슬라이드(타 환자 진료기록)가 결과물에 남지 않는가

실행: cd webapp && python -m pytest tests/test_case_deck.py -q
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import case_deck as CD  # noqa: E402
from template import load_presentation  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# 양식은 templates/ 로 옮겨졌다. 이 경로가 옛 자리를 가리키는 동안 아래 다섯 개가
# 조용히 건너뛰어졌다 — 건너뛴 테스트는 없는 테스트다.
CASE_TEMPLATE = ROOT / "templates" / "case_template.pptx"
CROSS_TEMPLATE = ROOT / "templates" / "intraoral_template.pptx"

KEEP_SLIDES = 16
NOTE_SLIDE_NO = 30
FACE_SLIDES = [4, 5, 6, 7, 8, 9]
BIG_SLIDES = [10, 11]
INTRAORAL_SLIDES = [12, 13, 14, 15, 16]

pytestmark = pytest.mark.skipif(
    not (CASE_TEMPLATE.exists() and CROSS_TEMPLATE.exists()),
    reason="템플릿 pptx가 없습니다",
)


@pytest.fixture(scope="module")
def deck(tmp_path_factory):
    out = tmp_path_factory.mktemp("deck") / "case.pptx"
    prs = CD.build_first_visit_deck(
        CASE_TEMPLATE, CROSS_TEMPLATE, out,
        keep_slides=KEEP_SLIDES, note_slide_no=NOTE_SLIDE_NO,
        cross_drop_shapes={"INFO_BOX"},
    )
    prs.save(str(out))
    return out


def test_anchors_are_read_from_template():
    """자리 종류가 템플릿 기하에서 바르게 유도되는가."""
    prs = load_presentation(CASE_TEMPLATE)
    anchors = CD.read_deck_anchors(prs, FACE_SLIDES, BIG_SLIDES, INTRAORAL_SLIDES)

    # 4~6은 좌우로 갈라진 두 자리
    for n in (4, 5, 6):
        assert (n, "L") in anchors and (n, "R") in anchors
        left, right = anchors[(n, "L")], anchors[(n, "R")]
        assert left.window.x < right.window.x
        assert left.window.x + left.window.w <= right.window.x + 1e-6

    # 7~9는 중앙 한 자리
    for n in (7, 8, 9):
        assert (n, "C") in anchors, f"slide {n}"
        assert (n, "L") not in anchors

    # 10·11은 겹친 사진을 한 자리로 접어 '위아래로 큰' 자리가 된다
    for n in (10, 11):
        assert (n, "BIG") in anchors, f"slide {n}"
        assert (n, "L") not in anchors and (n, "C") not in anchors

    # 구내 5장은 전면 한 자리씩 — 도형이 없는 슬라이드는 앞 슬라이드에서 물려받는다
    for n in INTRAORAL_SLIDES:
        assert (n, "FULL") in anchors, f"slide {n}"


def test_deck_shape_and_cross_view(deck):
    prs = load_presentation(deck)
    assert len(prs.slides) == KEEP_SLIDES + 1

    cross = prs.slides[CD.cross_slide_index(prs)]
    names = {sh.name for sh in cross.shapes}

    # 십자뷰 5슬롯이 얹혔는가
    for slot in ("SLOT_UPPER", "SLOT_LEFT", "SLOT_FRONT", "SLOT_RIGHT", "SLOT_LOWER"):
        assert slot in names, slot
    # 십자 템플릿의 마스크가 함께 왔는가 (MASK_S1이 노트 박스로 오인되지 않았는가)
    assert "MASK_S1" in names
    # 겹치는 INFO_BOX는 걸러졌는가 — 날짜는 NOTE_DATE가 맡는다
    assert "INFO_BOX" not in names


def test_all_four_note_boxes_exist_and_round_trip(deck, tmp_path):
    prs = load_presentation(deck)
    cross = prs.slides[CD.cross_slide_index(prs)]

    vals = {
        CD.NOTE_DATE: "26.07.31 (초진 A)",
        CD.NOTE_STATUS: "U: 018 NT\nL: 016 NT\n\nTx. Period: 0 month",
        CD.NOTE_SOAP: "s) n/s\np) bonding",
        CD.NOTE_NEXT: "n) AWC",
    }
    for name, text in vals.items():
        assert CD.set_note_text(cross, name, text), f"{name} 박스가 없습니다"

    out = tmp_path / "edited.pptx"
    prs.save(str(out))

    reopened = load_presentation(out)
    cross2 = reopened.slides[CD.cross_slide_index(reopened)]
    for name, text in vals.items():
        assert CD.get_note_text(cross2, name) == text, name


def test_other_patient_records_are_dropped(deck):
    """버려지는 슬라이드에 있던 남의 진료기록이 결과물에 남으면 안 된다."""
    prs = load_presentation(deck)
    haystack = "\n".join(
        sh.text_frame.text
        for slide in prs.slides
        for sh in slide.shapes
        if sh.has_text_frame
    )
    for leaked in ("재진 B", "MARPE", "Surgical exposure", "실밥", "24.09.12"):
        assert leaked not in haystack, f"{leaked!r} 가 남아 있습니다"


def test_source_template_is_untouched(deck):
    """덱을 만들어도 원본 양식은 그대로여야 한다 (사본에 대고 작업한다).

    양식은 이제 16장이다. 예전 30장에는 17~29장에 실제 환자 진료기록이 들어
    있었고, 그게 배포 파일에 실려 나갔다.
    """
    prs = load_presentation(CASE_TEMPLATE)
    assert len(prs.slides) == KEEP_SLIDES
