"""
파일명 생성 — Fastest Lap

저장 이름은 `{prefix}_{별칭}{구분자}{번호}.{확장자}` 하나뿐이다. 본편의 패턴
빌더(생성·역파싱 라운드트립)는 통째로 없다 — 여기서는 이름을 **읽지 않는다**.
폴더가 장부가 아니므로 파일명은 사람이 알아보기 위한 것 그 이상이 아니다.

번호 규칙(사용자 설정):
    number_mode  "multi_only"  같은 카테고리가 2장 이상일 때만 번호
                 "always"      한 장이어도 번호
    start        시작 번호 (0 또는 1)
    separator    별칭과 번호 사이 문자열 (기본 "_")

충돌(같은 이름의 파일이 이미 있음)은 여기서 판정하지 않는다 — main 의 plan 이
`exists` 를 보고, 덮어쓰지 않기로 한 이름은 `bump()` 로 다음 빈 번호를 받는다.
"""

from __future__ import annotations

import re

# 별칭 기본값은 카테고리 원문 그대로다 — 사용자가 설정에서 바꾼다.
DEFAULT_ALIASES = {
    "IO_FRONT": "IO_FRONT", "IO_RIGHT": "IO_RIGHT", "IO_LEFT": "IO_LEFT",
    "IO_UPPER": "IO_UPPER", "IO_LOWER": "IO_LOWER", "FACE": "FACE",
}

# Windows 파일명 금지 문자 + 경로 구분자. 별칭·prefix 검증에 쓴다.
_BAD_CHARS = set('\\/:*?"<>|')


def sanitize(text: str) -> str | None:
    """파일명 조각으로 쓸 수 있게 다듬는다. 못 쓰면 None."""
    t = (text or "").strip().rstrip(".")
    if not t or (_BAD_CHARS & set(t)):
        return None
    return t


def alias_of(category: str, aliases: dict[str, str] | None) -> str:
    got = (aliases or {}).get(category) or DEFAULT_ALIASES.get(category, category)
    return sanitize(got) or category


def stem(prefix: str, category: str, n: int | None, *,
         aliases: dict[str, str] | None = None, separator: str = "_") -> str:
    """확장자를 뺀 이름 한 개. n 이 None 이면 번호 없이."""
    base = f"{prefix}_{alias_of(category, aliases)}" if prefix else alias_of(category, aliases)
    return base if n is None else f"{base}{separator}{n}"


def plan_stems(prefix: str, categories: list[str], *,
               aliases: dict[str, str] | None = None,
               number_mode: str = "multi_only",
               start: int = 1,
               separator: str = "_") -> list[str]:
    """저장 순서대로 늘어선 카테고리 목록 → 같은 순서의 이름(확장자 없음) 목록.

    같은 카테고리가 여러 번 나오면 나온 순서대로 start 부터 번호가 붙는다.
    """
    total: dict[str, int] = {}
    for c in categories:
        total[c] = total.get(c, 0) + 1
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in categories:
        k = seen.get(c, 0)
        seen[c] = k + 1
        need_n = number_mode == "always" or total[c] > 1
        out.append(stem(prefix, c, (start + k) if need_n else None,
                        aliases=aliases, separator=separator))
    return out


def bump(name_stem: str, taken: set[str], separator: str = "_") -> str:
    """이미 쓰인 이름과 부딪히면 뒤에 `_2`, `_3`… 을 붙여 첫 빈 이름을 준다.

    의료 기록을 조용히 덮지 않기 위한 기본 동작이다 — 덮어쓰기는 저장 검토
    화면에서 파일별로 명시해야만 일어난다.
    """
    if name_stem not in taken:
        return name_stem
    n = 2
    while f"{name_stem}{separator}{n}" in taken:
        n += 1
    return f"{name_stem}{separator}{n}"


_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def raw_name(final_stem: str, orig_name: str) -> str:
    """원본 사본 이름 — 완성본과 같은 어간 + `_raw` + **원본 확장자**."""
    m = _EXT_RE.search(orig_name or "")
    ext = (m.group(0) if m else ".jpg").lower()
    return f"{final_stem}_raw{ext}"
