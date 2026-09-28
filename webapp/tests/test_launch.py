"""기동 스모크 — `python webapp/backend/main.py` 로 **실제로 뜬** 서버에 라우트가 다 있나.

TestClient 는 `main` 을 모듈로 임포트하므로 잡지 못하는 결함이 있다: 실행 파일로
띄우면 이 파일이 `__main__` 이 되고, fastlap 의 `import main` 이 같은 파일을 한 번
더 읽어 **다른 app** 에 라우터를 붙였다. 뜬 서버에는 /api/fl/* 가 없었고(404)
테스트는 전부 초록이었다. 그래서 여기서는 서브프로세스로 띄워 본다.

설정 파일은 **진짜 것을 읽기만** 한다 — 서버는 기동 때 settings.json 을 읽을 뿐
사람이 설정을 고치기 전에는 쓰지 않는다(기동 때 도는 두 쓰기 — 바로가기 아이콘
표시, 사라진 폴더의 PPT 선택 정리 — 는 한 번 돈 뒤로는 하는 일이 없다). 잠금 파일
`.server.json` 은 새 서버가 덮어쓰므로 미리 받아 두었다 되돌린다.

실행: cd webapp && python -m pytest tests/test_launch.py -q
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MAIN = REPO / "webapp" / "backend" / "main.py"
MODELS = REPO / "models" / "_installed"
LOCK = REPO / ".server.json"          # main.LOCK_FILE — 임포트하지 않고 같은 자리를 본다

pytestmark = pytest.mark.skipif(
    not PY.exists() or not MAIN.exists() or not any(MODELS.glob("*.onnx")),
    reason=".venv 파이썬이나 모델 가중치가 없습니다")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, timeout: float = 5.0) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status


def _wait_health(port: int, proc, deadline: float) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"서버가 뜨기 전에 죽었다 (exit {proc.returncode})")
        try:
            if _get(url, timeout=2.0) == 200:
                return
        except (urllib.error.URLError, OSError, socket.timeout):
            time.sleep(0.5)
    raise AssertionError("90초 안에 /api/health 가 응답하지 않았다")


def test_server_launched_as_a_script_serves_fastlap_routes(tmp_path):
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "CROCS_NO_BROWSER": "1",
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    had_lock = LOCK.read_bytes() if LOCK.exists() else None
    log = tmp_path / "server.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen([str(PY), str(MAIN)], cwd=str(REPO), env=env,
                                stdout=out, stderr=subprocess.STDOUT)
    try:
        _wait_health(port, proc, time.time() + 90)
        assert _get(f"http://127.0.0.1:{port}/api/fl/prefs") == 200, \
            "/api/fl/* 가 뜬 서버에 없다 — main 이 두 번 읽혀 라우터가 다른 app 에 붙었다"
        assert _get(f"http://127.0.0.1:{port}/api/patients", timeout=30.0) == 200
        assert json.loads(LOCK.read_text(encoding="utf-8"))["port"] == port
    except Exception:
        sys.stderr.write(log.read_text(encoding="utf-8", errors="replace")[-3000:])
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        # 잠금 파일은 우리가 덮어썼다 — 원래 돌던 서버의 것을 되돌린다
        if had_lock is None:
            LOCK.unlink(missing_ok=True)
        else:
            LOCK.write_bytes(had_lock)
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "/api/fl/*" not in text or "[경고]" not in text, "기동 자가 점검이 경고를 냈다"
