"""
사진 단위 반전(flip) 검증 — fastest_lap.

flip 은 슬롯이 아니라 사진에 달렸다. ① 기본값은 설정 그리드(현재 교합면만 on),
② 토글하면 표시 픽셀이 실제로 뒤집히고, ③ 그 사진과 얽힌 정합/기준영상 캐시가
무효화되어 다음 register 에서 다시 돈다.

실행: cd webapp && python -m pytest tests/test_flip_pixels.py -q
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import main as M  # noqa: E402

client = TestClient(M.app)


def _gradient_jpg():
    """위는 어둡고 아래는 밝은 그림 — 뒤집힘이 픽셀로 판별된다."""
    img = np.zeros((400, 600, 3), np.uint8)
    for y in range(400):
        img[y, :, :] = int(y / 399 * 255)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


def _upload_one(sid, pool="cur"):
    r = client.post(f"/api/photos/{sid}?pool={pool}",
                    files=[("files", ("g.jpg", io.BytesIO(_gradient_jpg()), "image/jpeg"))])
    assert r.status_code == 200, r.text
    return M.SESSIONS[sid].photos[-1]


def test_default_flip_follows_settings_grid():
    sid = client.post("/api/session", json={"folder": "반전검사"}).json()["session_id"]
    s = M.SESSIONS[sid]
    p = _upload_one(sid)
    with s.lock:
        M._put(s, p, "SLOT_UPPER", at=0)      # 현재 사진 교합면 → 기본 on
    assert p.flip is True
    with s.lock:
        M._put(s, p, "SLOT_FRONT", at=0)      # 정면으로 옮기면 기본 off
    assert p.flip is False


def test_flip_toggle_mirrors_served_pixels_and_invalidates():
    sid = client.post("/api/session", json={"folder": "반전검사2"}).json()["session_id"]
    s = M.SESSIONS[sid]
    p = _upload_one(sid)
    with s.lock:
        M._put(s, p, "SLOT_FRONT", at=0)
    s.framed["SLOT_FRONT"] = (p.id, p.flip, None)     # register 를 돈 셈 친다

    def top_mean():
        r = client.get(f"/api/thumb/{sid}/{p.id}")
        arr = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        return float(arr[:40].mean()), float(arr[-40:].mean())

    dark_top, bright_bottom = top_mean()
    assert dark_top < bright_bottom            # 원본: 위가 어둡다

    r = client.post("/api/flip", json={"session_id": sid, "photo_id": p.id, "on": True})
    assert r.status_code == 200, r.text
    assert p.flip is True and p.flip_user is True
    assert "SLOT_FRONT" not in s.framed        # 정합 결과 무효화

    top2, bottom2 = top_mean()
    assert top2 > bottom2                      # 뒤집힘: 위가 밝다

    # 사람이 토글한 값은 상자를 옮겨도 기본값이 덮지 않는다
    with s.lock:
        M._put(s, p, "SLOT_UPPER", at=0)
        M._put(s, p, "SLOT_FRONT", at=0)
    assert p.flip is True


def test_ref_flip_invalidates_reference_bake():
    sid = client.post("/api/session", json={"folder": "반전검사3"}).json()["session_id"]
    s = M.SESSIONS[sid]
    p = _upload_one(sid, pool="ref")
    with s.lock:
        M._put(s, p, "SLOT_FRONT", at=0)
    img = M._ref_bake(s, "SLOT_FRONT")
    assert img is not None and "SLOT_FRONT" in s.references
    client.post("/api/flip", json={"session_id": sid, "photo_id": p.id, "on": True})
    assert "SLOT_FRONT" not in s.references    # 베이크 무효화 — 다음에 다시 굽는다
    img2 = M._ref_bake(s, "SLOT_FRONT")
    assert img2 is not None
    assert not np.array_equal(img, img2)       # 실제로 다른(뒤집힌) 그림
