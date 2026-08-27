"""명명 규칙 테스트 (Stage 2). 표준 라이브러리만 사용."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import naming as N  # noqa: E402

PPT_PAT = "{name}_{hospital_id}_{ortho_id}.pptx"
PHOTO_PAT = "{ortho_id}_{visit} ({index}).jpg"


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


def test_next_letter_wraps_with_z_prefix():
    """Z 다음은 ZA — 교정과 폴더가 쓰는 순서."""
    assert N.next_letter("A") == "B"
    assert N.next_letter("Y") == "Z"
    assert N.next_letter("Z") == "ZA"
    assert N.next_letter("ZA") == "ZB"
    assert N.next_letter("ZY") == "ZZ"
    assert N.next_letter("ZZ") == "ZZA"
    assert N.next_letter("AA") == "AB"   # 옛 폴더 줄기는 그대로 이어 간다


def test_next_letter_sequence_is_increasing():
    """새 순서도 정렬값(letter_to_num)으로 단조증가한다."""
    cur, seq = "A", []
    for _ in range(60):
        seq.append(cur)
        cur = N.next_letter(cur)
    nums = [N.letter_to_num(x) for x in seq]
    assert nums == sorted(nums)
    assert seq[25:28] == ["Z", "ZA", "ZB"]
    assert seq[51:53] == ["ZZ", "ZZA"]


def test_next_letter_rejects_garbage():
    assert expect_error(lambda: N.next_letter("a"))
    assert expect_error(lambda: N.next_letter("A1"))


def test_next_visit_empty():
    assert N.next_visit_letter([]) == "A"
    assert N.next_visit_letter(None) == "A"


def test_next_visit_increment():
    assert N.next_visit_letter(["A", "B", "C"]) == "D"
    assert N.next_visit_letter(["Z"]) == "ZA"
    assert N.next_visit_letter(["A", "Z", "C"]) == "ZA"   # 최대값 기준
    assert N.next_visit_letter(["Z", "ZA", "ZB"]) == "ZC"


def test_seq_token_recognition():
    """{seq} = 순번(숫자 1~3자리) — 인식 전용 블록."""
    rx = N.compile_pattern("{seq}_{name}_{ortho_id}", N.default_field_regex())
    assert rx.match("12_홍길동_20001")
    assert not rx.match("1234_홍길동_20001")
    assert N.is_recognition_only("{seq}_{name}_{ortho_id}")


def test_strip_recognition():
    """생성 때는 인식 전용 블록을 없는 셈 친다 — 겹친 구분자도 함께 정리."""
    assert N.strip_recognition("{seq}_{name}_{ortho_id}") == "{name}_{ortho_id}"
    assert N.strip_recognition("{name}_{seq}_{ortho_id}") == "{name}_{ortho_id}"
    assert N.strip_recognition("{name}_{ortho_id}{any}") == "{name}_{ortho_id}"
    assert N.strip_recognition("{name}_{ortho_id}_{d1-3}") == "{name}_{ortho_id}"
    # extra: 병원번호가 비었을 때 그 블록도 함께 뺀다
    assert N.strip_recognition("{name}_{hospital_id}_{ortho_id}",
                               {"hospital_id"}) == "{name}_{ortho_id}"


def test_strip_roundtrip():
    """생성형으로 만든 폴더명은 같은 생성형으로 되읽힌다 (병원번호 없이)."""
    gen = N.strip_recognition("{seq}_{name}_{hospital_id}_{ortho_id}",
                              {"hospital_id"})
    ids = N.validate_identifiers("홍길동", "", "12345", require_hospital=False)
    folder = N.folder_name(ids, gen)
    assert folder == "홍길동_12345"
    back = N.parse_pattern(folder, gen, label="폴더명")
    assert back.ortho_id == "12345" and back.hospital_id == ""


# ── 파일명 생성 ───────────────────────────────────────────────────────────────
def test_photo_filename():
    assert N.photo_filename("12345", "C", 3, PHOTO_PAT) == "12345_C (3).jpg"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} naming tests passed.")
