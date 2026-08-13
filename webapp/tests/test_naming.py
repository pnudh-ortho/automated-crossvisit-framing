"""명명 규칙 테스트 (Stage 2). 표준 라이브러리만 사용."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import naming as N  # noqa: E402

PPT_PAT = "{name}_{hospital_id}_{ortho_id}.pptx"
PHOTO_PAT = "{ortho_id}_{visit} ({index}).jpg"
VISIT_RX = "{ortho_id}_([A-Z]+)"


def expect_error(fn):
    try:
        fn()
    except N.NamingError:
        return True
    return False


# ── 식별자 검증 ───────────────────────────────────────────────────────────────
def test_valid_identifiers():
    ids = N.validate_identifiers("홍길동", "123456789", "12345")
    assert ids.name == "홍길동"


def test_bad_hospital_digits():
    assert expect_error(lambda: N.validate_identifiers("홍길동", "12345", "12345"))


def test_bad_ortho_digits():
    assert expect_error(lambda: N.validate_identifiers("홍길동", "123456789", "123"))


def test_bad_name():
    # 숫자·밑줄·앞뒤 공백/마침표는 폴더명 파싱을 흔들거나 Windows가 거부한다
    for bad in ("", "홍길동2", "홍_길동", "홍길동.", "김/철수", "홍길동 이름이 아주 긴 경우" + "가" * 40):
        assert expect_error(lambda b=bad: N.validate_identifiers(b, "123456789", "12345")), bad


def test_name_is_trimmed():
    """앞뒤 공백은 오류가 아니라 다듬는다 — 붙여넣기 실수는 막을 필요가 없다."""
    assert N.validate_identifiers("  홍길동  ", "123456789", "12345").name == "홍길동"


def test_english_and_mixed_names():
    for ok in ("Hong", "John Smith", "Kim Ha-neul", "J. Smith", "O'Brien", "김Kim"):
        ids = N.validate_identifiers(ok, "123456789", "12345")
        assert ids.name == ok, ok


def test_parse_roundtrip_english_name():
    """폴더명 생성 → 파싱이 영문 이름에서도 되돌아와야 한다."""
    FOLDER_PAT = "{name}_{hospital_id}_{ortho_id}"
    for nm in ("홍길동", "John Smith", "Kim Ha-neul", "J. Smith"):
        ids = N.validate_identifiers(nm, "123456789", "12345")
        folder = N.folder_name(ids, FOLDER_PAT)
        back = N.parse_pattern(folder, FOLDER_PAT, label="폴더명")
        assert back == ids, (folder, back)


# ── PPT 파일명 파싱 ───────────────────────────────────────────────────────────
def test_parse_ppt_filename():
    ids = N.parse_ppt_filename("홍길동_123456789_12345.pptx", PPT_PAT)
    assert ids.name == "홍길동"
    assert ids.hospital_id == "123456789"
    assert ids.ortho_id == "12345"


def test_parse_ppt_filename_wrong_digits_fails():
    # ortho_id 4자리 → 불일치
    assert expect_error(lambda: N.parse_ppt_filename("홍길동_123456789_1234.pptx", PPT_PAT))


# ── 차수 알파벳 ───────────────────────────────────────────────────────────────
def test_letter_num_roundtrip():
    for n in [1, 26, 27, 28, 52, 53, 702, 703]:
        assert N.letter_to_num(N.num_to_letter(n)) == n


def test_known_letters():
    assert N.num_to_letter(1) == "A"
    assert N.num_to_letter(26) == "Z"
    assert N.num_to_letter(27) == "AA"
    assert N.num_to_letter(28) == "AB"


def test_next_visit_empty():
    assert N.next_visit_letter([]) == "A"
    assert N.next_visit_letter(None) == "A"


def test_next_visit_increment():
    assert N.next_visit_letter(["A", "B", "C"]) == "D"
    assert N.next_visit_letter(["Z"]) == "AA"
    assert N.next_visit_letter(["A", "Z", "C"]) == "AA"  # 최대값 기준


def test_scan_visit_letters():
    files = [
        "12345_A (1).jpg", "12345_A (2).jpg",
        "12345_B (1).jpg", "12345_C (3).jpg",
        "홍길동_123456789_12345.pptx",   # 매칭 안 됨
        "99999_D (1).jpg",              # 다른 환자
    ]
    letters = N.scan_visit_letters(files, "12345", VISIT_RX)
    assert letters == ["A", "B", "C"]
    assert N.next_visit_letter(letters) == "D"


# ── 파일명 생성 ───────────────────────────────────────────────────────────────
def test_photo_filename():
    assert N.photo_filename("12345", "C", 3, PHOTO_PAT) == "12345_C (3).jpg"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} naming tests passed.")
