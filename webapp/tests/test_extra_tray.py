"""'추가 작업용' — 슬라이드에 넣지 않고 손으로 잘라 파일로만 저장하는 사진.

여기서 못박는 것:

  · 상자와 따로 산다 — EXTRA 로 옮기면 어느 상자·자리에서도 빠지고, 다시 상자로
    가면 EXTRA 에서 빠지며 거기서 잡은 구도도 버려진다
  · 검수 JSON 에 `extra`·`extra_editors`·`extra_window` 가 실린다
  · `/api/extra/adjust` 는 배율만 막고 나머지는 그대로 받는다
  · 저장 계획·확정이 `교정번호_차수/교정번호_차수_extra (n).jpg` 로 굽는다 — 비어 있으면 폴더도 없다

실행: cd webapp && python -m pytest tests/test_extra_tray.py -q
"""
import io
import json
import os
import sys

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import crop as Cr                                                # noqa: E402
import main                                                      # noqa: E402
from coords import EditorState                                   # noqa: E402
from test_preview import SLOT_KEYS, SLOTS, _synth                # noqa: E402

NAME, HOSP, ORTHO = "추가작업환자", "123456789", "54329"
FOLDER = f"{NAME}_{HOSP}_{ORTHO}"


@pytest.fixture
def client():
    return TestClient(main.app)


def _session(client, n_extra=1):
    """다섯 자리를 채우고 여분 사진 `n_extra` 장을 EXTRA 로 보낸 초진 세션."""
    r = client.post("/api/session/first",
                    json={"name": NAME, "hospital_id": HOSP, "ortho_id": ORTHO})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    names = SLOTS + [f"extra{i}" for i in range(n_extra)]
    files = [("files", (f"{s}.jpg", io.BytesIO(_synth(s, 300 + i)), "image/jpeg"))
             for i, s in enumerate(names)]
    r = client.post(f"/api/upload/{sid}", files=files)
    assert r.status_code == 200, r.text
    photos = r.json()["photos"]
    for photo, slot in zip(photos, SLOT_KEYS):
        _assign(client, sid, photo["id"], slot, at=0)
    extras = [p["id"] for p in photos[len(SLOT_KEYS):]]
    for pid in extras:
        _assign(client, sid, pid, "EXTRA")
    return sid, extras


def _assign(client, sid, pid, slot, at=None):
    r = client.post("/api/assign", json={"session_id": sid, "photo_id": pid,
                                         "slot": slot, "at": at})
    assert r.status_code == 200, r.text
    return r.json()["review"]


def test_extra_leaves_every_bin_and_shows_in_review(client):
    sid, (pid,) = _session(client)
    s = main.get_session(sid)
    assert s.extra == [pid]
    assert all(pid not in lst for lst in s.bins.values())
    assert main._photo(s, pid).slot == "EXTRA"

    rev = client.post("/api/register/{}".format(sid), json={}).json()["review"]
    assert rev["extra"] == [pid]
    assert rev["extra_editors"] == {pid: {"dx": 0.0, "dy": 0.0, "scale": 1.0, "angle": 0.0}}
    win = main.SLOT_WINDOWS["SLOT_FRONT"]
    assert rev["extra_window"] == {"w": win.w, "h": win.h}
    # 슬롯 다섯은 그대로다 — EXTRA 는 자리를 뺏지 않는다
    assert rev["missing"] == []


def test_extra_keeps_order_and_at_reorders(client):
    sid, (a, b) = _session(client, n_extra=2)
    s = main.get_session(sid)
    assert s.extra == [a, b]
    # 구도를 잡아 둔 채 같은 곳 안에서 순서만 바꾼다 — 구도는 남는다
    client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": b,
                                           "dx": 3, "dy": 4, "scale": 1.1, "angle": 2})
    rev = _assign(client, sid, b, "EXTRA", at=0)
    assert rev["extra"] == [b, a]
    assert rev["extra_editors"][b]["angle"] == 2.0


