"""저장 위치가 사라졌을 때 — 외장 드라이브를 빼고 켠 경우.

"아직 안 골랐다"(첫 실행)와 "골라 뒀는데 지금 닿지 않는다"는 완전히 다른 일이다.
후자를 첫 실행처럼 물으면 사용자가 임시 위치를 확정해 버리고, 그 순간 설정에
남아 있던 외장 경로가 덮여 사라진다 — 환자 자료를 어디 뒀는지 앱이 잊는다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402


def _settings(d: dict):
    M.SETTINGS_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_아직_안_고른_상태는_첫_실행(_isolate_paths):
    _settings({})
    h = M.health()
    assert h["needs_setup"] is True
    assert h["root_missing"] == ""


def test_경로가_사라지면_첫_실행으로_묻지_않는다(_isolate_paths):
    gone = "/mnt/외장하드/환자자료"
    _settings({"root": gone})
    h = M.health()
    assert h["needs_setup"] is False        # 첫 실행 화면을 띄우면 안 된다
    assert h["root_missing"] == gone        # 어디가 없는지 화면이 말할 수 있다


def test_다시_확인은_설정을_건드리지_않는다(_isolate_paths):
    gone = "/mnt/외장하드/환자자료"
    _settings({"root": gone})
    r = M.root_recheck()
    assert r["ok"] is False and r["path"] == gone
    # 실패해도 설정의 경로는 그대로 — 드라이브를 꽂으면 돌아갈 수 있어야 한다
    assert json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))["root"] == gone


def test_드라이브가_돌아오면_그리로_되돌아간다(_isolate_paths, tmp_path):
    back = tmp_path / "외장하드"
    back.mkdir()
    _settings({"root": str(back)})
    assert M.health()["root_missing"] == ""
    r = M.root_recheck()
    assert r["ok"] is True and r["root"] == str(back)
    assert M.ROOT == back


def test_저장_위치를_바꿔도_다른_설정이_남는다(_isolate_paths, tmp_path):
    """예전에는 {"root": ...} 하나로 덮어써서 이름 양식·표기 설정이 다 날아갔다."""
    _settings({
        "root": str(_isolate_paths),
        "folder_patterns": ["{seq}.{name}({ortho_id})"],
        "ppt_patterns": ["{name}({ortho_id}).pptx"],
        "months_unit": "half", "copy_shapes": "all", "letterbox_color": "FFFFFF",
        "ppt_choice": {"홍길동_1_2": "보관/a.pptx"},
    })
    new_root = tmp_path / "새저장위치"
    new_root.mkdir()
    assert M.set_root(M.RootReq(path=str(new_root)))["root"] == str(new_root)

    d = json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert d["root"] == str(new_root)                       # 바뀐 것은 이것뿐
    assert d["folder_patterns"] == ["{seq}.{name}({ortho_id})"]
    assert d["ppt_patterns"] == ["{name}({ortho_id}).pptx"]
    assert d["months_unit"] == "half" and d["copy_shapes"] == "all"
    assert d["letterbox_color"] == "FFFFFF"
    assert d["ppt_choice"] == {"홍길동_1_2": "보관/a.pptx"}
