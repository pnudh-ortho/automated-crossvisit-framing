"""
투입 이미지 포맷 검증.

카메라가 내놓는 .jpg 중에는 실제로 **MPO**(다중 프레임 JPEG)인 것이 있다.
확장자만 믿고 원본 바이트를 그대로 두면 뒤 단계(베이크·뷰어)가 흔들린다 —
투입 시점에 실제 포맷을 보고 JPEG 으로 다시 인코딩하는 규약을 여기서 지킨다.

실행: cd webapp && python -m pytest tests/test_image_formats.py -q
"""
import io
import os
import sys

import numpy as np
from PIL import Image
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402

client = TestClient(M.app)

FOLDER = "포맷검사"


def _mpo_bytes(w=900, h=1200):
    """카메라가 내놓는 것과 같은 다중 프레임 JPEG(MPO)."""
    a = Image.fromarray(np.uint8(np.random.rand(h, w, 3) * 255))
    b = Image.fromarray(np.uint8(np.random.rand(h, w, 3) * 255))
    buf = io.BytesIO()
    a.save(buf, format="MPO", append_images=[b])
    return buf.getvalue()


def test_mpo_named_jpg_is_reencoded_on_ingest():
    """.jpg 로 위장한 MPO 는 투입 시점에 JPEG 으로 다시 인코딩돼야 한다."""
    sid = client.post("/api/session", json={"folder": FOLDER}).json()["session_id"]
    r = client.post(f"/api/photos/{sid}",
                    files=[("files", ("IMG_0001.jpg", io.BytesIO(_mpo_bytes()), "image/jpeg"))])
    assert r.status_code == 200, r.text
    photos = r.json()["photos"]
    assert len(photos) == 1, photos

    s = M.SESSIONS[sid]
    stored = s.photos[0].path
    with Image.open(stored) as im:
        assert im.format == "JPEG", im.format
    print(f"PASS MPO(.jpg) → 저장 포맷 JPEG")


def test_plain_jpeg_still_passes_through_untouched():
    """멀쩡한 JPEG 은 종전대로 원본 바이트 그대로 둔다(화질·EXIF 보존)."""
    img = Image.fromarray(np.uint8(np.random.rand(1200, 900, 3) * 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    raw = buf.getvalue()

    sid = client.post("/api/session", json={"folder": FOLDER}).json()["session_id"]
    client.post(f"/api/photos/{sid}",
                files=[("files", ("IMG_0002.jpg", io.BytesIO(raw), "image/jpeg"))])
    stored = M.SESSIONS[sid].photos[0].path
    assert stored.read_bytes() == raw, "다시 인코딩되어 원본이 아니다"
    print("PASS 정상 JPEG 은 원본 그대로")
