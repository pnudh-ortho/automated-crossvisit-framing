"""업데이트 확인이 **인코딩 때문에 무너지지 않는지** 지킨다.

한 번 이렇게 무너졌다. `_git` 이 `text=True` 만 주고 있었고, 그건 로케일 인코딩
(한국어 Windows = CP949)으로 디코딩한다는 뜻이다. git 은 커밋 제목을 UTF-8 로
내보내므로 제목에 한글이 한 자만 있어도:

    UnicodeDecodeError → 리더 스레드 사망 → p.stdout is None → TypeError → 500

화면은 `.catch(() => null)` 로 그 500 을 삼켜서 **아무 일도 없는 것처럼** 보였다.
사용자 쪽 컴퓨터는 두 커밋 뒤처진 채로 몇 시간을 그대로 있었다.

여기서는 진짜 git 저장소를 하나 만들어 한글·이모지 제목으로 커밋하고, `_git` 이
그걸 그대로 읽어오는지 본다. 로케일을 CP949 로 강제해도 마찬가지여야 한다 —
그게 사고가 난 환경이다.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

KOREAN = "케이스 양식을 16장으로 줄이고 저장소에 포함"


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    def git(*a):
        subprocess.run(["git", "-C", str(d), *a], check=True,
                       capture_output=True, env={**env, "PATH": "/usr/bin:/bin"})
    subprocess.run(["git", "init", "-q", str(d)], check=True, capture_output=True)
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("config", "i18n.commitEncoding", "UTF-8")
    (d / "f").write_text("1")
    git("add", "f")
    git("commit", "-q", "-m", KOREAN)
    return d


@pytest.fixture
def upd(tmp_path, monkeypatch):
    """`updater` 를 임시 저장소에 붙여 다시 읽는다."""
    import updater
    monkeypatch.setattr(updater, "REPO", _repo(tmp_path))
    return updater


def test_한글_커밋제목을_그대로_읽는다(upd):
    code, out = upd._git("log", "--oneline", "--no-decorate")
    assert code == 0
    assert KOREAN in out, f"제목이 깨졌다: {out!r}"


def test_로케일이_CP949여도_읽는다(upd, monkeypatch):
    """사고가 난 환경. 로케일이 무엇이든 git 출력은 UTF-8 로 읽어야 한다."""
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PYTHONIOENCODING", "cp949")
    code, out = upd._git("log", "--oneline", "--no-decorate")
    assert code == 0 and KOREAN in out


def test_실패해도_예외를_안_던진다(upd):
    """모듈이 약속한 것 — 업데이트 실패가 앱을 죽이면 안 된다."""
    code, out = upd._git("이런-명령은-없다")
    assert code != 0 and isinstance(out, str)


def test_없는_저장소에서도_사유만_돌려준다(upd, tmp_path, monkeypatch):
    monkeypatch.setattr(upd, "REPO", tmp_path / "없음")
    st = upd.check()
    assert st.ok is False and st.reason and st.has_update is False


def test_로케일_인코딩에_기대는_호출이_남아있지_않다():
    """`text=True` 는 로케일로 디코딩한다 — 이 사고의 근원이다.

    새로 추가하려면 `encoding=` 을 함께 주어야 한다. 주석·문서는 세지 않는다.
    """
    import ast
    root = Path(__file__).resolve().parents[2]
    bad = []
    for f in root.rglob("*.py"):
        if ".venv" in f.parts or "site-packages" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            kw = {k.arg: k.value for k in n.keywords if k.arg}
            decodes = any(isinstance(kw.get(a), ast.Constant) and kw[a].value
                          for a in ("text", "universal_newlines"))
            if decodes and "encoding" not in kw:
                bad.append(f"{f.relative_to(root)}:{n.lineno}")
    assert not bad, "로케일 인코딩에 기대는 호출: " + ", ".join(bad)


def test_powershell_선택창이_UTF8로_출력한다():
    """한글이 든 경로(`C:\\사용자\\바탕화면`)가 깨져 돌아오면 안 된다."""
    import main
    assert "[Console]::OutputEncoding" in main._PS_PICK


def test_확인이_터져도_500이_아니다(monkeypatch):
    import main
    monkeypatch.setattr(main.Up, "check",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("터짐")))
    st = main._safe_check()
    assert st.ok is False and "터짐" in st.reason


def test_화면_파일은_캐시하지_않는다():
    """브라우저가 옛 `app.js` 를 물어보지도 않고 쓰면 새 백엔드 + 옛 화면이 된다.

    실제로 Edge 가 그랬다. 서버 로그에 `GET /` 은 있는데 `GET /static/app.js` 가
    없어서 알았다 — 없는 버그를 한참 찾았다. 이 앱은 `git pull` 로 자기를 갱신하니
    갱신할 때마다 같은 일이 날 수 있다.
    """
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        for path in ("/", "/static/app.js", "/static/style.css"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "no-cache" in r.headers.get("cache-control", ""), path
        # no-cache 는 '쓰지 마라'가 아니라 '물어보고 써라' — 304 로 끝나야 한다
        et = c.get("/static/app.js").headers["etag"]
        assert c.get("/static/app.js", headers={"If-None-Match": et}).status_code == 304
