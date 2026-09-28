"""켤 때 지우는 덱 기억 — 닿는 저장 위치에서 사라진 폴더만 지운다."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import main as M                                                  # noqa: E402


def _settings(tmp_path, monkeypatch, body):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(M, "SETTINGS_FILE", f, raising=False)
    return f


def test_missing_folder_under_live_root_is_pruned(tmp_path, monkeypatch):
    root = tmp_path / "root"; (root / "있음").mkdir(parents=True)
    f = _settings(tmp_path, monkeypatch, {
        "roots": [str(root)],
        "ppt_choice": {str(root / "있음"): "a.pptx", str(root / "없음"): "b.pptx"}})
    M._prune_ppt_choice()
    assert json.loads(f.read_text(encoding="utf-8"))["ppt_choice"] == {str(root / "있음"): "a.pptx"}


def test_entries_under_unmounted_root_are_kept(tmp_path, monkeypatch):
    """안 꽂힌 외장 드라이브 · 안 붙은 공유 폴더 — 루트째 안 보이면 건드리지 않는다."""
    dead = tmp_path / "unplugged"          # 만들지 않는다
    f = _settings(tmp_path, monkeypatch, {
        "roots": [str(dead)],
        "ppt_choice": {str(dead / "환자"): "a.pptx"}})
    M._prune_ppt_choice()
    assert json.loads(f.read_text(encoding="utf-8"))["ppt_choice"] == {str(dead / "환자"): "a.pptx"}


def test_key_outside_every_root_is_pruned(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    stray = tmp_path / "elsewhere" / "환자"
    f = _settings(tmp_path, monkeypatch, {
        "roots": [str(root)], "ppt_choice": {str(stray): "a.pptx"}})
    M._prune_ppt_choice()
    assert json.loads(f.read_text(encoding="utf-8")).get("ppt_choice", {}) == {}


def test_legacy_name_key_kept_when_no_root_is_reachable(tmp_path, monkeypatch):
    dead = tmp_path / "unplugged"
    f = _settings(tmp_path, monkeypatch, {"roots": [str(dead)], "ppt_choice": {"환자": "a.pptx"}})
    M._prune_ppt_choice()
    assert json.loads(f.read_text(encoding="utf-8"))["ppt_choice"] == {"환자": "a.pptx"}