def test_leaving_extra_drops_its_editor(client):
    sid, (pid,) = _session(client)
    client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid,
                                           "dx": 1, "dy": 1, "scale": 1.2, "angle": 5})
    s = main.get_session(sid)
    assert pid in s.extra_editors
    rev = _assign(client, sid, pid, "SLOT_FRONT", at=1)     # 정면 상자의 추가 촬영본으로
    assert rev["extra"] == [] and rev["extra_editors"] == {}
    assert pid not in s.extra_editors and pid in s.bins["SLOT_FRONT"]
    # 아예 빼도(OTHERS) 같다
    _assign(client, sid, pid, "EXTRA")
    rev = _assign(client, sid, pid, None)
    assert rev["extra"] == [] and main._photo(s, pid).slot is None


def test_extra_adjust_clamps_only_scale(client):
    sid, (pid,) = _session(client)
    r = client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid,
                                               "dx": -900, "dy": 700, "scale": 9, "angle": 200})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "editor": {"dx": -900.0, "dy": 700.0,
                                               "scale": 2.0, "angle": 200.0}}
    r = client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid,
                                               "dx": 0, "dy": 0, "scale": 0.1, "angle": 0})
    assert r.json()["editor"]["scale"] == 0.5
    # EXTRA 에 없는 사진은 거절
    other = main.get_session(sid).slots["SLOT_FRONT"]
    r = client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": other,
                                               "dx": 0, "dy": 0, "scale": 1, "angle": 0})
    assert r.status_code == 400


def test_brightness_is_by_photo_so_extras_just_work(client):
    """밝기는 자리가 아니라 **사진**에 붙는다 — EXTRA 라고 다를 것이 없다."""
    sid, (pid,) = _session(client)
    r = client.post("/api/brightness", json={"session_id": sid, "photo_id": pid, "value": 20})
    assert r.status_code == 200, r.text
    assert r.json()["brightness"] == 20.0
    assert main._photo(main.get_session(sid), pid).brightness == 20.0


def test_plan_names_extras_per_photo_dir_setting(client, monkeypatch):
    sid, (a, b) = _session(client, n_extra=2)
    monkeypatch.setattr(main, "_photo_dir", lambda: "visit")
    p = client.get(f"/api/plan/{sid}").json()
    assert p["extra_dir"] == f"{ORTHO}_A"
    assert [e["file"] for e in p["extras"]] == [
        f"{ORTHO}_A/{ORTHO}_A_extra (1).jpg", f"{ORTHO}_A/{ORTHO}_A_extra (2).jpg"]
    assert [e["pid"] for e in p["extras"]] == [a, b]
    assert p["extras"][0]["editor"] == {"dx": 0.0, "dy": 0.0, "scale": 1.0, "angle": 0.0}
    assert "label" in p["extras"][0]

    monkeypatch.setattr(main, "_photo_dir", lambda: "flat")
    p = client.get(f"/api/plan/{sid}").json()
    assert p["extra_dir"] == ""
    assert [e["file"] for e in p["extras"]] == [
        f"{ORTHO}_A_extra (1).jpg", f"{ORTHO}_A_extra (2).jpg"]


