"""
케이스 덱의 얼굴 자리 배정 검증.

분류기는 FACE를 정면/45도/측면으로 나누지 못한다. 그래서 어느 사진이 어느
슬라이드 어느 쪽에 갈지는 사람이 고르고, 서버는 그 배정을 들고 있다가 확정할 때
덱에 꽂는다. 여기서 확인하는 것:

  - 양식에서 읽은 자리 표가 기대한 모양인가 (4·5·6 좌/우, 7·8·9 중앙)
  - 한 사진은 한 자리에만 놓이는가
  - 파생 자리(10·11)가 슬라이드 4 좌측을 따라가는가
  - 얼굴 상자에서 빠지면 자리도 놓아주는가
  - 확정하면 배정한 슬라이드에 실제로 들어가는가

실행: cd webapp && python -m pytest tests/test_face_cells.py -q
"""
import io
import os
import shutil
import sys

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import case_deck as CD  # noqa: E402
import main as M  # noqa: E402
import naming as N  # noqa: E402
import template as T  # noqa: E402

client = TestClient(M.app)

NAME, HOSP, ORTHO = "자리검사", "111222555", "54325"
IDS = N.Identifiers(NAME, HOSP, ORTHO)
FOLDER = N.folder_name(IDS, M.cfg.naming.folder_pattern)

pytestmark = pytest.mark.skipif(not M.CASE_ANCHORS, reason="케이스 양식이 없습니다")


