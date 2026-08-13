"""
명명 규칙 (Stage 2)

- 환자 폴더명 / PPT 파일명 / 사진 파일명을 config 템플릿으로 생성.
- 재진 차수 알파벳 자동 증가 (A..Z, 이후 AA, AB, ... 확장).
- 기존 PPT 파일명을 config 패턴으로 파싱해 식별자 획득 + 자릿수 검증.

패턴/자릿수는 하드코딩하지 않고 config에서 주입한다. 순수 표준 라이브러리.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class NamingError(ValueError):
    """식별자 검증/파싱 실패."""


# 이름 허용 문자. 한글·영문 혼용을 허용하되 세 가지를 의도적으로 막는다.
#   '_'  폴더명이 '{name}_{hospital_id}_{ortho_id}'라서 이름에 들어가면 파싱이 흔들린다
#   숫자  뒤따르는 번호와 헷갈린다
#   맨 앞/뒤의 공백·마침표  Windows가 폴더명으로 허용하지 않는다
# 결과적으로 \ / : * ? " < > | 같은 금지문자도 화이트리스트에서 자동으로 배제된다.
NAME_DEFAULT = r"[가-힣A-Za-z](?:[가-힣A-Za-z .'\-]{0,38}[가-힣A-Za-z])?"


def _bare(rx: str) -> str:
    """config의 ^...$ 앵커를 벗겨 캡처그룹 안에 넣을 수 있게 한다."""
    return rx.removeprefix("^").removesuffix("$")


@dataclass(frozen=True)
class Identifiers:
    name: str
    hospital_id: str
    ortho_id: str


# ── 식별자 검증 ───────────────────────────────────────────────────────────────
def validate_identifiers(
    name: str,
    hospital_id: str,
    ortho_id: str,
    *,
    hospital_digits: int = 9,
    ortho_digits: int = 5,
    name_regex: str | None = None,
) -> Identifiers:
    name = (name or "").strip()
    hospital_id = (hospital_id or "").strip()
    ortho_id = (ortho_id or "").strip()
    if not re.fullmatch(_bare(name_regex or NAME_DEFAULT), name):
        raise NamingError(
            f"이름 형식 오류: '{name}' — 한글/영문 1~40자, "
            "공백·마침표·하이픈만 함께 쓸 수 있습니다 (숫자·밑줄 불가)")
    if not re.fullmatch(rf"\d{{{hospital_digits}}}", hospital_id):
        raise NamingError(f"병원 환자번호는 {hospital_digits}자리 숫자여야 합니다: '{hospital_id}'")
    if not re.fullmatch(rf"\d{{{ortho_digits}}}", ortho_id):
        raise NamingError(f"교정과 환자번호는 {ortho_digits}자리 숫자여야 합니다: '{ortho_id}'")
    return Identifiers(name, hospital_id, ortho_id)


# ── 템플릿 포맷/파싱 ──────────────────────────────────────────────────────────
RECOG_ONLY = re.compile(r"\{(any|[dc]\d+-\d+)\}")


def is_recognition_only(pattern: str) -> bool:
    """인식 전용 토큰이 든 형식인가 — 이걸로는 폴더 이름을 만들 수 없다."""
    return bool(RECOG_ONLY.search(pattern))


def format_pattern(pattern: str, **fields) -> str:
    """'{a}_{b}' → 값 대입. 알 수 없는 필드가 있으면 그대로 KeyError로 노출."""
    return pattern.format(**fields)


# 필드별 정규식 (파싱 시 사용). config 자릿수에 맞춰 build.
def default_field_regex(hospital_digits: int = 9, ortho_digits: int = 5,
                        name_regex: str | None = None) -> dict[str, str]:
    return {
        "name": _bare(name_regex) if name_regex else NAME_DEFAULT,
        "hospital_id": rf"\d{{{hospital_digits}}}",
        "ortho_id": rf"\d{{{ortho_digits}}}",
        "visit": r"[A-Z]+",
        "index": r"\d+",
        "n": r"\d+",
    }


def compile_pattern(pattern: str, field_regex: dict[str, str]) -> re.Pattern:
    """
    '{name}_{hospital_id}_{ortho_id}.pptx' → 명명 캡처그룹 정규식.
    리터럴 부분은 이스케이프, {field}는 (?P<field>...)로 치환.
    """
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "{":
            j = pattern.index("}", i)
            field = pattern[i + 1 : j]
            if field not in field_regex:
                # 인식 전용 토큰 — 폴더를 '읽을' 때만 쓴다 (이름 생성엔 못 쓴다).
                #   {any}    이후 아무거나        {d1-3}  숫자 1~3자리
                #   {c2-5}   아무 글자 2~5자리
                mr = re.fullmatch(r"any|([dc])(\d+)-(\d+)", field)
                if mr:
                    if field == "any":
                        # 중간의 *는 구분자(- _ . 공백 괄호)를 먹지 않는다 — 먹으면
                        # 구분이 무의미해진다. 맨 끝의 *만 "이후 전부 무시"다.
                        out.append(r".*" if j == len(pattern) - 1
                                   else r"[^-_.() ]*")
                    else:
                        base = r"\d" if mr.group(1) == "d" else r"."
                        out.append(f"{base}{{{mr.group(2)},{mr.group(3)}}}")
                    i = j + 1
                    continue
                raise NamingError(f"패턴에 알 수 없는 필드: {{{field}}}")
            out.append(f"(?P<{field}>{field_regex[field]})")
            i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_pattern(
    text: str,
    pattern: str,
    *,
    hospital_digits: int = 9,
    ortho_digits: int = 5,
    name_regex: str | None = None,
    label: str = "이름",
) -> Identifiers:
    """폴더명/PPT 파일명 → 식별자. 자릿수 불일치 시 즉시 오류."""
    field_regex = default_field_regex(hospital_digits, ortho_digits, name_regex)
    rx = compile_pattern(pattern, field_regex)
    m = rx.match(text)
    if not m:
        raise NamingError(
            f"{label}이 패턴과 불일치: '{text}'  (기대 형식: {pattern})"
        )
    g = m.groupdict()
    return validate_identifiers(
        g["name"], g["hospital_id"], g["ortho_id"],
        hospital_digits=hospital_digits, ortho_digits=ortho_digits,
        name_regex=name_regex,
    )


def parse_ppt_filename(
    filename: str,
    pattern: str,
    *,
    hospital_digits: int = 9,
    ortho_digits: int = 5,
    name_regex: str | None = None,
) -> Identifiers:
    """재진: PPT 파일명 → 식별자."""
    return parse_pattern(filename, pattern, hospital_digits=hospital_digits,
                         ortho_digits=ortho_digits, name_regex=name_regex,
                         label="PPT 파일명")


# ── 차수 알파벳 (bijective base-26) ──────────────────────────────────────────
def letter_to_num(s: str) -> int:
    """A→1, Z→26, AA→27, AB→28 ..."""
    n = 0
    for ch in s:
        if not ("A" <= ch <= "Z"):
            raise NamingError(f"차수 알파벳이 아님: '{s}'")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def num_to_letter(n: int) -> str:
    """1→A, 26→Z, 27→AA ..."""
    if n < 1:
        raise NamingError("차수 번호는 1 이상")
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("A") + r) + out
    return out


def next_visit_letter(existing: list[str] | None) -> str:
    """기존 차수 목록에서 최대값+1. 없으면 'A'."""
    if not existing:
        return "A"
    mx = max(letter_to_num(x) for x in existing)
    return num_to_letter(mx + 1)


def scan_visit_letters(filenames: list[str], ortho_id: str, visit_regex_tpl: str) -> list[str]:
    """
    폴더 파일명 목록에서 이 환자의 차수 알파벳들을 추출.
    visit_regex_tpl 예: '{ortho_id}_([A-Z]+)'  → ortho_id 대입 후 매칭.
    """
    rx = re.compile(visit_regex_tpl.format(ortho_id=re.escape(ortho_id)))
    found = set()
    for fn in filenames:
        m = rx.search(fn)
        if m:
            found.add(m.group(1))
    return sorted(found, key=letter_to_num)


# ── 최종 이름 생성 ────────────────────────────────────────────────────────────
def folder_name(ids: Identifiers, pattern: str) -> str:
    return format_pattern(pattern, name=ids.name, hospital_id=ids.hospital_id, ortho_id=ids.ortho_id)


def ppt_filename(ids: Identifiers, pattern: str) -> str:
    return format_pattern(pattern, name=ids.name, hospital_id=ids.hospital_id, ortho_id=ids.ortho_id)


def photo_filename(ortho_id: str, visit: str, index: int, pattern: str) -> str:
    return format_pattern(pattern, ortho_id=ortho_id, visit=visit, index=index)


def photo_extra_filename(ortho_id: str, visit: str, index: int, n: int, pattern: str) -> str:
    """같은 자리의 추가 촬영본. n은 2부터 (대표가 1번 자리)."""
    return format_pattern(pattern, ortho_id=ortho_id, visit=visit, index=index, n=n)


# ── 원본 사본 (_raw) ──────────────────────────────────────────────────────────
# 환자 폴더에는 **슬라이드에 실린 그대로**(잘린 사진)가 간다. 폴더와 PPT 가 다른
# 그림이면 나중에 어느 쪽이 진짜인지 다투게 된다.
#
# 원본을 함께 남기고 싶으면 설정에서 켠다. 그때 원본은 같은 이름에 `_raw` 를 붙여
# **원본 확장자 그대로** 저장한다 — 잘린 쪽은 항상 .jpg 지만 원본은 .png·.heic 일
# 수 있고, 확장자가 내용과 다르면 나중에 못 여는 파일이 된다.
RAW_SUFFIX = "_raw"
RAW_DIR = "raw"     # 원본 사본이 모이는 하위 폴더 — 환자 폴더가 두 배로 안 붐빈다


def raw_filename(final_name: str, src_name: str) -> str:
    """잘린 사진 이름 → 짝이 되는 원본의 상대 경로 (`raw/` 하위).

        raw_filename("12345_A (1).jpg", "IMG_0042.PNG")  →  "raw/12345_A (1)_raw.png"

    `_raw` 접미는 하위 폴더가 생긴 뒤에도 남긴다 — 폴더 밖으로 복사돼 나가도
    완성본과 안 헷갈리고, 옛 버전이 루트에 저장한 `_raw` 파일과 규칙이 같다.
    """
    stem = final_name.rsplit(".", 1)[0]
    ext = ("." + src_name.rsplit(".", 1)[1].lower()) if "." in src_name else ".jpg"
    return f"{RAW_DIR}/{stem}{RAW_SUFFIX}{ext}"


def is_raw(name: str) -> bool:
    """원본 사본인가. 사진 개수를 셀 때 빼야 한 차수가 두 배로 보이지 않는다."""
    return name.rsplit(".", 1)[0].endswith(RAW_SUFFIX)
