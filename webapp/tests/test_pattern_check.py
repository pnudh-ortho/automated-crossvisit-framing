"""형식 인식 미리보기 — 폴더와 PPT 파일 모두.

✓ 이 형식으로 인식 · ↩ 다른 등록 형식으로는 인식 · ✗ 어느 형식으로도 못 읽음.
순번 같은 인식 전용 블록이 든 형식은 **생성형 변형**(그 블록을 뺀 모습)도 함께
맞춰 본다 — 그렇게 만들어진 이름도 그 형식 소속이기 때문이다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402


@pytest.fixture
def root(_isolate_paths):
    """폴더 3개 + 그 안의 PPT 3개."""
    for folder, ppt in (("123.조현진(12312)", "조현진(12312).pptx"),
                        ("12312 조현진(123123123)", "조현진_123123123_12312.pptx"),
                        ("홍길동_123456789_12345", "홍길동_123456789_12345.pptx")):
        d = _isolate_paths / folder
        d.mkdir(parents=True)
        (d / ppt).write_bytes(b"")
    return _isolate_paths


def _marks(items):
    return {i["name"]: ("✓" if i["match"] else "↩" if i["fallback"] else "✗")
            for i in items}


def test_폴더_형식_미리보기(root):
    m = _marks(M.pattern_check(pattern="{seq}.{name}({ortho_id})")["items"])
    assert m["123.조현진(12312)"] == "✓"
    assert m["12312 조현진(123123123)"] == "✗"
    assert m["홍길동_123456789_12345"] == "↩"   # 기본 형식으로는 읽힌다


def test_PPT_형식_미리보기(root):
    """PPT 탭도 폴더와 같은 방식으로 실제 파일을 맞춰 본다."""
    m = _marks(M.pattern_check(pattern="{name}({ortho_id})", kind="ppt")["items"])
    assert m["조현진(12312).pptx"] == "✓"
    # 옛 기본형(이름_병원번호_교정번호)은 인식 폴백으로 읽힌다
    assert m["홍길동_123456789_12345.pptx"] == "↩"


def test_순번을_뺀_이름도_그_형식_소속(root):
    """{seq} 로 만든 형식은 순번 없이 만들어진 폴더도 ✓ 여야 한다."""
    m = _marks(M.pattern_check(pattern="{seq}.{name}_{hospital_id}_{ortho_id}")["items"])
    assert m["홍길동_123456789_12345"] == "✓"
