"""라벨 표기 — 지문(fingerprint) 추출과 렌더링.

새 PPT 는 설정의 두 표기(tight/spaced) 중 하나로 쓰고, 기존 PPT 이어쓰기는
그 덱 마지막 십자뷰 라벨의 지문(점 뒤 공백·끝점·차수 글자 유무)을 따른다.
"""
import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import ppt_reader as Rd  # noqa: E402


def test_fingerprint_tight():
    fp = Rd.label_fingerprint("26.08.12 (재진 B)")
    assert fp == {"spaced": False, "trailing_dot": False, "paren": True,
                  "paren_space": True, "letter_space": True, "has_letter": True}


def test_띄어쓰기가_흔들려도_그대로_따라_쓴다():
    """한 덱 안에서도 손으로 적은 라벨은 띄어쓰기가 제각각이다.

    괄호 앞·차수 글자 앞 공백까지 마지막 차수 슬라이드를 따라간다 — 안 그러면
    같은 묶음 안에서 표기가 갈린다.
    """
    import main
    cases = {
        "24. 07. 18(재진 C)": "26. 08. 14(재진 G)",
        "24. 08. 18(재진D)": "26. 08. 14(재진G)",
        "24. 07. 18 (재진F)": "26. 08. 14 (재진G)",
        "24.09.04 (재진 C)": "26.08.14 (재진 G)",
    }
    for src_label, want in cases.items():
        fp = Rd.label_fingerprint(src_label)
        got = main._render_label("26.08.14", "G", fp)
        assert got == want, f"{src_label!r} → {got!r} (기대 {want!r})"


def test_fingerprint_spaced_trailing():
    fp = Rd.label_fingerprint("26. 08. 12. (초진 A)")
    assert fp["spaced"] and fp["trailing_dot"] and fp["has_letter"]


def test_fingerprint_letterless():
    """"(재진)" 처럼 글자 없는 덱 — 새 라벨도 글자 없이 써야 자동 부여와 맞는다."""
    fp = Rd.label_fingerprint("24. 09. 26 (재진)")
    assert fp["spaced"] and not fp["trailing_dot"] and not fp["has_letter"]


def test_fingerprint_not_a_label():
    assert Rd.label_fingerprint("환자정보") is None
    assert Rd.label_fingerprint("") is None


def test_render_follows_fingerprint():
    import main
    assert main._render_label(
        "26.08.12", "B",
        {"spaced": True, "trailing_dot": True, "has_letter": False},
    ) == "26. 08. 12. (재진)"
    assert main._render_label(
        "26.08.12", "A",
        {"spaced": False, "trailing_dot": False, "has_letter": True},
    ) == "26.08.12 (초진 A)"


def test_render_formats():
    """설정 2택이 만드는 모양 — 새 PPT 에만 쓰인다."""
    import main
    assert (main._render_label("26.01.05", "A", main._FMT_FP["tight"])
            == "26.01.05 (초진 A)")
    assert (main._render_label("26.01.05", "C", main._FMT_FP["spaced"])
            == "26. 01. 05. (재진 C)")


# ── 띄어쓰기 조합 전수 ────────────────────────────────────────────────────────
# 손으로 적은 라벨에서 흔들리는 자리는 셋이다: 날짜 점 사이 · 괄호 앞 · 차수 글자 앞.
# 여기에 날짜 끝 마침표까지 더하면 16가지. 읽기와 쓰기 모두 전부 통과해야 한다 —
# 한 덱 안에서도 표기가 섞이므로 "흔한 조합만" 으로는 부족하다.
_COMBOS = list(itertools.product([0, 1], repeat=4))


def _label(spaced, paren_gap, letter_gap, dot, date="24.09.04", visit="C"):
    d = "24. 09. 04" if spaced else "24.09.04"
    if date != "24.09.04":
        d = ". ".join(date.split(".")) if spaced else date
    if dot:
        d += "."
    return f"{d}{' ' if paren_gap else ''}(재진{' ' if letter_gap else ''}{visit})"


@pytest.mark.parametrize("combo", _COMBOS)
def test_모든_띄어쓰기_조합이_읽힌다(combo):
    v, date, kind = Rd.parse_info_box(_label(*combo))
    assert (v, date, kind) == ("C", "24.09.04", "revisit"), _label(*combo)


@pytest.mark.parametrize("combo", _COMBOS)
def test_모든_띄어쓰기_조합을_그대로_따라_쓴다(combo):
    import main
    src = _label(*combo)
    want = _label(*combo, date="26.08.14", visit="G")
    got = main._render_label("26.08.14", "G", Rd.label_fingerprint(src))
    assert got == want, f"{src!r} → {got!r} (기대 {want!r})"
