"""케이스 양식의 파생 자리(10·11) — 슬라이드 4 왼쪽을 따라간다.

  · 사진: 4 왼쪽에 넣으면 10·11 에도 같은 사진이 실린다
  · 구도: 4 왼쪽을 고치면 /api/face/adjust 응답의 face_editors 에 10·11 의 환산값
    (dx·dy 는 창 폭 비율만큼, 배율·회전은 그대로)이 함께 온다 — 화면이 그 장으로
    넘어갔을 때 옛 구도를 보이지 않게
"""
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import main                                                      # noqa: E402
from test_preview import _first_session                          # noqa: E402

pytestmark = pytest.mark.skipif(not main.CASE_ANCHORS or not main.MIRROR_CELLS,
                                reason="케이스 양식 또는 파생 자리가 없습니다")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_mirror_cells_follow_slide4_left_photo_and_editor(client):
    sid = _first_session(client)
    s = main.get_session(sid)
    pid = s.slots["SLOT_FRONT"]
    client.post("/api/assign", json={"session_id": sid, "photo_id": pid, "slot": "FACE"})
    src = main.MIRROR_SOURCE
    r = client.post("/api/face/assign", json={"session_id": sid, "cell": src, "photo_id": pid})
    assert r.status_code == 200, r.text
    slots = r.json()["face_slots"]
    for m in main.MIRROR_CELLS:
        assert slots[m] == pid, f"{m} 은 {src} 의 사진을 따라가야 한다"

    r = client.post("/api/face/adjust", json={"session_id": sid, "cell": src,
                                              "dx": 40, "dy": -20, "scale": 1.3, "angle": 4})
    assert r.status_code == 200, r.text
    eds = r.json()["face_editors"]
    a = main.CASE_ANCHORS[src].window
    for m in main.MIRROR_CELLS:
        b = main.CASE_ANCHORS[m].window
        k = b.w / a.w
        got = eds[m]
        assert abs(got["dx"] - 40 * k) < 0.05 and abs(got["dy"] - (-20 * k)) < 0.05, (m, got)
        assert got["angle"] == 4.0 and abs(got["scale"] - eds[src]["scale"]) < 1e-6

    # 파생 자리는 직접 못 고친다
    r = client.post("/api/face/adjust", json={"session_id": sid, "cell": main.MIRROR_CELLS[0],
                                              "dx": 0, "dy": 0, "scale": 1, "angle": 0})
    assert r.status_code == 400
