"""Fastest Lap 의 '추가 작업용' — 본편의 트레이를 그대로 옮긴 것.

여기서 못박는 것:

  · `/api/fl/assign` 의 slot="EXTRA" 는 오늘(cur) 사진만 받는다 — 기준 사진은 400
  · EXTRA 로 가면 상자에서 빠지고 반전이 풀리며, 다시 상자·OTHERS 로 가면 EXTRA 와
    거기서 잡은 구도가 함께 버려진다
  · 검수 JSON 에 본편과 같은 `extra`·`extra_editors`·`extra_window` 가 실린다
  · `/api/extra/adjust`·`/api/brightness` 는 fast 세션에서도 그대로 돈다
  · 저장 계획: 환자 모드 `교정번호_차수/교정번호_차수_extra (n).jpg`(flat 이면 폴더
    없이), 폴더 모드 `접두어_extra (n).jpg` — 겹치면 [자동 번호 | 덮어쓰기]
  · 확정: 편집기 값대로 반전 없이 굽고, 감사 기록·응답 `files` 에 남는다

실행: cd webapp && python -m pytest tests/test_fastlap_extra.py -q
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import crop as Cr                                                # noqa: E402
import fastlap as FL                                             # noqa: E402
import main as M                                                 # noqa: E402
from coords import EditorState                                   # noqa: E402
from test_fastlap import _folder_session, _photo, _ready, _session  # noqa: E402

client = TestClient(M.app)
IDENT = {"dx": 0.0, "dy": 0.0, "scale": 1.0, "angle": 0.0}


def _assign(sid, pid, slot, at=None, ok=True):
    r = client.post("/api/fl/assign", json={"session_id": sid, "photo_id": pid,
                                            "slot": slot, "at": at})
    assert (r.status_code == 200) is ok, r.text
    return r.json()["review"] if ok else r


def _with_extras(s, n=1, seed=700):
    """다섯 자리를 채우고 오늘 사진 `n` 장을 EXTRA 로 보낸 세션."""
    _ready(s)
    pids = []
    for i in range(n):
        p = _photo(s, seed + i, "cur")
        _assign(s.id, p.id, "EXTRA")
        pids.append(p.id)
    return pids


# ── 넣고 빼기 ────────────────────────────────────────────────────────────────
def test_오늘_사진을_EXTRA_로_보내면_상자에서_빠지고_반전은_그대로다():
    s = _ready(_session())
    p = _photo(s, 600, "cur")                 # 라벨 IO_UPPER — cur 기본값은 뒤집힘
    FL._put(s, p, "SLOT_UPPER", at=1)
    assert p.flip_v is True
    rev = _assign(s.id, p.id, "EXTRA")
    assert s.extra == [p.id] and p.slot == "EXTRA"
    assert all(p.id not in lst for lst in s.bins.values())
    assert p.flip_v is True, "추가 작업용은 들어올 때 상태를 그대로 둔다 — 사람이 ↕ 로 정한다"
    assert rev["extra"] == [p.id]
    # OTHERS 목록에도 없다 — 자리를 받은 사진이다
    assert p.id not in [x["id"] for x in rev["others"]["cur"]]
    # 다섯 자리는 그대로다
    assert rev["missing"] == []


def test_EXTRA_에서_나가면_구도도_버린다():
    s = _session()
    (pid,) = _with_extras(s)
    r = client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": pid,
                                               "dx": 3, "dy": 4, "scale": 1.1, "angle": 2})
    assert r.status_code == 200, r.text
    assert pid in s.extra_editors
    rev = _assign(s.id, pid, "SLOT_FRONT", at=1)
    assert rev["extra"] == [] and rev["extra_editors"] == {}
    assert pid not in s.extra_editors and pid in s.bins["SLOT_FRONT"]
    # 아예 빼도(OTHERS) 같다
    _assign(s.id, pid, "EXTRA")
    client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": pid,
                                           "dx": 1, "dy": 1, "scale": 1, "angle": 1})
    rev = _assign(s.id, pid, None)
    assert rev["extra"] == [] and s.extra_editors == {}
    assert M._photo(s, pid).slot is None
    assert pid in [x["id"] for x in rev["others"]["cur"]]


def test_같은_곳_안에서_순서만_바꾸면_구도는_남는다():
    s = _session()
    a, b = _with_extras(s, n=2)
    client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": b,
                                           "dx": 3, "dy": 4, "scale": 1.1, "angle": 2})
    rev = _assign(s.id, b, "EXTRA", at=0)
    assert rev["extra"] == [b, a]
    assert rev["extra_editors"][b]["angle"] == 2.0


def test_기준_사진은_EXTRA_로_못_보낸다():
    s = _ready(_session())
    ref = _photo(s, 610, "ref")
    FL._put(s, ref, "SLOT_UPPER")
    r = _assign(s.id, ref.id, "EXTRA", ok=False)
    assert r.status_code == 400
    assert s.extra == [] and s.ref_bins["SLOT_UPPER"] == [ref.id]
    assert ref.slot == "SLOT_UPPER"


def test_사진을_비우면_EXTRA_도_비운다():
    s = _session()
    (pid,) = _with_extras(s)
    client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": pid,
                                           "dx": 1, "dy": 1, "scale": 1, "angle": 1})
    r = client.delete(f"/api/fl/photos/{s.id}/{pid}")
    assert r.status_code == 200, r.text
    assert s.extra == [] and s.extra_editors == {}
    (pid,) = _with_extras(s, seed=720)
    r = client.delete(f"/api/fl/photos/{s.id}", params={"pool": "cur"})
    assert r.status_code == 200, r.text
    assert s.extra == [] and s.extra_editors == {}
    assert r.json()["review"]["extra"] == []


# ── 검수 JSON · 편집 ─────────────────────────────────────────────────────────
def test_검수_JSON_은_본편과_같은_열쇠를_싣는다():
    s = _session()
    (pid,) = _with_extras(s)
    rev = client.get(f"/api/fl/review/{s.id}").json()["review"]
    assert rev["extra"] == [pid]
    assert rev["extra_editors"] == {pid: IDENT}
    win = M.SLOT_WINDOWS["SLOT_FRONT"]
    assert rev["extra_window"] == {"w": win.w, "h": win.h}
    # 본편의 검수 JSON 과 한 조각 — 열쇠가 갈라지면 화면이 두 벌 필요해진다
    assert {k: rev[k] for k in ("extra", "extra_editors", "extra_window", "extra_windows", "extra_shapes")} \
        == M._extra_json(s)


def test_구도와_밝기는_fast_세션에서도_그대로_돈다():
    s = _session()
    (pid,) = _with_extras(s)
    r = client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": pid,
                                               "dx": -900, "dy": 700, "scale": 9, "angle": 200})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "editor": {"dx": -900.0, "dy": 700.0,
                                               "scale": 2.0, "angle": 200.0}}
    # EXTRA 에 없는 사진은 거절 — 본편과 같다
    other = s.slots["SLOT_FRONT"]
    assert client.post("/api/extra/adjust", json={"session_id": s.id, "photo_id": other,
                                                  "dx": 0, "dy": 0, "scale": 1, "angle": 0}
                       ).status_code == 400
    r = client.post("/api/brightness", json={"session_id": s.id, "photo_id": pid, "value": 20})
    assert r.status_code == 200, r.text
    assert M._photo(s, pid).brightness == 20.0


# ── 저장 계획 ────────────────────────────────────────────────────────────────
def _extra_entries(pl):
    return [f for f in pl["files"] if f["kind"] == "extra_work"]


def test_환자_모드_이름은_본편_그대로다(monkeypatch):
    s = _session()
    a, b = _with_extras(s, n=2)
    monkeypatch.setattr(M, "_photo_dir", lambda: "visit")
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["extra_dir"] == "54321_A"
    got = _extra_entries(pl)
    assert [e["file"] for e in got] == [
        "54321_A/54321_A_extra (1).jpg", "54321_A/54321_A_extra (2).jpg"]
    assert [e["pid"] for e in got] == [a, b]
    assert pl["extras"] == got, "본편처럼 위쪽 `extras` 로도 같은 항목을 준다"
    e = got[0]
    assert e["slot"] == "EXTRA" and e["extra"] is False and e["raw"] is None
    assert e["base"] == e["file"] and e["exists"] is False and e["action"] == "new"
    assert e["editor"] == IDENT and "label" in e
    # 슬롯 사진은 `kind` 만 붙고 나머지는 그대로다
    assert all(f["kind"] == "photo" for f in pl["files"] if f["slot"] != "EXTRA")
    assert len(pl["files"]) == 7

    monkeypatch.setattr(M, "_photo_dir", lambda: "flat")
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["extra_dir"] == ""
    assert [e["file"] for e in _extra_entries(pl)] == [
        "54321_A_extra (1).jpg", "54321_A_extra (2).jpg"]


def test_폴더_모드_이름은_접두어_extra_다():
    body, s = _folder_session("추가작업", prefix="PT07")
    a, b = _with_extras(s, n=2)
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["folder_mode"] is True and pl["extra_dir"] == ""
    assert [e["file"] for e in _extra_entries(pl)] == [
        "PT07_extra (1).jpg", "PT07_extra (2).jpg"]
    # 접두어를 비우면 폴더 이름이 그 자리에 온다
    body, s2 = _folder_session("비운접두어")
    _with_extras(s2, n=1, seed=740)
    pl = client.get(f"/api/fl/plan/{s2.id}").json()
    assert [e["file"] for e in _extra_entries(pl)] == ["비운접두어_extra (1).jpg"]


def test_이미_있는_이름은_사람에게_묻는다():
    s = _session()
    a, b = _with_extras(s, n=2)
    first = _extra_entries(client.get(f"/api/fl/plan/{s.id}").json())[0]
    dst = s.patient_dir / first["file"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"x")

    got = _extra_entries(client.get(f"/api/fl/plan/{s.id}").json())
    assert got[0]["exists"] is True and got[0]["action"] == "number"
    # 뒷장의 번호를 가로채지 않는다 — 트레이 전체 뒤의 첫 빈 번호로 간다
    assert got[0]["file"].endswith("_extra (3).jpg"), got[0]["file"]
    assert got[1]["file"].endswith("_extra (2).jpg") and got[1]["action"] == "new"

    picked = _extra_entries(client.get(f"/api/fl/plan/{s.id}",
                                       params={"overwrite": first["base"]}).json())[0]
    assert picked["action"] == "overwrite" and picked["file"] == picked["base"]


# ── 확정 ─────────────────────────────────────────────────────────────────────
def _bake_check(out, src, st):
    """구운 것은 편집기 값 그대로, 반전 없이, 정면 창 크기다."""
    win = M.SLOT_WINDOWS["SLOT_FRONT"]
    ppcm = M.cfg.geometry.export_px_per_cm
    got = cv2.imread(str(out))
    want = Cr.render_window(src, win, st, False, ppcm, M.PPC,
                            Cr.hex_to_bgr(M._letterbox_color()))
    assert got.shape == want.shape == (round(win.h * ppcm), round(win.w * ppcm), 3)
    assert np.abs(got.astype(int) - want.astype(int)).mean() < 3.0, "구운 그림이 편집기 값과 다르다"


def test_확정하면_편집기_값대로_굽고_기록에_남는다():
    s = _session()
    (pid,) = _with_extras(s)
    sid, pdir = s.id, s.patient_dir
    client.post("/api/extra/adjust", json={"session_id": sid, "photo_id": pid,
                                           "dx": 40, "dy": -25, "scale": 1.3, "angle": 6})
    src = M._imread(M._photo(s, pid).path)
    planned = [e["file"] for e in client.get(f"/api/fl/plan/{sid}").json()["extras"]]

    r = client.post(f"/api/fl/commit/{sid}")
    assert r.status_code == 200, r.text
    files = r.json()["files"]
    assert len(files) == 6 and set(planned) <= set(files), files
    out = pdir / planned[0]
    assert out.exists() and out.name == "54321_A_extra (1).jpg"
    _bake_check(out, src, EditorState(40, -25, 1.3, 6))
    assert not any("_extra" in f and "raw" in f for f in files), "원본 사본은 없다"
    log = [json.loads(x) for x in M.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    done = [x for x in log if x.get("event") == "commit"][-1]
    assert done["fast"] is True and done["extras"] == planned


def test_폴더_모드로_확정해도_같다():
    body, s = _folder_session("추가확정", prefix="PT09")
    (pid,) = _with_extras(s, seed=760)
    r = client.post(f"/api/fl/commit/{s.id}", json={"overwrite": []})
    assert r.status_code == 200, r.text
    assert "PT09_extra (1).jpg" in r.json()["files"]
    dest = M.ROOT / "추가확정"
    written = sorted(q.name for q in dest.rglob("*") if q.is_file())
    assert written == [f"PT09 ({i}).jpg" for i in range(1, 6)] + ["PT09_extra (1).jpg"], written


def test_트레이가_비면_아무것도_바뀌지_않는다():
    s = _ready(_session())
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["extras"] == [] and len(pl["files"]) == 5
    r = client.post(f"/api/fl/commit/{s.id}")
    assert r.status_code == 200, r.text
    assert len(r.json()["files"]) == 5
    assert not any("_extra" in f for f in r.json()["files"])
