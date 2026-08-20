"""fl_names — 파일명 생성 규칙 검증. 실행: python -m pytest tests/test_fl_names.py -q"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import fl_names as FN  # noqa: E402


def test_multi_only_numbers_only_duplicates():
    cats = ["IO_FRONT", "FACE", "FACE", "FACE", "IO_UPPER"]
    got = FN.plan_stems("홍길동", cats)
    assert got == ["홍길동_IO_FRONT", "홍길동_FACE_1", "홍길동_FACE_2",
                   "홍길동_FACE_3", "홍길동_IO_UPPER"]


def test_always_numbers_everything_from_start():
    got = FN.plan_stems("p", ["IO_FRONT", "FACE"], number_mode="always", start=0)
    assert got == ["p_IO_FRONT_0", "p_FACE_0"]


def test_aliases_and_separator():
    got = FN.plan_stems("김", ["IO_FRONT", "FACE", "FACE"],
                        aliases={"IO_FRONT": "정면", "FACE": "안모"},
                        separator="-")
    assert got == ["김_정면", "김_안모-1", "김_안모-2"]


def test_bump_finds_first_free_number():
    taken = {"a_IO_FRONT", "a_IO_FRONT_2", "a_IO_FRONT_3"}
    assert FN.bump("a_IO_FRONT", taken) == "a_IO_FRONT_4"
    assert FN.bump("a_IO_LEFT", taken) == "a_IO_LEFT"


def test_sanitize_rejects_path_hostile_names():
    assert FN.sanitize("정상 이름") == "정상 이름"
    assert FN.sanitize("  둘레공백  ") == "둘레공백"
    for bad in ("", "  ", "a/b", "a\\b", "a:b", "a*b", 'a"b', "a?b", "끝점."):
        got = FN.sanitize(bad)
        assert got in (None, "끝점"), (bad, got)


def test_raw_name_keeps_original_extension():
    assert FN.raw_name("홍_IO_FRONT", "IMG_0001.CR2") == "홍_IO_FRONT_raw.cr2"
    assert FN.raw_name("홍_FACE_1", "no_ext") == "홍_FACE_1_raw.jpg"
