"""기준 사진 prewarm — 스테이징 즉시 뒤에서 분류·프레이밍이 돈다.

정합 버튼을 눌렀을 때 기준 쪽 무거운 계산이 이미 끝나 있게 하는 파이프라이닝이다.
여기서는 ① 스테이징이 prewarm 을 던지고 라벨이 뒤에서 붙는 것, ② classify 가
그 라벨을 다시 계산하지 않는 것(픽셀은 안 변한다)을 확인한다.

실행: cd webapp && python -m pytest tests/test_prewarm.py -q
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import main as M  # noqa: E402

client = TestClient(M.app)


def _jpg():
    img = np.full((900, 1200, 3), 90, np.uint8)
    cv2.circle(img, (600, 450), 200, (255, 255, 255), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _wait_labeled(photo, timeout=30.0):
    t0 = time.time()
    while photo.label is None and time.time() - t0 < timeout:
        time.sleep(0.1)
    return photo.label is not None


def test_ref_staging_prewarms_label():
    sid = client.post("/api/session", json={"folder": "프리웜검사"}).json()["session_id"]
    r = client.post(f"/api/photos/{sid}?pool=ref",
                    files=[("files", ("r.jpg", io.BytesIO(_jpg()), "image/jpeg"))])
    assert r.status_code == 200, r.text
    photo = M.SESSIONS[sid].photos[0]
    assert _wait_labeled(photo), "prewarm 이 라벨을 붙이지 않았다"


def test_classify_does_not_repredict_prewarmed(monkeypatch):
    sid = client.post("/api/session", json={"folder": "프리웜검사2"}).json()["session_id"]
    client.post(f"/api/photos/{sid}?pool=ref",
                files=[("files", ("r.jpg", io.BytesIO(_jpg()), "image/jpeg"))])
    photo = M.SESSIONS[sid].photos[0]
    assert _wait_labeled(photo)
    # 이제 classify 가 predict 를 다시 부르면 실패하는 스텁을 심는다
    class Boom:
        def predict(self, im, filename=""):
            raise AssertionError("prewarm 된 사진을 다시 분류했다")
    monkeypatch.setattr(M, "classifier", Boom())
    r = client.post(f"/api/classify/{sid}")
    assert r.status_code == 200, r.text
    # 라벨은 그대로, 상자에도 들어갔다
    assert photo.label is not None
    if photo.label in M.cfg.slot_by_class or photo.label == "FACE":
        assert photo.slot is not None
