"""테스트는 **진짜 환자 자료를 건드릴 수 없어야 한다.**

`main` 은 기동할 때 저장 위치·감사 로그·설정 파일 경로를 모듈 변수로 굳힌다.
테스트가 그걸 그대로 쓰면 사용자의 자료 폴더에 `노트내보내기_111222777_54325`
같은 환자가 생기고, 감사 로그에도 그 기록이 남는다.

실제로 그랬다 — 감사 로그 227줄 중 225줄이 테스트가 쓴 것이었다. 감사 로그는
무엇이 언제 확정됐는지 따지는 기록이라, 거기 섞인 가짜 줄은 그냥 잡음이 아니다.

그래서 **모든 테스트**에서 네 경로를 임시 폴더로 돌린다(autouse). 테스트가 스스로
기억해서 하는 일이 아니라, 잊어도 안전한 쪽이 기본값이어야 한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    import main

    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    sess = tmp_path / "_sessions_tmp"
    sess.mkdir(exist_ok=True)
    monkeypatch.setattr(main, "ROOT", root, raising=False)
    monkeypatch.setattr(main, "SESS_ROOT", sess, raising=False)
    monkeypatch.setattr(main, "LOG_FILE", tmp_path / "_audit_log.jsonl", raising=False)
    monkeypatch.setattr(main, "SETTINGS_FILE", tmp_path / "settings.json", raising=False)
    yield root