def test_commit_bakes_extras_into_the_same_transaction(client):
    sid, (pid,) = _session(client)
    st = {"dx": 40, "dy": -25, "scale": 1.3, "angle": 6}
    client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid, **st})
    s = main.get_session(sid)
    photo = main._photo(s, pid)
    src = main._imread(photo.path)
    planned = {e["file"] for e in client.get(f"/api/plan/{sid}").json()["extras"]}

    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    files = set(r.json()["files"])
    assert planned <= files, "계획한 추가 작업용 파일이 저장 목록에 없다"
    out = main.ROOT / FOLDER / f"{ORTHO}_A" / f"{ORTHO}_A_extra (1).jpg"
    assert out.exists()

    # 구운 것은 편집기 값 그대로, 반전 없이, 정면 창 크기다
    win = main.SLOT_WINDOWS["SLOT_FRONT"]
    ppcm = main.cfg.geometry.export_px_per_cm
    got = cv2.imread(str(out))
    want = Cr.render_window(src, win, EditorState(40, -25, 1.3, 6), False, ppcm, main.PPC,
                            Cr.hex_to_bgr(main._letterbox_color()))
    assert got.shape == want.shape == (round(win.h * ppcm), round(win.w * ppcm), 3)
    assert np.abs(got.astype(int) - want.astype(int)).mean() < 3.0, "구운 그림이 편집기 값과 다르다"
    # 원본 사본은 없다
    assert not any("_extra" in f and "_raw" in f for f in files)
    # 감사 로그에도 남는다
    log = [json.loads(x) for x in main.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    done = [x for x in log if x.get("event") == "commit"][-1]
    assert done["extras"] == sorted(planned)


def test_face_labelled_extra_uses_portrait_window(client):
    """얼굴로 분류된 사진이 추가 작업용에 오면 창이 얼굴 창(3:4 세로)이다 —
    검수 JSON 의 `extra_windows` 와 구운 파일 크기 둘 다."""
    sid, (pid,) = _session(client)
    s = main.get_session(sid)
    photo = main._photo(s, pid)
    photo.label = "FACE"                       # 분류기가 얼굴로 본 사진
    rev = client.post(f"/api/register/{sid}", json={}).json()["review"]
    fw = rev["extra_windows"][pid]
    assert fw["h"] > fw["w"], "얼굴은 세로 창이어야 한다"
    assert rev["extra_window"]["w"] > rev["extra_window"]["h"], "기본 창(구내)은 그대로 가로"
    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    out = main.ROOT / FOLDER / f"{ORTHO}_A" / f"{ORTHO}_A_extra (1).jpg"
    ppcm = main.cfg.geometry.export_px_per_cm
    got = cv2.imread(str(out))
    assert got.shape[:2] == (round(fw["h"] * ppcm), round(fw["w"] * ppcm)), "구운 파일이 얼굴 창 크기가 아니다"


def test_shape_button_overrides_label_and_resets_editor(client):
    """비율 버튼: 구내 라벨 사진을 세로로 → 창이 3:4, 조정값은 identity 로.
    다시 가로로 → 4:3. 상자에서 빼면 고른 모양도 잊는다."""
    sid, (pid,) = _session(client)
    client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid,
                                           "dx": 30, "dy": 10, "scale": 1.2, "angle": 3})
    r = client.post("/api/extra/shape", json={"session_id": sid, "photo_id": pid, "shape": "portrait"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["window"]["h"] > j["window"]["w"]
    assert j["extra_shapes"][pid] == "portrait"
    assert j["extra_editors"][pid] == {"dx": 0.0, "dy": 0.0, "scale": 1.0, "angle": 0.0}, "창이 바뀌면 조정값은 초기화"
    r = client.post("/api/extra/shape", json={"session_id": sid, "photo_id": pid, "shape": "landscape"})
    assert r.json()["window"]["w"] > r.json()["window"]["h"]
    assert client.post("/api/extra/shape", json={"session_id": sid, "photo_id": pid, "shape": "square"}).status_code == 400
    # 상자에서 빼면 잊는다
    client.post("/api/extra/shape", json={"session_id": sid, "photo_id": pid, "shape": "portrait"})
    _assign(client, sid, pid, None)
    s = main.get_session(sid)
    assert pid not in s.extra_shape


def test_commit_without_extras_creates_no_extra_folder(client):
    sid, _ = _session(client, n_extra=0)
    assert client.get(f"/api/plan/{sid}").json()["extras"] == []
    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    assert not (main.ROOT / FOLDER / f"{ORTHO}_A_extra").exists()
    assert not any("_extra" in f for f in r.json()["files"])
