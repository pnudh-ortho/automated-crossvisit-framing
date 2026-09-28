"""본편의 `/api/flip` — 사람이 사진 한 장의 상하반전을 정한다.

Fastest Lap 의 `/api/fl/flip` 과 같은 규약이다:
  · 편집기 값은 반전 화면 기준이라 함께 환산된다 (dy·angle 부호)
  · 사람이 고른 값은 자리가 바뀌어도 덮이지 않는다
  · 정합·프레이밍 기록을 지워 다음 `/api/register` 가 그 자리를 다시 돈다
추가 작업용 사진은 반전이 없다 — 거절한다.

실행: cd webapp && python -m pytest tests/test_flip_main.py -q
"""
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main                                                      # noqa: E402
from coords import EditorState                                   # noqa: E402
from test_preview import _first_session                          # noqa: E402

FRAMED = EditorState(dx_px=12.5, dy_px=-8.0, scale=1.14, angle_deg=2.5)


@pytest.fixture
def client():
    return TestClient(main.app)


def _flip(client, sid, pid, on):
    r = client.post("/api/flip", json={"session_id": sid, "photo_id": pid, "flip_v": on})
    assert r.status_code == 200, r.text
    return r.json()


def test_flip_mirrors_editor_and_reruns_registration_for_that_slot(client, monkeypatch):
    def fake(s, photo, win, fallback_badge=None, bgr=None):
        photo.editor = main.flip_editor_v(FRAMED) if photo.flip_v else FRAMED
        photo.framing = "model"
    monkeypatch.setattr(main, "_auto_frame", fake)
    sid = _first_session(client)
    assert client.post(f"/api/register/{sid}", json={}).status_code == 200
    s = main.get_session(sid)
    pid = s.slots["SLOT_FRONT"]
    photo = main._photo(s, pid)
    assert photo.flip_v is False
    before = photo.editor

    out = _flip(client, sid, pid, True)
    assert set(out) == {"review", "photos"}
    got = out["review"]["slots"]["SLOT_FRONT"]
    assert got["flip_v"] is True
    assert got["editor"] == {"dx": before.dx_px, "dy": -before.dy_px,
                             "scale": before.scale, "angle": -before.angle_deg}
    assert got["editor0"]["angle"] == -FRAMED.angle_deg
    # 그 자리만 다시 돈다
    done = client.post(f"/api/register/{sid}", json={}).json()["done"]
    assert done == ["SLOT_FRONT"]
    # 다시 돈 구도도 반전 화면 기준이다
    assert main._photo(s, pid).editor == main.flip_editor_v(FRAMED)


def test_user_choice_survives_moving_between_slots(client):
    sid = _first_session(client)
    s = main.get_session(sid)
    pid = s.slots["SLOT_UPPER"]
    assert main._photo(s, pid).flip_v is True, "교합면은 기본이 반전"
    _flip(client, sid, pid, False)
    r = client.post("/api/assign", json={"session_id": sid, "photo_id": pid,
                                         "slot": "SLOT_LOWER", "at": 0})
    assert main._photo(s, pid).flip_v is False, "사람이 고른 값이 자리 기본값에 덮였다"
    assert r.json()["review"]["slots"]["SLOT_LOWER"]["flip_v"] is False


def test_extra_photos_keep_their_flip_and_can_be_flipped(client):
    """추가 작업용은 자리 기본값이 없다 — 들어올 때 상태를 그대로 두고 ↕ 로 정한다.
    거울로 찍은 부분 사진이 여기 오기도 한다."""
    sid = _first_session(client)
    s = main.get_session(sid)
    pid = s.slots["SLOT_UPPER"]
    before = main._photo(s, pid).flip_v
    client.post("/api/assign", json={"session_id": sid, "photo_id": pid, "slot": "EXTRA"})
    assert main._photo(s, pid).flip_v is before, "상자에 넣는다고 반전이 바뀌면 안 된다"
    r = client.post("/api/flip", json={"session_id": sid, "photo_id": pid, "flip_v": not before})
    assert r.status_code == 200, r.text
    assert main._photo(s, pid).flip_v is (not before)
    # 계획에도 실린다 — 저장 검토 줄에 "상하반전" 이 붙는 근거
    plan = client.get(f"/api/plan/{sid}").json()
    ex = [e for e in plan["extras"] if e["pid"] == pid]
    assert ex and ex[0]["flip_v"] is (not before)
