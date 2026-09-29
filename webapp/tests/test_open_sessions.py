"""업데이트를 막는 작업 — 탭이 닫히면 버리고, 남은 것은 목록으로 보이고 골라서 버린다."""
import os
import sys

from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import main                                                      # noqa: E402
import updater as Up                                             # noqa: E402
from test_preview import _first_session                          # noqa: E402


def _client():
    return TestClient(main.app)


def test_close_discards_the_session_and_is_idempotent():
    c = _client()
    sid = _first_session(c)
    assert sid in main.SESSIONS
    assert c.post(f"/api/session/{sid}/close").json()["ok"]
    assert sid not in main.SESSIONS
    assert c.post(f"/api/session/{sid}/close").json()["ok"], "이미 없어도 조용히 넘어간다"


def test_open_lists_work_with_who_photos_stage_and_last_activity():
    c = _client()
    sid = _first_session(c)
    s = main.get_session(sid)
    s.touched -= 3 * 3600                        # 세 시간 전에 마지막으로 만진 작업
    rows = {r["id"]: r for r in c.get("/api/sessions/open").json()["sessions"]}
    r = rows[sid]
    assert r["photos"] == len(s.photos) > 0
    assert r["stage"] in ("사진 추가", "자동 분류", "검수·조정")
    assert r["idle_s"] >= 3 * 3600 - 5
    assert "stale" not in r, "열린 창인지 짐작하지 않는다 — 사실만 준다"
    main.discard_session(s)


def test_apply_discards_chosen_sessions_before_checking(monkeypatch):
    monkeypatch.setattr(main, "SESSIONS", {})    # 다른 테스트가 남긴 세션과 섞이지 않게
    c = _client()
    sid = _first_session(c)
    seen = {}

    def fake_check(busy=False):
        seen["busy"] = busy
        return Up.UpdateStatus(ok=True, has_update=False)
    monkeypatch.setattr(main.Up, "check", fake_check)
    r = c.post("/api/update/apply", json={"discard": [sid]}).json()
    assert sid not in main.SESSIONS
    assert seen["busy"] is False, "버린 뒤에 확인해야 막히지 않는다"
    assert r["ok"] is False                      # 여기선 새 버전이 없다고 꾸몄다
