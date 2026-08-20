"""설정 저장은 **읽은 그 파일에만** 쓴다.

`SETTINGS_FILE` 은 모듈 전역이라 읽을 때와 쓸 때 각각 다시 참조된다. 그 사이에
값이 바뀌면 *A 에서 읽어 B 에 쓰는* 일이 생긴다 — 실제로 테스트가 이 전역을 임시
경로로 바꿔치기하는 동안 기동 스레드(바로가기 아이콘 수리)가 저장을 돌아, 빈
dict 를 진짜 설정 파일에 덮어써 저장 위치와 이름 양식이 통째로 날아갔다.

실행: cd webapp && python -m pytest tests/test_settings_path.py -q
"""
import json
import os
import pathlib
import sys

from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402

KEEP = {"root": "/자료", "months_unit": "half", "folder_patterns": ["{name}"]}


def test_읽는_도중_경로가_바뀌어도_원래_설정을_지우지_않는다(tmp_path, monkeypatch):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(KEEP, ensure_ascii=False), encoding="utf-8")
    scratch = tmp_path / "scratch.json"          # 바꿔치기해 둔 경로 (아직 없다)
    monkeypatch.setattr(M, "SETTINGS_FILE", scratch)

    plain_read = pathlib.Path.read_text

    def flip(self, *a, **kw):
        # 읽는 순간 전역이 진짜 경로로 되돌아간다 (테스트 teardown 과 같은 상황)
        M.SETTINGS_FILE = real
        return plain_read(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", flip)
    M._save_setting("shortcut_icon", "crocs-2.ico")
    monkeypatch.setattr(pathlib.Path, "read_text", plain_read)

    assert json.loads(real.read_text(encoding="utf-8")) == KEEP, "진짜 설정이 덮였다"
    assert json.loads(scratch.read_text(encoding="utf-8")) == {
        "shortcut_icon": "crocs-2.ico"}, "읽은 파일이 아닌 곳에 썼다"


def test_다른_값을_보존한다(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(KEEP, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(M, "SETTINGS_FILE", f)
    M._save_setting("save_raw", True)
    got = json.loads(f.read_text(encoding="utf-8"))
    assert got == {**KEEP, "save_raw": True}


def test_테스트에서는_바로가기_수리_스레드를_띄우지_않는다(monkeypatch):
    """설정 파일에 쓰는 스레드다 — 테스트가 경로를 바꿔치기한 동안 돌면 안 된다."""
    ran = []
    monkeypatch.setattr(M, "_repair_shortcut_icon", lambda: ran.append(1))
    with TestClient(M.app):
        pass
    assert not ran, "테스트 중에 바로가기 수리가 돌았다"
