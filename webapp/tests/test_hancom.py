"""한컴오피스 한쇼(.show) 파일 — 열 수는 없어도, 왜 안 되는지는 말해 준다.

한쇼는 저장 형식을 바꾸면 그대로 쓸 수 있다. 문제는 아무 말 없이 지나칠 때다 —
폴더에 덱이 빤히 있는데 화면에는 "PPT 없음" 만 뜨면, 사람은 프로그램이 고장 난
줄 알거나 새 덱을 만들어 한 환자의 기록을 둘로 가른다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402

FOLDER = "홍길동_123456789_54321"


def _setup(root):
    (root / FOLDER).mkdir()
    M.SETTINGS_FILE.write_text(json.dumps({
        "folder_patterns": ["{name}_{hospital_id}_{ortho_id}"],
        "ppt_patterns": ["{name}({ortho_id}).pptx"]}, ensure_ascii=False),
        encoding="utf-8")
    return root / FOLDER


def test_한쇼_파일은_고르지_않는다(_isolate_paths):
    """파이썬으로 열 수 없는 형식이다. 골라 놓고 열다 실패하면 더 나쁘다."""
    d = _setup(_isolate_paths)
    (d / "홍길동(54321).show").write_bytes(b"hancom")
    assert M._scan_patient(d, deep=True)["ppt"] is None


def test_한쇼_파일이_있으면_할_일을_알려준다(_isolate_paths):
    d = _setup(_isolate_paths)
    (d / "홍길동(54321).show").write_bytes(b"hancom")
    diag = M._scan_patient(d, deep=True)["ppt_diag"]
    assert len(diag) == 1 and diag[0]["name"] == "홍길동(54321).show"
    why = diag[0]["why"]
    assert "한쇼" in why and "pptx" in why
    # 확장자만 바꾸면 된다고 오해하지 않게 — 겉이 같아 보여 그렇게들 한다
    assert "이름만" in why
    assert diag[0]["convertible"] is True


def test_이름_양식까지_어긋나면_그것도_말해_준다(_isolate_paths):
    """형식만 바꾸라고 하면, 시키는 대로 하고도 안 붙는 이유를 알 수 없다."""
    d = _setup(_isolate_paths)
    (d / "그냥이름.show").write_bytes(b"hancom")
    diag = M._scan_patient(d, deep=True)["ppt_diag"]
    assert diag[0]["convertible"] is False
    assert "이름 양식" in diag[0]["why"]


def test_폴더_내용에서도_덱으로_표시된다(_isolate_paths):
    """사진도 아니고 잡파일도 아니다 — 화면이 '한쇼' 표를 달 수 있어야 한다."""
    d = _setup(_isolate_paths)
    (d / "홍길동(54321).show").write_bytes(b"hancom")
    items = M.folder_contents(FOLDER)["items"]
    assert [(i["name"], i["kind"]) for i in items] == [("홍길동(54321).show", "deck")]


def test_한쇼가_저장한_pptx_는_그냥_쓴다(_isolate_paths):
    """한쇼로 내보낸 .pptx 는 보통의 OOXML 이다. 만든 프로그램을 따지지 않는다 —
    라벨과 사진 기하로만 읽으므로 파워포인트가 만든 것과 다를 이유가 없다."""
    d = _setup(_isolate_paths)
    (d / "홍길동(54321).pptx").write_bytes(b"x")     # 내용은 여기서 상관없다
    rec = M._scan_patient(d, deep=True)
    assert rec["ppt"] == "홍길동(54321).pptx"
    assert rec["ppt_diag"] == []                     # 고쳐야 할 것이 없다
