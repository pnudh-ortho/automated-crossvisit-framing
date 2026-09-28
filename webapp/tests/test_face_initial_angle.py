"""얼굴 자리의 첫 구도는 **회전 0** 으로 시작한다.

프레이밍 모델의 배율·이동은 받되 회전은 쓰지 않는다 — 곧게 선 원본에서 시작해
필요할 때만 사람이 돌린다. 되돌리기 자리(face_editors0)도 같다. 그 뒤
`/api/face/adjust` 로 돌리는 것은 그대로 된다.

실행: cd webapp && python -m pytest tests/test_face_initial_angle.py -q
"""
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main                                                      # noqa: E402
from coords import EditorState                                   # noqa: E402
from test_preview import _first_session                          # noqa: E402

pytestmark = pytest.mark.skipif(not main.CASE_ANCHORS, reason="케이스 양식이 없습니다")

MODEL = EditorState(dx_px=10.0, dy_px=-5.0, scale=1.2, angle_deg=7.5)


class _Res:
    ok = True


@pytest.fixture
def client():
    return TestClient(main.app)


def test_face_initial_fit_has_zero_angle_but_keeps_scale_and_shift(client, monkeypatch):
    # 모델이 있었다면 잡았을 값을 심는다 — 보려는 것은 배선이지 모델이 아니다
    monkeypatch.setattr(main, "_face_frame_result", lambda s, photo: _Res())
    monkeypatch.setattr(main, "framing_to_editor", lambda res, win, pw, ph: MODEL)
    sid = _first_session(client)
    s = main.get_session(sid)
    pid = s.slots["SLOT_FRONT"]
    r = client.post("/api/assign", json={"session_id": sid, "photo_id": pid, "slot": "FACE"})
    assert r.status_code == 200, r.text
    rev = r.json()["review"]
    cell = next(c for c, p in rev["face_slots"].items() if p == pid and c in main.FACE_CELLS)
    assert rev["face_framing"][cell] == "model"
    for book in ("face_editors", "face_editors0"):
        st = rev[book][cell]
        assert st["angle"] == 0.0, (book, st)
        assert st["dx"] == 10.0 and st["dy"] == -5.0
        assert st["scale"] >= 1.2                 # cover 하한만 걸릴 수 있다

    # 그 뒤 사람이 돌리는 것은 된다 — 되돌리기 자리는 그대로 0
    r = client.post("/api/face/adjust", json={"session_id": sid, "cell": cell,
                                              "dx": 10, "dy": -5, "scale": 1.2, "angle": 3.0})
    assert r.status_code == 200, r.text
    rev = client.post(f"/api/register/{sid}", json={}).json()["review"]
    assert rev["face_editors"][cell]["angle"] == 3.0
    assert rev["face_editors0"][cell]["angle"] == 0.0
