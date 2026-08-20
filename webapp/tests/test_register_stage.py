"""정합·프레이밍은 **배정이 확정된 뒤**에 돈다.

예전에는 `_classify` 가 분류 직후 정합까지 했다. 정합은 "이 사진이 **어느 자리**에
들어가는가"에 딸린 계산인데(기준영상이 자리마다 다르다) 자리가 정해지기 전에 한 번
돌리고 끝냈다. 그래서:

    분류기가 좌·우를 바꿔 넣은 걸 고침   →  틀린 자리 기준으로 잡힌 배치가 남음
    OTHERS 로 빠진 걸 수동 배정          →  정합이 아예 없음
    대표를 다른 장으로 교체              →  옛 대표의 배치를 물려받음

이제 화면의 `검수·조정으로` 버튼이 `/api/register` 를 부른다. 여기서는 그 계약을
확인한다 — 분류만으로는 안 돌고, 자리가 바뀌면 그 자리는 다시 돈다.
"""

from __future__ import annotations

import io
import os
import sys

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main                                                       # noqa: E402

HOSP, ORTHO, NAME = "111000222", "24680", "단계검사"
SLOTS = ["SLOT_FRONT", "SLOT_LEFT", "SLOT_RIGHT", "SLOT_UPPER", "SLOT_LOWER"]


def _jpg(i: int) -> bytes:
    img = np.full((900, 1200, 3), (30 + 40 * i, 90, 200 - 30 * i), np.uint8)
    rng = np.random.default_rng(i)
    for _ in range(30):
        p = tuple(int(x) for x in rng.integers(0, [1200, 900], 2))
        cv2.circle(img, p, 70, (255, 255, 255), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


# 분류기를 파일명으로 답하게 바꾼다. 여기서 보려는 것은 분류 정확도가 아니라
# **어느 단계에서 무엇이 도는가** 라, 합성 사진이 OTHERS 로 빠지면 아무것도 안 보인다.
CLASSES = ["IO_FRONT", "IO_RIGHT", "IO_LEFT", "IO_UPPER", "IO_LOWER"]


class _Pred:
    def __init__(self, label):
        self.label, self.confidence, self.probs = label, 0.99, {label: 0.99}


@pytest.fixture(autouse=True)
def mock_classifier(monkeypatch):
    class Stub:
        def predict(self, im, filename=""):
            return _Pred(CLASSES[int(filename.split(".")[0])])
    monkeypatch.setattr(main, "classifier", Stub())


def _upload(c, sid, n=len(CLASSES)):
    return c.post(f"/api/photos/{sid}",
                  files=[("files", (f"{i}.jpg", io.BytesIO(_jpg(i)), "image/jpeg"))
                         for i in range(n)])


@pytest.fixture
def sess(_isolate_paths):
    with TestClient(main.app) as c:
        sid = c.post("/api/session",
                     json={"folder": NAME}).json()["session_id"]
        assert _upload(c, sid).status_code == 200
        assert c.post(f"/api/classify/{sid}").status_code == 200
        s = main.get_session(sid)
        assert set(s.slots) == set(SLOTS), s.slots
        yield c, sid, s


def test_분류만으로는_정합하지_않는다(sess):
    _, _, s = sess
    assert s.framed == {}, "분류 단계가 정합까지 했다"


def test_register가_배정된_자리를_계산한다(sess):
    c, sid, s = sess
    r = c.post(f"/api/register/{sid}", json={})
    assert r.status_code == 200, r.text
    assert set(r.json()["done"]) == set(SLOTS)
    assert set(s.framed) == set(SLOTS)


def test_같은_대표면_다시_돌지_않는다(sess):
    c, sid, _ = sess
    c.post(f"/api/register/{sid}", json={})
    assert c.post(f"/api/register/{sid}", json={}).json()["done"] == []
    assert set(c.post(f"/api/register/{sid}",
                      json={"force": True}).json()["done"]) == set(SLOTS)


def test_좌우를_고치면_그_두_자리만_다시_돈다(sess):
    """이 테스트가 이 변경의 이유다."""
    c, sid, s = sess
    c.post(f"/api/register/{sid}", json={})
    left, right = s.slots["SLOT_LEFT"], s.slots["SLOT_RIGHT"]

    c.post("/api/assign", json={"session_id": sid, "photo_id": left,
                                "slot": "SLOT_RIGHT", "at": 0})
    c.post("/api/assign", json={"session_id": sid, "photo_id": right,
                                "slot": "SLOT_LEFT", "at": 0})
    assert s.slots["SLOT_LEFT"] == right and s.slots["SLOT_RIGHT"] == left

    done = c.post(f"/api/register/{sid}", json={}).json()["done"]
    assert set(done) == {"SLOT_LEFT", "SLOT_RIGHT"}, done
    assert s.framed["SLOT_LEFT"][0] == right and s.framed["SLOT_RIGHT"][0] == left


def test_나중에_배정된_사진도_계산된다(sess):
    """OTHERS 로 빠졌다가 사람이 자리를 준 사진 — 예전에는 cover-fit 으로 남았다."""
    c, sid, s = sess
    c.post(f"/api/register/{sid}", json={})
    pid = s.slots["SLOT_UPPER"]
    c.post("/api/assign", json={"session_id": sid, "photo_id": pid, "slot": None})
    assert "SLOT_UPPER" not in s.slots
    assert c.post(f"/api/register/{sid}", json={}).json()["done"] == []
    assert "SLOT_UPPER" not in s.framed, "비워진 자리의 기록이 남았다"

    c.post("/api/assign", json={"session_id": sid, "photo_id": pid,
                                "slot": "SLOT_UPPER", "at": 0})
    assert c.post(f"/api/register/{sid}", json={}).json()["done"] == ["SLOT_UPPER"]


def test_한_방_경로는_그대로_둘_다_한다(_isolate_paths):
    """`/api/upload` 는 API 테스트·외부 자동화용이라 고칠 사람이 없다."""
    with TestClient(main.app) as c:
        sid = c.post("/api/session",
                     json={"folder": NAME + "2"}).json()["session_id"]
        r = c.post(f"/api/upload/{sid}",
                   files=[("files", (f"{i}.jpg", io.BytesIO(_jpg(i)), "image/jpeg"))
                          for i in range(len(CLASSES))])
        assert r.status_code == 200, r.text
        s = main.get_session(sid)
        assert set(s.framed) == set(SLOTS) == set(s.slots)


def test_겹쳐볼_수_있는_기준_목록(sess, monkeypatch):
    """초진에는 기준이 없다 — 화면은 이 목록이 비면 토글을 감춘다."""
    c, sid, s = sess
    assert c.get(f"/api/references/{sid}").json() == {}, "초진에 기준영상이 있다"

    ref = np.full((630, 840, 3), 60, np.uint8)
    s.references["SLOT_FRONT"] = ref
    assert c.get(f"/api/references/{sid}").json() == {"SLOT_FRONT": True}

    r = c.get(f"/api/reference/{sid}/SLOT_FRONT")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (630, 840)

    assert c.get(f"/api/reference/{sid}/SLOT_LOWER").status_code == 404
