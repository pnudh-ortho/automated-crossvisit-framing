"""
촬영 순서 정렬 검증.

EXIF 의 촬영시각은 초까지만이라, 한 자리를 연달아 찍는 촬영에서는 같은 초에
여러 장이 몰려 초 단위 정렬이 무의미해진다. 그래서 서브초(SubSecTime*)를 읽고,
없으면 EXIF 일련번호 → 파일명 끝 숫자 → 업로드 순서로 물러난다. 그 단계가
의도대로 도는지 여기서 고정한다.

실행: cd webapp && python tests/test_shot_order.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import main as M  # noqa: E402

OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "shot_order_test"


def _photo(pid, name, taken=None, seq=None):
    p = M.Photo(pid, OUT / f"{pid}.jpg", 4000, 3000, "cur")
    p.orig_name = name
    p.taken_at = taken
    p.exif_seq = seq
    return p


def _order(photos):
    """_shot_order_key 로 세운 결과의 id 순서."""
    return [p.id for p in sorted(photos, key=lambda p: M._shot_order_key(p, photos.index(p)))]


# ── 조각들 ───────────────────────────────────────────────────────────────────
def test_subsec_us():
    # SubSecTime 은 '소수점 이하' 문자열이다 — '83' 은 0.83초지 0.083초가 아니다.
    assert M._subsec_us("83") == 830000
    assert M._subsec_us("083") == 83000
    assert M._subsec_us("0421") == 42100
    assert M._subsec_us("") == 0 and M._subsec_us(None) == 0
    print("PASS _subsec_us: 소수점 이하 자릿수 해석")


def test_name_seq():
    assert M._name_seq("IMG_1234.JPG") == 1234
    assert M._name_seq("DSC00007.jpg") == 7
    assert M._name_seq("2026-07-31_045.jpeg") == 45, "마지막 숫자 뭉치를 쓴다"
    assert M._name_seq("face.jpg") == -1
    assert M._name_seq(None) == -1
    print("PASS _name_seq: 파일명 끝 숫자")


def test_exif_subsec_round_trip():
    """PIL 로 쓴 EXIF 를 _exif_facts 가 그대로 되읽는지."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "exif.jpg"
    im = Image.fromarray(np.uint8(np.random.rand(40, 60, 3) * 255))
    ex = Image.Exif()
    ex[M.EXIF_ORIENT] = 1
    sub = ex.get_ifd(M.EXIF_SUB_IFD)
    sub[36867] = "2026:07:31 15:04:05"
    sub[37521] = "083"
    sub[37393] = 4211
    im.save(path, exif=ex)

    with Image.open(path) as o:
        orient, taken, seq = M._exif_facts(o)
    assert orient == 1
    assert taken == datetime(2026, 7, 31, 15, 4, 5, 83000), taken
    assert seq == 4211, seq
    print(f"PASS EXIF 왕복: {taken} (서브초 {taken.microsecond}µs), 일련번호 {seq}")


def test_exif_absent_is_survivable():
    """EXIF 가 아예 없는 파일도 예외 없이 (1, None, None)."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "bare.jpg"
    Image.fromarray(np.uint8(np.random.rand(40, 60, 3) * 255)).save(path)
    with Image.open(path) as o:
        assert M._exif_facts(o) == (1, None, None)
    print("PASS EXIF 없는 파일도 안전")


# ── 정렬 ─────────────────────────────────────────────────────────────────────
def test_subsec_breaks_same_second_tie():
    """같은 초에 찍힌 세 장 — 서브초가 순서를 가른다."""
    t = datetime(2026, 7, 31, 15, 4, 5)
    photos = [
        _photo("c", "IMG_0003.JPG", t.replace(microsecond=900000)),
        _photo("a", "IMG_0001.JPG", t.replace(microsecond=100000)),
        _photo("b", "IMG_0002.JPG", t.replace(microsecond=500000)),
    ]
    assert _order(photos) == ["a", "b", "c"]
    print("PASS 같은 초 → 서브초로 정렬")


def test_falls_back_to_exif_seq_then_filename():
    t = datetime(2026, 7, 31, 15, 4, 5)          # 서브초까지 완전 동률
    photos = [_photo("b", "X.JPG", t, seq=20), _photo("a", "Y.JPG", t, seq=10)]
    assert _order(photos) == ["a", "b"], "일련번호가 동률을 가른다"

    photos = [_photo("b", "IMG_0020.JPG", t), _photo("a", "IMG_0010.JPG", t)]
    assert _order(photos) == ["a", "b"], "일련번호가 없으면 파일명 끝 숫자"
    print("PASS 동률 → 일련번호 → 파일명 순으로 물러남")


def test_timed_photos_come_first_and_order_is_deterministic():
    t = datetime(2026, 7, 31, 15, 4, 5)
    photos = [
        _photo("none2", "face.jpg"),                       # 시각도 번호도 없음
        _photo("timed", "IMG_0009.JPG", t),
        _photo("none1", "face.jpg"),                       # 위와 완전 동일한 조건
    ]
    got = _order(photos)
    assert got[0] == "timed", "촬영시각을 아는 사진이 앞"
    # 남은 둘은 구분할 근거가 전혀 없다 → 업로드 순서가 유지돼야 한다(안정 정렬)
    assert got[1:] == ["none2", "none1"], got
    print("PASS 시각 있는 사진 우선 + 근거 없으면 업로드 순서 유지")


if __name__ == "__main__":
    test_subsec_us()
    test_name_seq()
    test_exif_subsec_round_trip()
    test_exif_absent_is_survivable()
    test_subsec_breaks_same_second_tie()
    test_falls_back_to_exif_seq_then_filename()
    test_timed_photos_come_first_and_order_is_deterministic()
    print("\n✅ 촬영 순서 정렬 테스트 통과")
