"""macOS 의 자모 분해(NFD) 이름.

맥은 파일·폴더 이름의 한글을 자모로 분해해 저장한다. 눈에는 같은 "홍길동" 이지만
문자열로는 다르고(19자 vs 25자), 이름 규칙의 [가-힣] 은 조합된 글자만 받는다.
그대로 두면 맥에서는 한글 환자 폴더가 통째로 목록에서 빠진다.
"""
import json
import os
import sys
import unicodedata as ud

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402
import naming as N                                                # noqa: E402

FOLDER = "홍길동_123456789_12345"
IDS = N.Identifiers("홍길동", "123456789", "12345")
DIG = dict(hospital_digits=9, ortho_digits=5)


def test_분해형_이름도_같은_글자로_읽는다():
    assert ud.normalize("NFD", FOLDER) != FOLDER          # 문자열로는 다르다
    assert N.nfc(ud.normalize("NFD", FOLDER)) == FOLDER   # 조합형으로 맞춘다


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_폴더명_파싱(form):
    got = N.parse_pattern(ud.normalize(form, FOLDER),
                          "{name}_{hospital_id}_{ortho_id}", label="폴더명", **DIG)
    assert (got.name, got.ortho_id) == ("홍길동", "12345")


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_PPT_고르기(form, _isolate_paths):
    d = _isolate_paths / ud.normalize(form, FOLDER)
    d.mkdir()
    (d / ud.normalize(form, "홍길동(12345).pptx")).write_bytes(b"")
    got = M._find_ppt(M._patient_files(d), d, IDS)
    assert got is not None, "맥 이름의 PPT 를 못 찾았다"


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_환자별_PPT_기억(form, _isolate_paths):
    """넣을 때와 찾을 때의 형태가 달라도 같은 환자로 본다."""
    M._remember_ppt(ud.normalize(form, FOLDER), "보관/a.pptx")
    other = "NFD" if form == "NFC" else "NFC"
    assert M._remembered_ppt(ud.normalize(other, FOLDER)) == "보관/a.pptx"
    saved = json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))["ppt_choice"]
    assert FOLDER in saved                                 # 열쇠는 조합형으로 남는다
