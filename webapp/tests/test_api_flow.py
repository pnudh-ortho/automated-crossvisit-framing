"""
백엔드 종단 플로우 검증 — fastest_lap.

초진: 세션 → 현재 사진 업로드 → 분류 → (수동 배정) → register → plan → commit.
재진: 기준 사진 풀 + 현재 사진 풀 → 양쪽 분류 → 기준영상 베이크 → 정합(실패 시
프레이밍 폴백) → commit. 충돌: 같은 폴더에 두 번 저장하면 두 번째는 자동 번호.

plan 이 예고한 파일 이름과 commit 이 실제로 남긴 것이 **정확히 같아야** 한다 —
저장 전 검토 화면이 보여주는 것이 plan 이라, 어긋나면 사용자는 실제와 다른 것을
보고 확정하게 된다.

실행: cd webapp && python -m pytest tests/test_api_flow.py -q
"""
import io
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import main as M  # noqa: E402

client = TestClient(M.app)

CLASSES = ["IO_FRONT", "IO_RIGHT", "IO_LEFT", "IO_UPPER", "IO_LOWER"]


def synth(tag, seed):
    rng = np.random.default_rng(seed)
    img = np.full((1200, 1600, 3), 25, np.uint8)
    for _ in range(150):
        c = tuple(int(x) for x in rng.integers(60, 255, 3))
        p = tuple(int(x) for x in rng.integers(0, [1600, 1200], 2))
        cv2.circle(img, p, int(rng.integers(8, 30)), c, -1)
    cv2.putText(img, tag, (450, 620), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def _add(sid, pool, n=5, seed0=100):
    files = [("files", (f"{pool}_{i}.jpg", io.BytesIO(synth(CLASSES[i], seed0 + i)),
                        "image/jpeg")) for i in range(n)]
    r = client.post(f"/api/photos/{sid}?pool={pool}", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def _force_bins(s):
    """합성 이미지는 분류가 흔들린다 — 배치 로직 검증이 목적이므로 직접 꽂는다."""
    for pool_bins, pool in ((s.cur_bins, "cur"), (s.ref_bins, "ref")):
        pool_bins.clear()
    for photo in s.photos:
        idx = int(photo.orig_name.split("_")[-1].split(".")[0])
        slot = M.cfg.slot_by_class[CLASSES[idx]]
        M._put(s, photo, slot, at=0)


def _session(folder):
    r = client.post("/api/session", json={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_first_visit_end_to_end():
    folder = "검사환자"
    d = M.ROOT / folder
    shutil.rmtree(d, ignore_errors=True)
    try:
        sid = _session(folder)
        _add(sid, "cur")
        assert M.SESSIONS[sid].mode == "first"
        client.post(f"/api/classify/{sid}")
        _force_bins(M.SESSIONS[sid])
        r = client.post(f"/api/register/{sid}", json={})
        assert r.status_code == 200, r.text

        pl = client.get(f"/api/plan/{sid}").json()
        assert pl["mode"] == "first"
        assert not pl["missing"], pl["missing"]
        planned = [f["file"] for f in pl["files"]]
        assert planned == [f"검사환자_{c}.jpg" for c in
                           ["IO_FRONT", "IO_RIGHT", "IO_LEFT", "IO_UPPER", "IO_LOWER"]]
        assert all(not f["exists"] for f in pl["files"])

        r = client.post(f"/api/commit/{sid}", json={"overwrite": []})
        assert r.status_code == 200, r.text
        saved = r.json()["files"]
        assert sorted(saved) == sorted(planned), (saved, planned)
        for name in saved:
            assert (d / name).exists(), name
        assert not list(d.glob("*.pptx"))
        assert sid not in M.SESSIONS          # 확정 후 세션은 정리된다
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_collision_gets_numbered_unless_overwritten():
    folder = "충돌검사"
    d = M.ROOT / folder
    shutil.rmtree(d, ignore_errors=True)
    try:
        for round_no in (1, 2):
            sid = _session(folder)
            _add(sid, "cur")
            client.post(f"/api/classify/{sid}")
            _force_bins(M.SESSIONS[sid])
            client.post(f"/api/register/{sid}", json={})
            pl = client.get(f"/api/plan/{sid}").json()
            if round_no == 2:
                # 이미 있는 이름은 기본이 '자동 번호' — 조용한 덮어쓰기는 없다
                assert all(f["exists"] for f in pl["files"])
                assert all(f["action"] == "number" for f in pl["files"])
                assert all(f["file"].endswith("_2.jpg") for f in pl["files"]), pl["files"]
            r = client.post(f"/api/commit/{sid}", json={"overwrite": []})
            assert r.status_code == 200, r.text
        names = sorted(p.name for p in d.glob("*.jpg"))
        assert len(names) == 10, names
        assert "충돌검사_IO_FRONT.jpg" in names
        assert "충돌검사_IO_FRONT_2.jpg" in names

        # 3회차 — 대표 하나를 '덮어쓰기'로 고르면 그 이름 그대로 쓴다
        sid = _session(folder)
        _add(sid, "cur")
        client.post(f"/api/classify/{sid}")
        _force_bins(M.SESSIONS[sid])
        client.post(f"/api/register/{sid}", json={})
        r = client.post(f"/api/commit/{sid}",
                        json={"overwrite": ["충돌검사_IO_FRONT.jpg"]})
        assert r.status_code == 200, r.text
        saved = r.json()["files"]
        assert "충돌검사_IO_FRONT.jpg" in saved
        assert sum(1 for n in saved if n.startswith("충돌검사_IO_FRONT")) == 1
        assert len(list(d.glob("충돌검사_IO_FRONT*.jpg"))) == 2   # 원본+_2 (덮임)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_revisit_reference_bake_and_fallback():
    """재진: 기준 풀이 있으면 mode=revisit, 기준영상이 구워지고, 정합이 실패해도
    프레이밍 폴백 + '수동' 배지로 검수까지는 반드시 간다."""
    folder = "재진검사"
    d = M.ROOT / folder
    shutil.rmtree(d, ignore_errors=True)
    try:
        sid = _session(folder)
        _add(sid, "ref", seed0=100)
        _add(sid, "cur", seed0=200)
        s = M.SESSIONS[sid]
        assert s.mode == "revisit"
        client.post(f"/api/classify/{sid}")
        _force_bins(s)
        assert len(s.ref_slots) == 5 and len(s.slots) == 5

        r = client.post(f"/api/register/{sid}", json={})
        assert r.status_code == 200, r.text
        # 기준영상이 창 크기(PPC)로 구워졌다
        for slot in M.SLOT_NAMES:
            img = s.references.get(slot)
            assert img is not None, slot
            win = M.SLOT_WINDOWS[slot]
            assert img.shape[1] == round(win.w * M.PPC), (slot, img.shape)
            assert img.shape[0] == round(win.h * M.PPC), (slot, img.shape)
        # 합성 이미지라 치아 정합은 실패한다 — 그래도 배지 달고 진행돼야 한다
        rv = r.json()["review"]
        for slot in M.SLOT_NAMES:
            ph = rv["slots"][slot]
            assert ph is not None, slot
            assert ph["badge"] in ("ok", "manual", "low"), ph
        # 겹쳐보기 이미지가 서빙된다
        assert client.get(f"/api/reference/{sid}/SLOT_FRONT").status_code == 200

        r = client.post(f"/api/commit/{sid}", json={"overwrite": []})
        assert r.status_code == 200, r.text
        # 저장은 **현재 사진만** — 5장이어야 한다 (기준 5장은 저장 안 됨)
        assert len(r.json()["files"]) == 5, r.json()["files"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
