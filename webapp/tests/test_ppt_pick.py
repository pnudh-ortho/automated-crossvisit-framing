"""여러 PPT 중 어느 것에 이어붙일까.

한 환자 폴더에 덱이 둘 이상 있는 일은 흔하다 — 백업본, 이전 버전, 보관 폴더로
치워 둔 것. 말없이 아무거나 고르면 한 환자의 기록이 두 덱으로 갈린다.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402
import naming as N                                                # noqa: E402

IDS = N.Identifiers("홍길동", "123456789", "12345")


@pytest.fixture
def patient(_isolate_paths):
    d = _isolate_paths / "홍길동_123456789_12345"
    (d / "보관").mkdir(parents=True)
    return d


def _touch(d, rel):
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def _pick(d):
    got = M._find_ppt(M._patient_files(d), d, IDS)
    return got.relative_to(d).as_posix() if got else None


def test_환자_폴더_바로_아래가_먼저다(patient):
    """하위 폴더로 치운 것은 대개 보관본이다."""
    _touch(patient, "보관/홍길동_123456789_12345.pptx")
    _touch(patient, "홍길동_123456789_12345.pptx")
    assert _pick(patient) == "홍길동_123456789_12345.pptx"


def test_같은_깊이면_생성_형식_이름이_먼저다(patient):
    _touch(patient, "홍길동_123456789_12345.pptx")     # 옛 기본형
    _touch(patient, M._gen_ppt_name(IDS))              # 지금 생성 형식
    assert _pick(patient) == M._gen_ppt_name(IDS)


def test_마지막에_이어붙인_덱을_기억한다(patient):
    """기억이 깊이·이름 규칙을 이긴다 — 갈라진 기록을 만들지 않기 위해서다."""
    _touch(patient, "홍길동_123456789_12345.pptx")
    keep = _touch(patient, "보관/홍길동_123456789_12345.pptx")
    M._remember_ppt(patient.name, keep.relative_to(patient).as_posix())
    assert _pick(patient) == "보관/홍길동_123456789_12345.pptx"

    # 기억한 파일이 사라지면 조용히 종전 규칙으로 돌아간다
    keep.unlink()
    assert _pick(patient) == "홍길동_123456789_12345.pptx"


def test_다른_환자_파일은_후보가_아니다(patient):
    _touch(patient, "김철수_111111111_99999.pptx")
    assert _pick(patient) is None


def test_기억은_설정_파일에_남는다(patient):
    M._remember_ppt(patient.name, "보관/x.pptx")
    d = json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert d["ppt_choice"][patient.name] == "보관/x.pptx"


def test_사람이_고르면_그_덱으로_바뀐다(_isolate_paths):
    """규칙이 어긋났을 때 되돌릴 길 — 폴더 내용에서 눌러 고른다."""
    from starlette.testclient import TestClient

    d = _isolate_paths / "홍길동_123456789_12345"
    (d / "보관").mkdir(parents=True)
    _touch(d, "홍길동_123456789_12345.pptx")
    _touch(d, "보관/홍길동_123456789_12345.pptx")
    assert _pick(d) == "홍길동_123456789_12345.pptx"      # 바로 아래가 기본

    with TestClient(M.app) as c:
        r = c.post("/api/folder/ppt", json={"folder": d.name,
                                            "ppt": "보관/홍길동_123456789_12345.pptx"})
        assert r.status_code == 200, r.text
        assert _pick(d) == "보관/홍길동_123456789_12345.pptx"
        # 폴더 밖·그 폴더의 .pptx 가 아닌 것은 거절한다
        assert c.post("/api/folder/ppt",
                      json={"folder": d.name, "ppt": "../다른.pptx"}).status_code == 400
        assert c.post("/api/folder/ppt",
                      json={"folder": d.name, "ppt": "없는파일.pptx"}).status_code == 400