def _synth(tag, seed):
    rng = np.random.default_rng(seed)
    img = np.full((1200, 900, 3), 25, np.uint8)
    for _ in range(60):
        c = tuple(int(x) for x in rng.integers(60, 255, 3))
        cv2.circle(img, tuple(int(x) for x in rng.integers(0, [900, 1200], 2)), 20, c, -1)
    cv2.putText(img, tag, (120, 620), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
    return cv2.imencode(".jpg", img)[1].tobytes()


@pytest.fixture
def patient(tmp_path_factory):
    d = M.ROOT / FOLDER
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def session_with_faces(patient):
    """얼굴 상자에 사진 3장이 든 세션."""
    sid = client.post("/api/session", json={"folder": FOLDER}).json()["session_id"]
    files = [("files", (f"f{i}.jpg", io.BytesIO(_synth(f"F{i}", i)), "image/jpeg"))
             for i in range(3)]
    assert client.post(f"/api/photos/{sid}", files=files).status_code == 200
    photos = client.post(f"/api/classify/{sid}").json()["photos"]
    for p in photos:
        client.post("/api/assign",
                    json={"session_id": sid, "photo_id": p["id"], "slot": "FACE", "at": None})
    face = client.post(f"/api/classify/{sid}").json()["review"]["face"]
    assert len(face) == 3
    return sid, face


def test_layout_lists_expected_cells():
    lay = client.get("/api/case/layout").json()
    assert lay["enabled"] is True
    cells = [c["cell"] for c in lay["cells"]]
    assert cells == ["4L", "4R", "5L", "5R", "6L", "6R", "7C", "8C", "9C"]
    # 중앙 자리는 좌/우가 아니다
    assert {c["pos"] for c in lay["cells"] if c["slide"] in (7, 8, 9)} == {"C"}
    mirrors = {m["cell"]: m["from"] for m in lay["mirrors"]}
    assert mirrors == {"10BIG": "4L", "11BIG": "4L"}


def test_layout_carries_geometry_for_preview():
    """화면이 슬라이드를 그대로 그리려면 좌표와 슬라이드 크기가 필요하다."""
    lay = client.get("/api/case/layout").json()
    assert lay["slide_w"] > 0 and lay["slide_h"] > 0
    for c in lay["cells"] + lay["mirrors"]:
        for k in ("x", "y", "w", "h"):
            assert k in c, f"{c['cell']}에 {k}가 없습니다"
        # 자리는 슬라이드 안에 있어야 한다
        assert 0 <= c["x"] < lay["slide_w"] and c["w"] <= lay["slide_w"] + 1e-6
        assert c["h"] <= lay["slide_h"] + 1e-6

    by = {c["cell"]: c for c in lay["cells"]}
    # 4L과 4R은 좌우로 갈라져 겹치지 않는다
    assert by["4L"]["x"] + by["4L"]["w"] <= by["4R"]["x"] + 1e-6
    # 중앙 자리는 가운데 정렬
    c7 = by["7C"]
    assert abs((c7["x"] + c7["w"] / 2) - lay["slide_w"] / 2) < 0.05


def test_slides_carry_meaning_labels():
    """'슬라이드 4'만으로는 무엇을 놓을 자리인지 알 수 없다. 라벨이 함께 와야 한다."""
    lay = client.get("/api/case/layout").json()
    by_slide = {}
    for c in lay["cells"] + lay["mirrors"]:
        by_slide.setdefault(c["slide"], set()).add(c["label"])
    # 한 슬라이드의 자리들은 같은 라벨을 공유한다
    for n, labels in by_slide.items():
        assert len(labels) == 1, f"슬라이드 {n}의 라벨이 갈립니다: {labels}"
    for n, want in M.cfg.case_deck.slide_labels.items():
        if n in by_slide:
            assert by_slide[n] == {want}, f"슬라이드 {n}"


def test_one_photo_lives_in_one_cell(session_with_faces):
    sid, face = session_with_faces
    pid = face[0]["id"]
    client.post("/api/face/assign", json={"session_id": sid, "cell": "4L", "photo_id": pid})
    r = client.post("/api/face/assign", json={"session_id": sid, "cell": "6R", "photo_id": pid})
    slots = r.json()["face_slots"]
    assert slots.get("6R") == pid
    assert "4L" not in slots          # 옮겨왔으므로 원래 자리는 비어야 한다


def test_mirror_cells_follow_slide4_left(session_with_faces):
    sid, face = session_with_faces
    pid = face[0]["id"]
    r = client.post("/api/face/assign",
                    json={"session_id": sid, "cell": "4L", "photo_id": pid})
    slots = r.json()["face_slots"]
    assert slots["10BIG"] == pid and slots["11BIG"] == pid

    # 비우면 파생 자리도 함께 빈다
    r = client.post("/api/face/assign",
                    json={"session_id": sid, "cell": "4L", "photo_id": None})
    slots = r.json()["face_slots"]
    assert "10BIG" not in slots and "11BIG" not in slots


def test_leaving_face_bin_releases_cell(session_with_faces):
    sid, face = session_with_faces
    pid = face[0]["id"]
    client.post("/api/face/assign", json={"session_id": sid, "cell": "5L", "photo_id": pid})
    # 얼굴 상자에서 빼면 잡아 둔 자리도 놓아준다
    client.post("/api/assign", json={"session_id": sid, "photo_id": pid, "slot": None, "at": None})
    slots = client.post(f"/api/classify/{sid}").json()["review"]["face_slots"]
    assert "5L" not in slots


def test_unknown_cell_is_rejected(session_with_faces):
    sid, face = session_with_faces
    r = client.post("/api/face/assign",
                    json={"session_id": sid, "cell": "10BIG", "photo_id": face[0]["id"]})
    assert r.status_code == 400      # 파생 자리는 직접 배정할 수 없다


def test_commit_places_faces_on_assigned_slides(session_with_faces, patient):
    sid, face = session_with_faces
    for cell, p in zip(["4L", "4R", "7C"], face):
        client.post("/api/face/assign",
                    json={"session_id": sid, "cell": cell, "photo_id": p["id"]})
    assert client.post(f"/api/commit/{sid}?allow_missing=true").status_code == 200

    prs = T.load_presentation(patient / N.ppt_filename(IDS, M.cfg.naming.ppt_pattern))
    assert len(prs.slides) == M.cfg.case_deck.keep_slides + 1

    def placed(n):
        return sorted(sh.name for sh in prs.slides[n - 1].shapes
                      if sh.name.startswith("PHOTO_FACE_"))

    assert placed(4) == ["PHOTO_FACE_4L", "PHOTO_FACE_4R"]
    assert placed(7) == ["PHOTO_FACE_7C"]
    assert placed(10) == ["PHOTO_FACE_10BIG"]   # 슬라이드 4 좌측을 다시 씀
    assert placed(11) == ["PHOTO_FACE_11BIG"]
    assert placed(5) == [] and placed(6) == []  # 배정 안 한 자리는 그대로 둔다

    # 얼굴 사진 이름은 구내 슬롯으로 오인되면 안 된다(ppt_reader가 창을 못 찾아 건너뛴다)
    cross = prs.slides[CD.cross_slide_index(prs)]
    assert not any(sh.name.startswith("PHOTO_FACE_") for sh in cross.shapes)

def _real(slots):
    """파생 자리(10·11)를 뺀 실제 자리만 — 미러가 값 검사를 흐리지 않게."""
    return {c: pid for c, pid in slots.items() if c in M.FACE_CELLS}


def test_자리를_옮기면_맞바꾼다(session_with_faces):
    """옮기기는 **자리를 바꾸는 일**이지 사진을 버리는 일이 아니다.

    예전에는 목표 자리를 그냥 덮어썼다. 그래서 한 번 옮길 때마다 떠난 자리는 비고
    목표 자리에 있던 사진은 판에서 통째로 사라졌다. 아홉 자리를 채워 둔 판이 두 번
    만에 일곱 자리로 줄었고, 화면에서는 "양식 순서대로 안 들어간다"로 보였다.
    """
    sid, _ = session_with_faces
    slots = _real(client.post(f"/api/classify/{sid}").json()["review"]["face_slots"])
    filled = [c for c in M.cfg.case_deck.face_auto_order if slots.get(c)]
    assert len(filled) >= 2, slots
    c1, c2 = filled[0], filled[1]
    p1, p2 = slots[c1], slots[c2]

    got = _real(client.post("/api/face/assign",
                json={"session_id": sid, "cell": c2, "photo_id": p1}).json()["face_slots"])
    assert got[c2] == p1
    assert got[c1] == p2                  # 밀린 사진이 상대의 빈 자리로 온다
    # 채워진 자리 수가 줄지 않는다 — 예전에는 옮길 때마다 하나씩 사라졌다
    assert len(got) == len(slots)


def test_상자에서_끌어오면_밀린_사진은_상자로_간다(session_with_faces):
    """맞바꿀 상대가 없는 경우 — 자리에서만 빠지고 사진 자체는 그대로다."""
    sid, _ = session_with_faces
    slots = _real(client.post(f"/api/classify/{sid}").json()["review"]["face_slots"])
    filled = [c for c in M.cfg.case_deck.face_auto_order if slots.get(c)]
    c1, c2 = filled[0], filled[1]
    p1, p2 = slots[c1], slots[c2]

    client.post("/api/face/assign", json={"session_id": sid, "cell": c1, "photo_id": None})
    got = _real(client.post("/api/face/assign",
                json={"session_id": sid, "cell": c2, "photo_id": p1}).json()["face_slots"])
    assert got[c2] == p1
    assert c1 not in got                  # 떠나온 자리가 없으니 되돌려 놓을 곳도 없다
    assert p2 not in got.values()         # 밀린 사진은 상자로


def test_제자리에_다시_놓아도_그대로다(session_with_faces):
    """같은 자리에 다시 놓는 것은 아무 일도 아니어야 한다 — 잡아 둔 구도까지 포함해."""
    sid, _ = session_with_faces
    slots = _real(client.post(f"/api/classify/{sid}").json()["review"]["face_slots"])
    c = next(c for c in M.cfg.case_deck.face_auto_order if slots.get(c))
    r = client.post("/api/face/assign",
                    json={"session_id": sid, "cell": c, "photo_id": slots[c]})
    assert _real(r.json()["face_slots"]) == slots
