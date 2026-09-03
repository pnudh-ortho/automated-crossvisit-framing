"""
Fastest Lap — 환자 폴더에 PPT 없이 사진만 남기는 진행 방식.

여기서 지키는 약속은 넷이다.
  ① fast 세션과 본편 세션이 **같은 코드로 갈라진다** — 본편 반전 규칙이 안 바뀐다
  ② 두 드롭존(기준·오늘)의 사진이 섞이지 않는다
  ③ 기준영상은 창 크기 그대로 구워진다 (겹쳐보기·정합이 같은 좌표계를 본다)
  ④ 사람이 ↕ 로 고른 반전은 분류가 다시 돌아도 덮이지 않는다
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading

from pathlib import Path

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import fastlap as FL  # noqa: E402
import main as M  # noqa: E402
import naming as N  # noqa: E402

client = TestClient(M.app)


def _jpg(seed: int, w: int = 1600, h: int = 1200) -> bytes:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 25, np.uint8)
    for _ in range(120):
        c = tuple(int(x) for x in rng.integers(60, 255, 3))
        p = tuple(int(x) for x in rng.integers(0, [w, h], 2))
        cv2.circle(img, p, int(rng.integers(8, 30)), c, -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _session(fast: bool = True):
    """세션 하나를 손으로 세운다 — 분류기 결과에 기대지 않으려는 것이다."""
    ids = N.Identifiers("검사환자", "", "54321")
    s = M.Session("first", ids, "A")
    s.fast = fast
    s.patient_dir = M.ROOT / "검사환자(54321)"
    s.patient_dir.mkdir(parents=True, exist_ok=True)
    M.SESSIONS[s.id] = s
    return s


def _photo(s, seed: int, pool: str = "cur"):
    data = _jpg(seed)
    pid = f"p{seed}"
    dst = s.tmp / f"{pid}.jpg"
    dst.write_bytes(data)
    p = M.Photo(pid, dst, 1600, 1200)
    p.pool = pool
    p.label = "IO_UPPER"
    p.confidence = 0.99
    s.photos.append(p)
    return p


# ── ① 모드가 갈리는 자리 ──────────────────────────────────────────────────────
def test_본편_반전규칙은_그대로다():
    """`_sync_flip` 에 fast 분기를 넣었다 — 본편 동작이 흔들리면 안 된다."""
    s = _session(fast=False)
    p = _photo(s, 1)
    M._put(s, p, "SLOT_UPPER")
    assert p.flip_v is True, "교합면 슬롯은 자리 규칙으로 뒤집힌다"
    M._put(s, p, "SLOT_FRONT")
    assert p.flip_v is False, "정면으로 옮기면 되돌아온다"


def test_fast는_자리가_아니라_카테고리로_뒤집는다(tmp_path):
    s = _session(fast=True)
    p = _photo(s, 2, pool="cur")
    FL._put(s, p, "SLOT_UPPER")
    # 기본 그리드는 cur/IO_UPPER 가 참이다 (FLIP_DEFAULTS)
    assert p.flip_v is True
    # 같은 사진을 기준 풀에 두면 기본값이 다르다 — 기준은 이미 뒤집힌 완성본일
    # 수 있어서 사람이 정하게 비워 둔다.
    p.pool = "ref"
    p.flip_v = False
    FL._put(s, p, "SLOT_UPPER")
    assert p.flip_v is False


def test_세션은_fast_플래그를_들고_열린다():
    r = client.post("/api/session", json={"name": "빠른이", "ortho_id": "54399",
                                          "fast": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast"] is True
    assert client.post("/api/session", json={"name": "느린이", "ortho_id": "54398"}
                       ).json()["fast"] is False


# ── ② 두 풀 ──────────────────────────────────────────────────────────────────
def test_두_풀은_섞이지_않는다():
    s = _session()
    ref, cur = _photo(s, 3, "ref"), _photo(s, 4, "cur")
    FL._put(s, ref, "SLOT_UPPER")
    FL._put(s, cur, "SLOT_UPPER")
    assert s.ref_bins["SLOT_UPPER"] == [ref.id]
    assert s.bins["SLOT_UPPER"] == [cur.id], "오늘 사진은 본편과 같은 상자에 든다"
    assert FL._ref_slots(s) == {"SLOT_UPPER": ref.id}
    assert s.slots == {"SLOT_UPPER": cur.id}


def test_업로드는_풀을_따라간다():
    s = _session()
    r = client.post(f"/api/fl/photos/{s.id}?pool=ref",
                    files=[("files", ("a.jpg", io.BytesIO(_jpg(5)), "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert all(p.pool == "ref" for p in s.photos)
    assert client.post(f"/api/fl/photos/{s.id}?pool=nope",
                       files=[("files", ("a.jpg", io.BytesIO(_jpg(6)), "image/jpeg"))]
                       ).status_code == 400


def test_본편_세션은_fl_경로를_거절한다():
    s = _session(fast=False)
    assert client.get(f"/api/fl/review/{s.id}").status_code == 400


# ── ③ 기준영상 ───────────────────────────────────────────────────────────────
def test_기준영상은_창_크기로_구워진다():
    s = _session()
    ref = _photo(s, 7, "ref")
    FL._put(s, ref, "SLOT_UPPER")
    img = FL._ref_bake(s, "SLOT_UPPER")
    win = s.slot_windows["SLOT_UPPER"]
    assert img is not None
    assert img.shape[:2] == (round(win.h * M.PPC), round(win.w * M.PPC))
    # 같은 근거면 다시 굽지 않는다
    assert FL._ref_bake(s, "SLOT_UPPER") is img


def test_기준_사진을_빼면_기준영상도_사라진다():
    s = _session()
    ref = _photo(s, 8, "ref")
    FL._put(s, ref, "SLOT_UPPER")
    FL._ref_bake(s, "SLOT_UPPER")
    assert "SLOT_UPPER" in s.references
    FL._detach(s, ref)
    assert FL._ref_bake(s, "SLOT_UPPER") is None
    assert "SLOT_UPPER" not in s.references


def test_겹쳐보기는_fast_모드에서도_돈다():
    """본편의 겹쳐보기 엔드포인트를 그대로 쓴다 — 열쇠가 차수 글자가 아닌데도."""
    s = _session()
    ref = _photo(s, 9, "ref")
    FL._put(s, ref, "SLOT_UPPER")
    FL._ref_bake(s, "SLOT_UPPER")
    r = client.get(f"/api/reference/{s.id}/SLOT_UPPER")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert client.get(f"/api/references/{s.id}").json() == {"SLOT_UPPER": [FL.REF_KEY]}


# ── ④ 사람이 고른 반전 ───────────────────────────────────────────────────────
def test_사람이_뒤집으면_다시_분류해도_그대로다():
    s = _session()
    p = _photo(s, 10, "cur")
    FL._put(s, p, "SLOT_UPPER")
    assert p.flip_v is True
    r = client.post("/api/fl/flip", json={"session_id": s.id, "photo_id": p.id,
                                          "on": False})
    assert r.status_code == 200, r.text
    assert p.flip_v is False and p.flip_user is True
    # 자리를 옮겼다 되돌려도 사람이 고른 값이 이긴다
    FL._put(s, p, "SLOT_FRONT")
    FL._put(s, p, "SLOT_UPPER")
    assert p.flip_v is False


def test_반전을_바꾸면_계산_기록이_지워진다():
    s = _session()
    ref, cur = _photo(s, 11, "ref"), _photo(s, 12, "cur")
    FL._put(s, ref, "SLOT_UPPER")
    FL._put(s, cur, "SLOT_UPPER")
    FL._ref_bake(s, "SLOT_UPPER")
    s.framed["SLOT_UPPER"] = (cur.id, cur.flip_v)
    client.post("/api/fl/flip", json={"session_id": s.id, "photo_id": ref.id,
                                      "on": not ref.flip_v})
    assert "SLOT_UPPER" not in s.ref_src, "기준을 뒤집었으니 다시 구워야 한다"
    assert "SLOT_UPPER" not in s.references


# ── 정합 한 바퀴 ─────────────────────────────────────────────────────────────
def test_정합_한_바퀴가_끝까지_돈다():
    """짝이 맞든 안 맞든 **검수 진입을 막지 않는다** — 실패하면 프레이밍이 받는다."""
    s = _session()
    ref, cur = _photo(s, 13, "ref"), _photo(s, 14, "cur")
    FL._put(s, ref, "SLOT_UPPER")
    FL._put(s, cur, "SLOT_UPPER")
    done = FL._register(s)
    assert done == ["SLOT_UPPER"]
    assert s.progress["SLOT_UPPER"] in ("reg", "fallback")
    assert s.framed["SLOT_UPPER"] == (cur.id, cur.flip_v)
    # 다시 불러도 싸다 — 근거가 그대로면 건너뛴다
    assert FL._register(s) == []


def test_기준이_없으면_초진처럼_간다():
    s = _session()
    cur = _photo(s, 15, "cur")
    FL._put(s, cur, "SLOT_UPPER")
    assert FL._register(s) == ["SLOT_UPPER"]
    assert s.progress["SLOT_UPPER"] == "frame", "짝이 없으면 프레이밍 모델이 잡는다"
    assert cur.badge != "manual", "기준이 애초에 없었으니 '수동' 배지는 붙지 않는다"


# ── 앱에 제대로 붙었나 ───────────────────────────────────────────────────────
def test_라우터가_한_벌만_붙는다():
    """`app.routes` 로는 확인할 수 없다 — 이 FastAPI 는 포함된 라우터를 감싼다.

    그래서 실제로 문서에 나오는 경로를 본다. 재임포트해도 겹치지 않아야 한다.
    """
    import importlib

    paths = client.get("/openapi.json").json()["paths"]
    fl = sorted(p for p in paths if p.startswith("/api/fl"))
    # 개수가 아니라 **목록**을 적어 둔다 — 경로가 늘 때 숫자만 고치면 무엇이
    # 늘었는지 아무도 안 본다. 여기 한 줄이 이 모드의 API 전부다.
    assert fl == [
        "/api/fl/adjust", "/api/fl/assign", "/api/fl/classify/{sid}",
        "/api/fl/commit/{sid}", "/api/fl/flip", "/api/fl/naming",
        "/api/fl/open", "/api/fl/photos/{sid}", "/api/fl/photos/{sid}/{pid}",
        "/api/fl/plan/{sid}",
        "/api/fl/prefs", "/api/fl/register/{sid}", "/api/fl/register/{sid}/status",
        "/api/fl/review/{sid}", "/api/fl/session", "/api/fl/session/{sid}/names",
        "/api/fl/sort",
    ], fl
    importlib.reload(FL)
    again = sorted(p for p in client.get("/openapi.json").json()["paths"]
                   if p.startswith("/api/fl"))
    assert again == fl, "재임포트가 라우트를 겹쳐 쌓았다"


# ── 저장 ─────────────────────────────────────────────────────────────────────
def _ready(s):
    """다섯 슬롯을 채운 세션 — 저장 검토가 돌 수 있는 최소 상태."""
    for i, slot in enumerate(M.cfg.ppt.slot_names):
        p = _photo(s, 200 + i, "cur")
        FL._put(s, p, slot)
    return s


def test_저장_이름은_본편_규칙_그대로다():
    s = _ready(_session())
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["writes_ppt"] is False, "이 모드는 덱을 만들지도 고치지도 않는다"
    assert "ppt" not in pl
    # 교합면(SLOT_UPPER)은 파일명 순번 4 — 본편과 같은 표를 쓴다
    upper = next(f for f in pl["files"] if f["slot"] == "SLOT_UPPER")
    assert upper["file"].endswith("54321_A (4).jpg"), upper["file"]
    assert all(f["action"] == "new" for f in pl["files"])


def test_이미_있는_이름은_사람에게_묻는다():
    s = _ready(_session())
    first = client.get(f"/api/fl/plan/{s.id}").json()["files"][0]
    # 그 이름을 미리 만들어 둔다 — 같은 차수를 한 번 더 돌린 상황이다
    dst = s.patient_dir / first["file"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"x")

    again = client.get(f"/api/fl/plan/{s.id}").json()["files"][0]
    assert again["exists"] is True
    assert again["action"] == "number"
    assert again["file"] != again["base"], "덮어쓰지 않고 다음 번호를 받는다"

    picked = client.get(f"/api/fl/plan/{s.id}",
                        params={"overwrite": again["base"]}).json()["files"][0]
    assert picked["action"] == "overwrite"
    assert picked["file"] == picked["base"]


def test_확정하면_사진만_남는다():
    s = _ready(_session())
    sid, pdir = s.id, s.patient_dir
    r = client.post(f"/api/fl/commit/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["files"]) == 5
    written = sorted(q.name for q in pdir.rglob("*") if q.is_file())
    assert len(written) == 5, written
    assert not list(pdir.rglob("*.pptx")), "덱을 만들면 안 된다"
    # 구운 사진은 창 크기 그대로다
    img = cv2.imread(str(next(q for q in pdir.rglob("*.jpg"))))
    win = M.SLOT_WINDOWS[M.cfg.ppt.slot_names[0]]
    ppcm = M.cfg.geometry.export_px_per_cm
    assert img.shape[:2] == (round(win.h * ppcm), round(win.w * ppcm))
    assert client.get(f"/api/fl/review/{sid}").status_code == 410, "세션은 정리된다"


def test_슬롯이_비면_확정을_막는다():
    s = _session()
    FL._put(s, _photo(s, 300, "cur"), "SLOT_UPPER")
    r = client.post(f"/api/fl/commit/{s.id}")
    assert r.status_code == 409
    assert set(r.json()["missing"]) == set(M.cfg.ppt.slot_names) - {"SLOT_UPPER"}
    assert client.post(f"/api/fl/commit/{s.id}",
                       params={"allow_missing": True}).status_code == 200


# ── 화면이 실제로 도는 순서 그대로 ───────────────────────────────────────────
def test_화면_흐름_한_바퀴():
    """환자 열기 → 좌우 드롭 → 분류 → 정합 → 저장 검토 → 확정.

    프론트(fast.js)가 이 순서로 부르고 이 열쇠들을 읽는다. 여기서 깨지면
    화면에서는 조용히 빈 칸으로 보인다.
    """
    r = client.post("/api/session", json={"name": "흐름", "ortho_id": "54322",
                                          "fast": True}).json()
    sid = r["session_id"]
    assert r["fast"] is True

    # 좌우 두 드롭존
    for pool, seed in (("ref", 400), ("cur", 410)):
        up = client.post(f"/api/fl/photos/{sid}?pool={pool}",
                         files=[("files", (f"{pool}.jpg", io.BytesIO(_jpg(seed)),
                                           "image/jpeg"))])
        assert up.status_code == 200, up.text
    assert {p["pool"] for p in up.json()["photos"]} == {"ref", "cur"}

    rv = client.post(f"/api/fl/classify/{sid}").json()["review"]
    # fast.js 가 읽는 열쇠들
    for key in ("bins", "ref_bins", "others", "slots", "face", "has_ref",
                "progress", "missing"):
        assert key in rv, key
    assert set(rv["others"]) == {"ref", "cur"}

    # 다섯 자리를 손으로 채운다 — 분류기 결과에 기대지 않는다
    s = M.SESSIONS[sid]
    for i, slot in enumerate(M.cfg.ppt.slot_names):
        FL._put(s, _photo(s, 420 + i, "cur"), slot)
    FL._put(s, M._photo(s, [p.id for p in s.photos if p.pool == "ref"][0]),
            M.cfg.ppt.slot_names[0])

    reg = client.post(f"/api/fl/register/{sid}")
    assert reg.status_code == 200, reg.text
    assert reg.json()["review"]["has_ref"] is True
    st = client.get(f"/api/fl/register/{sid}/status").json()
    assert st["busy"] is False and st["progress"]

    # 검수 화면의 겹쳐보기는 본편 엔드포인트를 그대로 쓴다
    assert client.get(f"/api/reference/{sid}/{M.cfg.ppt.slot_names[0]}").status_code == 200
    # 구내 다섯 자리의 구도 조정도 본편 것을 그대로 쓴다
    adj = client.post("/api/adjust", json={"session_id": sid,
                                           "slot": M.cfg.ppt.slot_names[0],
                                           "dx": 3, "dy": 0, "scale": 1.0, "angle": 0})
    assert adj.status_code == 200, adj.text

    pl = client.get(f"/api/fl/plan/{sid}").json()
    assert pl["writes_ppt"] is False and len(pl["files"]) == 5
    ok = client.post(f"/api/fl/commit/{sid}",
                     json={"overwrite": []})
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["files"]) == 5


# ── 환자 없이 진행하기 ───────────────────────────────────────────────────────
def _folder_session(folder="김하늘_260831", prefix=""):
    r = client.post("/api/fl/session", json={"folder": folder, "prefix": prefix})
    assert r.status_code == 200, r.text
    return r.json(), M.SESSIONS[r.json()["session_id"]]


def test_폴더_세션은_환자도_차수도_없다():
    body, s = _folder_session()
    assert body["fast"] is True and body["folder_mode"] if "folder_mode" in body else True
    assert s.ids is None and s.visit == "" and s.patient_dir is None
    assert FL._folder_mode(s) is True
    assert FL._dest(s) == M.ROOT / "김하늘_260831"
    # 화면(startSession)이 읽는 자리 — 이름표에 폴더 이름이 뜬다
    assert body["ids"]["name"] == "김하늘_260831"
    assert body["prefix"] == "김하늘_260831", "접두어를 비우면 폴더 이름을 쓴다"


def test_쓸_수_없는_폴더_이름은_거절한다():
    for bad in ("", "   ", "a/b", 'x"y', "c:d"):
        assert client.post("/api/fl/session", json={"folder": bad}).status_code == 400
    assert client.post("/api/fl/session",
                       json={"folder": "정상", "prefix": "a|b"}).status_code == 400


def test_폴더_모드_이름은_접두어_별칭_규칙이다():
    body, s = _folder_session("환자A", prefix="PT01")
    for slot in M.cfg.ppt.slot_names:
        FL._put(s, _photo(s, 500 + M.cfg.ppt.slot_names.index(slot), "cur"), slot)
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["folder_mode"] is True
    assert pl["prefix"] == "PT01"
    names = sorted(f["file"] for f in pl["files"])
    # 기본 별칭은 **본편이 붙이는 번호 그대로**다 — 구내 (1)~(5)
    assert names == [f"PT01 ({i}).jpg" for i in range(1, 6)], names


def test_폴더_모드로_확정하면_저장_루트_아래에_생긴다():
    body, s = _folder_session("저장검사")
    for slot in M.cfg.ppt.slot_names:
        FL._put(s, _photo(s, 520 + M.cfg.ppt.slot_names.index(slot), "cur"), slot)
    r = client.post(f"/api/fl/commit/{s.id}", json={"overwrite": []})
    assert r.status_code == 200, r.text
    assert r.json()["folder_mode"] is True
    dest = M.ROOT / "저장검사"
    written = sorted(q.name for q in dest.rglob("*") if q.is_file())
    assert len(written) == 5, written
    assert written == [f"저장검사 ({i}).jpg" for i in range(1, 6)], written
    assert not list(dest.rglob("*.pptx"))


def test_폴더_모드도_겹치면_사람에게_묻는다():
    body, s = _folder_session("겹침검사")
    for slot in M.cfg.ppt.slot_names:
        FL._put(s, _photo(s, 540 + M.cfg.ppt.slot_names.index(slot), "cur"), slot)
    first = client.get(f"/api/fl/plan/{s.id}").json()["files"][0]
    dest = M.ROOT / "겹침검사"; dest.mkdir(parents=True, exist_ok=True)
    (dest / first["file"]).write_bytes(b"x")

    again = client.get(f"/api/fl/plan/{s.id}").json()["files"][0]
    assert again["exists"] is True and again["action"] == "number"
    assert again["file"] != again["base"]
    picked = client.get(f"/api/fl/plan/{s.id}",
                        params={"overwrite": again["base"]}).json()["files"][0]
    assert picked["action"] == "overwrite" and picked["file"] == picked["base"]


def test_환자_모드는_여전히_본편_이름을_쓴다():
    """두 길이 섞이면 한 폴더에 이름 체계가 둘이 된다 — 그걸 막는 자리."""
    s = _ready(_session())
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["folder_mode"] is False
    assert all("_IO_" not in f["file"] for f in pl["files"]), pl["files"]
    assert any(f["file"].endswith("54321_A (4).jpg") for f in pl["files"])


def test_탐색기로_고른_절대경로도_받는다(tmp_path):
    """저장 루트 밖(외장 드라이브·바탕화면 등)을 고를 수 있어야 한다.

    이 모드는 장부를 만들지 않으므로 루트 안에 가둘 이유가 없다.
    """
    out = tmp_path / "외장" / "김하늘_260831"
    out.parent.mkdir(parents=True)
    body, s = _folder_session(str(out))
    assert FL._dest(s) == out
    # 긴 경로 대신 마지막 칸만 이름·접두어로 쓴다
    assert body["ids"]["name"] == "김하늘_260831"
    assert body["prefix"] == "김하늘_260831"
    assert body["dir"] == str(out)

    for slot in M.cfg.ppt.slot_names:
        FL._put(s, _photo(s, 560 + M.cfg.ppt.slot_names.index(slot), "cur"), slot)
    r = client.post(f"/api/fl/commit/{s.id}", json={"overwrite": []})
    assert r.status_code == 200, r.text
    written = sorted(q.name for q in out.rglob("*") if q.is_file())
    assert written == [f"김하늘_260831 ({i}).jpg" for i in range(1, 6)], written


def test_없는_상위폴더는_거절한다(tmp_path):
    assert client.post("/api/fl/session",
                       json={"folder": str(tmp_path / "없는곳" / "그아래")}
                       ).status_code == 400


def test_폴더_이름은_사진을_넣은_뒤에도_고칠_수_있다():
    body, s = _folder_session("오타난이름")
    FL._put(s, _photo(s, 580, "cur"), "SLOT_UPPER")
    r = client.post(f"/api/fl/session/{s.id}/names",
                    json={"folder": "고친이름", "prefix": "PT"})
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == "고친이름" and r.json()["prefix"] == "PT"
    assert FL._dest(s) == M.ROOT / "고친이름"
    assert client.get(f"/api/fl/plan/{s.id}").json()["files"][0]["file"] == "PT (4).jpg"
    # 환자 폴더에 저장하는 세션에는 쓸 수 없다
    ps = _ready(_session())
    assert client.post(f"/api/fl/session/{ps.id}/names",
                       json={"folder": "아무거나"}).status_code == 400


def test_fast_세션에는_차수_노트가_없다():
    """노트는 덱에 적는 글이다. 환자 없이 연 세션은 환자 정보조차 없어서,
    막지 않으면 미리보기를 만들다 500 이 난다 — 실제로 그랬다."""
    _, fs = _folder_session("노트없음")
    r = client.get(f"/api/notes/{fs.id}")
    assert r.status_code == 400, r.text
    assert "노트가 없습니다" in r.json()["detail"]
    # 환자를 고른 fast 세션도 마찬가지 — 덱을 안 만드는 것은 같다
    ps = _session(fast=True)
    assert client.get(f"/api/notes/{ps.id}").status_code == 400
    # 본편 세션은 그대로 열린다
    full = _session(fast=False)
    assert client.get(f"/api/notes/{full.id}").status_code == 200


def test_이름_규칙을_화면에_내려준다():
    """화면이 '이렇게 저장됩니다' 예시를 지어내지 않고 서버 값을 쓴다."""
    r = client.get("/api/fl/naming")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["separator"] and d["ext"] == "jpg"
    assert d["number_mode"] in ("multi_only", "always")
    assert d["aliases"]["IO_FRONT"] == "(1)" and d["aliases"]["FACE"] == "(6)"


# ── 설정 ─────────────────────────────────────────────────────────────────────
def test_설정은_보낸_항목만_바꾼다():
    """화면이 늘 전부를 보내야 한다면 한 항목을 고칠 때마다 나머지를 실어
    나르다가 언젠가 하나를 빠뜨린다."""
    base = client.get("/api/fl/prefs").json()
    assert set(base["flip_defaults"]) == {"ref", "cur"}
    assert base["naming"]["aliases"]["IO_FRONT"] == "(1)"

    r = client.post("/api/fl/prefs",
                    json={"naming": {"aliases": {"IO_FRONT": "정면"}}}).json()
    assert r["naming"]["aliases"]["IO_FRONT"] == "정면"
    assert r["naming"]["aliases"]["IO_LEFT"] == "(3)", "안 보낸 별칭은 그대로"
    assert r["flip_defaults"] == base["flip_defaults"], "반전 표는 손대지 않았다"

    r = client.post("/api/fl/prefs", json={"naming": {"number_mode": "always",
                                                     "start": 0, "separator": "-"}}).json()
    assert r["naming"]["aliases"]["IO_FRONT"] == "정면", "별칭이 살아남는다"
    assert (r["naming"]["number_mode"], r["naming"]["start"],
            r["naming"]["separator"]) == ("always", 0, "-")


def test_설정은_쓸_수_없는_값을_막는다():
    bad = [{"naming": {"number_mode": "가끔"}},
           {"naming": {"start": 5}},
           {"naming": {"separator": "a/b"}},
           {"naming": {"aliases": {"IO_FRONT": "a:b"}}},
           {"naming": {"aliases": {"모르는것": "x"}}},
           {"flip_defaults": {"ref": {}}}]
    for body in bad:
        assert client.post("/api/fl/prefs", json=body).status_code == 400, body


def test_설정한_별칭이_실제_파일_이름에_쓰인다():
    """설정과 저장이 갈라지면 사람은 보지도 못한 이름을 보고 확정하게 된다."""
    client.post("/api/fl/prefs", json={"naming": {"aliases": {"IO_UPPER": "상악"},
                                                  "number_mode": "multi_only"}})
    body, s = _folder_session("별칭검사")
    FL._put(s, _photo(s, 600, "cur"), "SLOT_UPPER")
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert pl["files"][0]["file"] == "별칭검사 상악.jpg", pl["files"][0]


def test_반전_기본값을_바꾸면_분류가_그대로_따른다():
    client.post("/api/fl/prefs", json={"flip_defaults": {
        "ref": {"IO_UPPER": False}, "cur": {"IO_UPPER": False}}})
    s = _session(fast=True)
    p = _photo(s, 610, "cur")
    FL._put(s, p, "SLOT_UPPER")
    assert p.flip_v is False, "설정에서 껐으므로 뒤집지 않는다"


# ── 이름 번호 규칙 (본편과 같게) ─────────────────────────────────────────────
def test_별칭_기본값은_본편_번호_그대로다():
    """설정을 베끼지 않고 config 에서 뽑는다 — 베끼면 설정을 고친 날 갈라진다."""
    d = client.get("/api/fl/prefs").json()["aliases_default"]
    assert d == {cls: f"({si.index})" for cls, si in M.cfg.intraoral_slots.items()} | {
        "FACE": f"({M.cfg.face.start_index})"}
    assert d["IO_FRONT"] == "(1)" and d["FACE"] == "(6)"


def test_얼굴은_본편처럼_번호가_이어_오른다():
    """(6) 다음은 (6)_2 가 아니라 (7) 이다 — 본편이 그렇게 센다."""
    body, s = _folder_session("얼굴번호")
    for i in range(3):
        FL._put(s, _photo(s, 700 + i, "cur"), "FACE")
    names = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
    assert names == ["얼굴번호 (6).jpg", "얼굴번호 (7).jpg", "얼굴번호 (8).jpg"], names


def test_같은_자리_추가_촬영본은_뒤에_번호가_붙는다():
    """자리는 그대로고 장수만 는 것이라 번호를 올리면 옆자리와 부딪힌다."""
    body, s = _folder_session("추가촬영")
    for i in range(3):
        FL._put(s, _photo(s, 710 + i, "cur"), "SLOT_FRONT")
    names = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
    assert names == ["추가촬영 (1).jpg", "추가촬영 (1)-2.jpg", "추가촬영 (1)-3.jpg"], names


def test_글자_별칭을_주면_그쪽_규칙으로_간다():
    client.post("/api/fl/prefs", json={"naming": {"aliases": {"FACE": "얼굴"}}})
    body, s = _folder_session("글자별칭")
    for i in range(2):
        FL._put(s, _photo(s, 720 + i, "cur"), "FACE")
    names = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
    assert names == ["글자별칭 얼굴 1.jpg", "글자별칭 얼굴 2.jpg"], names


def test_옛_기본값으로_굳은_별칭은_새_기본값에_자리를_내준다(tmp_path):
    """옛 판본의 기본값은 카테고리 이름 그대로였다. 그 값이 남아 있다고 해서
    사람이 고른 것으로 볼 수는 없다 — 새 설치본과 이름이 갈라진다."""
    M.SETTINGS_FILE.write_text(
        '{"naming": {"aliases": {"IO_FRONT": "IO_FRONT", "IO_UPPER": "상악"}}}',
        encoding="utf-8")
    al = client.get("/api/fl/prefs").json()["naming"]["aliases"]
    assert al["IO_FRONT"] == "(1)", "옛 기본값은 새 기본값으로 넘어간다"
    assert al["IO_UPPER"] == "상악", "사람이 고친 것은 그대로 남는다"


def test_기준_풀은_아무것도_뒤집지_않는_것이_기본이다():
    """왼쪽에 떨구는 것이 이미 뒤집힌 완성본일 수도 있어 사람이 정하게 둔다."""
    assert M.FLIP_DEFAULTS["ref"] == {}
    M.SETTINGS_FILE.write_text("{}", encoding="utf-8")
    ref = client.get("/api/fl/prefs").json()["flip_defaults"]["ref"]
    assert not any(ref.values()), ref
    cur = client.get("/api/fl/prefs").json()["flip_defaults"]["cur"]
    assert cur["IO_UPPER"] and cur["IO_LOWER"], "오늘 사진의 교합면은 기본으로 뒤집는다"


def test_예시는_구내와_얼굴을_나눠_준다():
    """한 줄로 늘어놓으면 (6) 부터가 얼굴이라는 것이 안 보인다."""
    ex = client.get("/api/fl/naming?prefix=김하늘").json()["example"]
    assert ex["prefix"] == "김하늘"
    assert ex["io"] == [f"김하늘 ({i}).jpg" for i in range(1, 6)]
    assert ex["face"] == ["김하늘 (6).jpg", "김하늘 (7).jpg"], "얼굴은 번호가 이어 오른다"


def test_구분자는_접두어와_이름을_잇는다():
    """설정에 둔 값이 파일 이름에 실제로 보여야 한다 — 안 보이면 고장으로 읽힌다."""
    body, s = _folder_session("구분자검사")
    FL._put(s, _photo(s, 730, "cur"), "SLOT_FRONT")
    FL._put(s, _photo(s, 731, "cur"), "FACE")
    for sep, want in (("_", ["구분자검사_(1).jpg", "구분자검사_(6).jpg"]),
                      ("-", ["구분자검사-(1).jpg", "구분자검사-(6).jpg"]),
                      (" ", ["구분자검사 (1).jpg", "구분자검사 (6).jpg"])):
        client.post("/api/fl/prefs", json={"naming": {"separator": sep}})
        got = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
        assert got == want, (sep, got)


def test_구분자는_글자_이름에도_같이_쓰인다():
    client.post("/api/fl/prefs", json={"naming": {"separator": "-",
                                                  "aliases": {"FACE": "얼굴"}}})
    body, s = _folder_session("글자구분자")
    for i in range(2):
        FL._put(s, _photo(s, 740 + i, "cur"), "FACE")
    names = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
    assert names == ["글자구분자-얼굴-1.jpg", "글자구분자-얼굴-2.jpg"], names


def test_예시는_공통_머리를_알려준다():
    """화면이 두 번째부터 꼬리만 보일 때 뗄 만큼. 접두어만 떼면 구분자가 남는다."""
    client.post("/api/fl/prefs", json={"naming": {"separator": "-"}})
    ex = client.get("/api/fl/naming", params={"prefix": "김하늘"}).json()["example"]
    assert ex["join"] == "김하늘-"
    assert all(x.startswith(ex["join"]) for x in ex["io"] + ex["face"]), ex


# ── 한 자리를 여러 장 찍었을 때 ──────────────────────────────────────────────
def test_환자_폴더_이름은_본편_계획과_한_글자도_다르지_않다():
    """문자열을 적어 두고 비교하지 않는다 — **본편의 `_build_plan` 을 같은 세션에
    돌려** 나온 이름과 맞춰 본다. 본편이 규칙을 바꾸면 여기가 먼저 깨진다.

    같은 자리를 여러 장 찍은 경우와 얼굴 여러 장까지 함께 본다.
    """
    s = _ready(_session())                       # 다섯 자리 한 장씩
    FL._put(s, _photo(s, 800, "cur"), "SLOT_FRONT")   # 같은 자리 두 장 더
    FL._put(s, _photo(s, 801, "cur"), "SLOT_FRONT")
    FL._put(s, _photo(s, 802, "cur"), "FACE")         # 얼굴 두 장
    FL._put(s, _photo(s, 803, "cur"), "FACE")

    fast = sorted(f["file"] for f in FL._build_plan(s)["files"])

    plan = M._build_plan(s)
    want = []
    for e in plan["slots"]:
        if e["empty"]:
            continue
        want.append(e["file"])
        want += [x["file"] for x in e["extras"]]
    want += [f["file"] for f in plan["faces"]]

    assert fast == sorted(want), (fast, sorted(want))
    # 규약이 실제로 무엇인지도 한 번 박아 둔다 — 대표는 (1), 추가는 (1)-2·(1)-3
    names = [Path(x).name for x in fast]
    assert "54321_A (1).jpg" in names and "54321_A (1)-2.jpg" in names, names
    assert "54321_A (1)-3.jpg" in names, names
    assert "54321_A (6).jpg" in names and "54321_A (7).jpg" in names, names


def test_환자_없이_저장해도_추가_촬영본은_같은_모양이다():
    """이름의 앞부분만 다르고 `-2` 규칙은 같다 — 두 경로를 오가도 읽는 법이 하나다."""
    body, s = _folder_session("여러장")
    for i in range(3):
        FL._put(s, _photo(s, 810 + i, "cur"), "SLOT_FRONT")
    names = [f["file"] for f in client.get(f"/api/fl/plan/{s.id}").json()["files"]]
    assert names == ["여러장 (1).jpg", "여러장 (1)-2.jpg", "여러장 (1)-3.jpg"], names


def test_반전_안내_그림이_내보내진다():
    """설정 창의 방향 예시. `/assets` 는 지금까지 화면에 나가지 않던 자리라
    마운트를 빠뜨리면 그림만 조용히 안 뜬다."""
    r = client.get("/assets/flip_appendix.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert len(r.content) > 1000


def test_기본_구분자는_공백_한_칸이다():
    """본편의 사진 이름은 `12345_A (1).jpg` — `_` 는 신원 조각(번호·차수)을 잇고
    **공백이 신원과 번호를 가른다**. 환자 없이 저장할 때의 접두어가 곧 그 신원
    자리이므로, 같은 자리에 오는 구분자도 공백이라야 두 경로가 한 모양이 된다."""
    assert client.get("/api/fl/prefs").json()["naming"]["separator"] == " "
    body, s = _folder_session("공백기본")
    FL._put(s, _photo(s, 820, "cur"), "SLOT_FRONT")
    got = client.get(f"/api/fl/plan/{s.id}").json()["files"][0]["file"]
    assert got == "공백기본 (1).jpg", got
    # 본편이 같은 자리에 만드는 이름과 모양이 같다
    assert M.N.photo_filename("12345", "A", 1, M.cfg.naming.photo_pattern) \
        == "12345_A (1).jpg"


def test_기본값과_같은_별칭은_저장하지_않는다():
    """굳혀 두면 나중에 설정의 번호를 바꿔도 저장된 옛 값이 이겨서 갈라진다."""
    client.post("/api/fl/prefs", json={"naming": {"aliases": {"IO_FRONT": "정면"}}})
    saved = json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved["naming"]["aliases"] == {"IO_FRONT": "정면"}

    client.post("/api/fl/prefs", json={"naming": {"aliases": {"IO_FRONT": "(1)"}}})
    saved = json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved["naming"]["aliases"] == {}, "기본값으로 되돌리면 자국이 남지 않는다"
    assert client.get("/api/fl/prefs").json()["naming"]["aliases"]["IO_FRONT"] == "(1)"


# ── 얼굴 검수 ────────────────────────────────────────────────────────────────
def test_얼굴은_사진마다_구도를_잡는다():
    """본편은 얼굴을 케이스 덱의 슬라이드 자리에 놓고 그 자리마다 잡는다.
    이 모드는 덱이 없으므로 **사진 한 장이 곧 대상**이다 — 자리가 아니라 사진을
    가리켜 조정한다."""
    s = _session(fast=True)
    a, b = _photo(s, 900, "cur"), _photo(s, 901, "cur")
    FL._put(s, a, "FACE")
    FL._put(s, b, "FACE")
    r = client.post("/api/fl/adjust", json={"session_id": s.id, "photo_id": b.id,
                                            "dx": 12, "dy": -8, "scale": 1.1, "angle": 2})
    assert r.status_code == 200, r.text
    assert abs(b.editor.dx_px - 12) < 1e-6 and abs(b.editor.dy_px + 8) < 1e-6
    assert a.editor.dx_px == 0, "옆 사진은 건드리지 않는다"


def test_얼굴_창은_3대4_세로다():
    """검수 캔버스와 저장 해상도가 이 창 하나로 선다."""
    w = FL.FACE_WINDOW
    assert abs(w.w / w.h - 0.75) < 1e-9, (w.w, w.h)
    assert w.w == next(iter(M.SLOT_WINDOWS.values())).w, "폭은 구내 슬롯과 같다"
    # 화면이 캔버스를 세울 수 있게 세션이 창을 알려준다
    body, fs = _folder_session("얼굴창")
    assert body["face_window"] == {"x": 0.0, "y": 0.0, "w": w.w, "h": w.h}


def test_얼굴_구도는_저장까지_따라간다():
    body, s = _folder_session("얼굴구도")
    p = _photo(s, 910, "cur")
    FL._put(s, p, "FACE")
    client.post("/api/fl/adjust", json={"session_id": s.id, "photo_id": p.id,
                                        "dx": 5, "dy": 5, "scale": 1.2, "angle": 0})
    assert client.post(f"/api/fl/commit/{s.id}",
                       params={"allow_missing": True}).status_code == 200
    out = M.ROOT / "얼굴구도"
    got = [q for q in out.rglob("*.jpg")]
    assert len(got) == 1 and got[0].name == "얼굴구도 (6).jpg", [q.name for q in got]
    # 얼굴 창(3:4)으로 구워진다 — 구내(4:3)와 다르다
    img = cv2.imread(str(got[0]))
    ppcm = M.cfg.geometry.export_px_per_cm
    assert img.shape[:2] == (round(FL.FACE_WINDOW.h * ppcm),
                             round(FL.FACE_WINDOW.w * ppcm)), img.shape


# ── 저장 폴더 열기 ───────────────────────────────────────────────────────────
def test_저장_폴더_열기는_경로를_화면에서_받지_않는다(monkeypatch):
    """이 모드는 저장 루트 밖도 고를 수 있어 본편의 루트 기준 열기로는 닿지 않는다.
    그렇다고 브라우저가 보낸 절대경로를 그대로 여는 것은 다른 이야기다 —
    서버가 아는 자리(세션의 저장 위치 · 방금 저장한 자리)만 연다."""
    opened = []
    monkeypatch.setattr(M, "_os_open", lambda p: opened.append(p))

    body, s = _folder_session("열기검사")
    # 아직 만들어지지 않았다
    assert client.post("/api/fl/open", json={"session_id": s.id}).status_code == 404

    FL._put(s, _photo(s, 950, "cur"), "SLOT_FRONT")
    assert client.post(f"/api/fl/commit/{s.id}",
                       params={"allow_missing": True}).status_code == 200
    # 저장이 끝나면 세션은 사라진다 — 그래도 방금 저장한 자리는 열 수 있어야 한다
    r = client.post("/api/fl/open", json={})
    assert r.status_code == 200, r.text
    assert opened and opened[-1] == M.ROOT / "열기검사"
    assert r.json()["opened"] == str(M.ROOT / "열기검사")


def test_없는_세션으로는_못_연다():
    assert client.post("/api/fl/open", json={"session_id": "없는세션"}).status_code == 410


# ── 거울로 찍은 사진의 방향 ──────────────────────────────────────────────────
class _Pred:
    def __init__(self, label, confidence):
        self.label, self.confidence, self.probs = label, confidence, {}


def _stub_classifier(monkeypatch, answers):
    """`answers` 는 (원본 답, 뒤집은 답). 호출 횟수를 함께 센다."""
    calls = []

    class _C:
        def predict(self, im, filename=None):
            calls.append(filename)
            return answers[min(len(calls) - 1, len(answers) - 1)]

    monkeypatch.setattr(M, "classifier", _C())
    return calls


def test_교합면은_뒤집어서_한_번_더_묻는다(monkeypatch):
    """분류 모델은 반전 없는 원본으로 학습됐다. 그런데 기준 풀에는 반전이 이미
    구워진 **저장본**이 들어오는 일이 흔하다 — 실측에서 하악 저장본이 98.4%
    확신으로 '상악' 이라고 나왔다. 두 장이 같은 상자로 몰리면 하악 기준이 비고
    정합이 조용히 프레이밍으로 물러난다."""
    from PIL import Image
    im = Image.new("RGB", (40, 30))

    calls = _stub_classifier(monkeypatch, [_Pred("IO_UPPER", 0.984),
                                           _Pred("IO_LOWER", 1.0)])
    got = FL._predict(im, "a.jpg")
    assert len(calls) == 2, "교합면이면 뒤집어서도 물어야 한다"
    assert got.label == "IO_LOWER", "더 확신하는 쪽을 쓴다"


def test_거울로_찍지_않는_카테고리는_한_번만_묻는다(monkeypatch):
    """얼굴·정면·측방은 뒤집어도 답이 같다 — 값을 치를 자리가 아니다."""
    from PIL import Image
    im = Image.new("RGB", (40, 30))
    calls = _stub_classifier(monkeypatch, [_Pred("FACE", 0.99)])
    assert FL._predict(im, "a.jpg").label == "FACE"
    assert len(calls) == 1, calls


def test_확신이_낮으면_카테고리와_무관하게_다시_묻는다(monkeypatch):
    from PIL import Image
    im = Image.new("RGB", (40, 30))
    calls = _stub_classifier(monkeypatch, [_Pred("IO_FRONT", 0.4),
                                           _Pred("IO_FRONT", 0.95)])
    got = FL._predict(im, "a.jpg")
    assert len(calls) == 2 and got.confidence == 0.95


def test_거울_카테고리는_설정에서_나온다():
    """여기 베껴 두면 `flip_v_slots` 를 고친 날 조용히 갈라진다."""
    by_slot = {si.slot: cls for cls, si in M.cfg.intraoral_slots.items()}
    assert FL._mirror_classes() == {by_slot[s] for s in M.cfg.flip_v_slots}
    assert FL._mirror_classes() == {"IO_UPPER", "IO_LOWER"}


def test_정합을_돌면_겹쳐볼_기준이_생긴다():
    """기준영상은 정합을 돌 때 구워진다 — 분류 시점에는 아직 없다.
    화면이 그 목록으로 겹쳐보기·대보기를 켜므로, 이 순서가 어긋나면
    "직전 차수가 없습니다" 만 뜬다."""
    s = _session(fast=True)
    ref, cur = _photo(s, 960, "ref"), _photo(s, 961, "cur")
    FL._put(s, ref, "SLOT_UPPER")
    FL._put(s, cur, "SLOT_UPPER")

    # 분류 직후 — 아직 구운 것이 없다
    assert client.get(f"/api/references/{s.id}").json() == {}
    # 정합을 돌면 생긴다
    assert client.post(f"/api/fl/register/{s.id}").status_code == 200
    assert client.get(f"/api/references/{s.id}").json() == {"SLOT_UPPER": [FL.REF_KEY]}


def test_네_자리만_채워도_끝까지_간다():
    """사진이 빠지는 날이 있다. 확정 저장이 어차피 되묻는데 그 앞에서만 막아 두면
    사람은 되돌아갈 길이 없다."""
    s = _session(fast=True)
    slots = M.cfg.ppt.slot_names[:4]              # 다섯 중 넷만
    for i, slot in enumerate(slots):
        FL._put(s, _photo(s, 970 + i, "cur"), slot)

    assert client.post(f"/api/fl/register/{s.id}").status_code == 200
    pl = client.get(f"/api/fl/plan/{s.id}").json()
    assert len(pl["files"]) == 4
    assert pl["missing"] == [M.cfg.ppt.slot_names[4]]
    # 그냥 확정하면 한 번 되묻고, 허락하면 넷을 그대로 저장한다
    assert client.post(f"/api/fl/commit/{s.id}").status_code == 409
    r = client.post(f"/api/fl/commit/{s.id}", params={"allow_missing": True})
    assert r.status_code == 200, r.text
    assert len(r.json()["files"]) == 4


def test_얼굴만_있어도_저장된다():
    """구내를 한 장도 안 찍은 날 — 저장할 것이 있으면 막을 이유가 없다."""
    body, s = _folder_session("얼굴만")
    FL._put(s, _photo(s, 980, "cur"), "FACE")
    r = client.post(f"/api/fl/commit/{s.id}", params={"allow_missing": True})
    assert r.status_code == 200, r.text
    assert [Path(x).name for x in r.json()["files"]] == ["얼굴만 (6).jpg"]


# ── 병렬 구간 ────────────────────────────────────────────────────────────────
def test_상자는_여러_스레드가_함께_만져도_망가지지_않는다():
    """기준 사진의 prewarm 이 백그라운드에서 배정하는 동안 사람이 `자동 분류로` 를
    누르거나 카드를 끌면 같은 목록을 두 스레드가 동시에 고친다. 잠그지 않으면
    사진이 두 번 들어가거나 사라진다 — 화면에는 카드가 겹쳐 보이고, 대표가
    엉뚱한 장이 된다."""
    s = _session(fast=True)
    photos = [_photo(s, 990 + i, "cur") for i in range(6)]
    slot = M.cfg.ppt.slot_names[0]

    def work():
        for p in photos:
            FL._put(s, p, slot)

    ts = [threading.Thread(target=work) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    got = s.bins[slot]
    assert sorted(got) == sorted(p.id for p in photos), got
    assert len(got) == len(set(got)), f"같은 사진이 두 번 들어갔다: {got}"


def test_정합이_도는_동안_진행표를_읽어도_터지지_않는다():
    """화면은 400ms 마다 진행을 폴링한다 — 그 사이 작업 스레드가 표를 고친다.
    베끼는 동안 크기가 바뀌면 파이썬이 예외를 던진다."""
    s = _session(fast=True)
    for i, slot in enumerate(M.cfg.ppt.slot_names):
        FL._put(s, _photo(s, 1000 + i, "cur"), slot)

    seen, stop = [], threading.Event()

    def poll():
        while not stop.is_set():
            seen.append(FL._progress_json(s))     # 예외가 나면 스레드가 죽는다

    t = threading.Thread(target=poll)
    t.start()
    try:
        FL._register(s)
    finally:
        stop.set(); t.join()

    assert seen, "폴링이 한 번도 못 돌았다"
    assert all(isinstance(x, dict) for x in seen)
    assert set(s.progress) == set(M.cfg.ppt.slot_names)


def test_추론_도중_뺀_사진은_다시_들어가지_않는다(monkeypatch):
    """분류는 느린 추론을 잠금 **밖에서** 한다. 그 사이 사람이 × 로 사진을 빼면,
    끝난 추론이 이미 없는 사진을 상자에 넣어 버린다 — 그때부터 모든 응답이
    404('사진 없음')로 넘어져 세션을 못 쓰게 된다."""
    s = _session(fast=True)
    p = _photo(s, 1100, "ref")
    gate = threading.Event()

    def slow(im, filename=None):
        gate.wait(3)
        return _Pred("IO_UPPER", 0.99)

    monkeypatch.setattr(FL, "_predict", slow)
    t = threading.Thread(target=FL._classify, args=(s, [p]))
    t.start()
    try:
        assert client.delete(f"/api/fl/photos/{s.id}/{p.id}").status_code == 200
    finally:
        gate.set(); t.join()

    assert p.id not in s.ref_bins.get("SLOT_UPPER", []), s.ref_bins
    assert client.get(f"/api/fl/review/{s.id}").status_code == 200, "세션이 살아 있어야 한다"


def test_상자에_못_들어간_기준_사진도_화면에_나온다():
    """분류가 어긋나 OTHERS 로 빠진 기준 사진이 화면에서 사라지면, 사람이 끌어
    넣어 고칠 방법이 없다 — 정합은 조용히 프레이밍으로 물러난다."""
    s = _session(fast=True)
    p = _photo(s, 1110, "ref")
    p.slot = None                                  # 어느 상자에도 안 들어갔다
    rv = client.get(f"/api/fl/review/{s.id}").json()["review"]
    assert rv["has_ref"] is True, "기준 풀에 사진이 있으면 짝맞춤 화면을 연다"
    assert [x["id"] for x in rv["others"]["ref"]] == [p.id]


def test_한_자리가_터져도_나머지를_끝까지_돌린다(monkeypatch):
    """첫 예외에서 바로 던지면 남은 일꾼이 도는 채로 요청이 끝난다 — 사람이 다시
    누르면 같은 사진을 두 스레드가 함께 고친다."""
    s = _session(fast=True)
    for i, slot in enumerate(M.cfg.ppt.slot_names):
        FL._put(s, _photo(s, 1120 + i, "cur"), slot)

    done, bad = [], M.cfg.ppt.slot_names[0]
    real = FL._frame_slot

    def flaky(sess, slot):
        if slot == bad:
            raise RuntimeError("정합 폭발")
        real(sess, slot)
        done.append(slot)

    monkeypatch.setattr(FL, "_frame_slot", flaky)
    with pytest.raises(RuntimeError):
        FL._register(s)
    assert sorted(done) == sorted(M.cfg.ppt.slot_names[1:]), done


# ── 본편: 폴더 이름과 덱의 교정번호가 다를 때 ────────────────────────────────
def test_교정번호가_달라도_덱을_지정할_수_있다(tmp_path):
    """폴더 이름의 교정번호가 덱과 다르면 그 덱은 기각된다. 그때 사람이 직접
    지정하는 길이 없으면 폴더 이름을 고쳐 다시 읽히는 수밖에 없다 —
    실제로 그 길이 막혀 사용자가 손을 놓았다."""
    d = M.ROOT / "129. 이서희(20001)_차트번호"
    d.mkdir(parents=True)
    deck = d / "이서희(21450)_여예은.pptx"
    deck.write_bytes(b"not a real pptx")          # 이름만 보면 된다

    r = client.post("/api/folder/ppt",
                    json={"folder": d.name, "ppt": deck.name})
    assert r.status_code == 200, r.text
    assert r.json()["ppt"] == deck.name
    # 기억은 다음에도 그 덱으로 이어진다
    assert M._remembered_ppt(str(d)) == deck.name


def test_폴더_이름을_앱_안에서_고친다(tmp_path):
    """폴더 이름은 신원의 출처다 — 사진 이름도 차수 폴더도 거기서 나온다.
    덱과 번호가 어긋나면 고칠 곳이 여기고, 탐색기에서 하다 사본이 생기면
    어느 쪽을 고쳤는지 잃는다."""
    d = M.ROOT / "이서희_123456789_20001"
    d.mkdir(parents=True)
    deck = d / "이서희(21450).pptx"; deck.write_bytes(b"x")
    M._remember_ppt(str(d), deck.name)          # 지정해 둔 덱이 있다

    r = client.post("/api/folder/rename",
                    json={"folder": d.name, "name": "이서희_123456789_21450"})
    assert r.status_code == 200, r.text
    new = M.ROOT / "이서희_123456789_21450"
    assert new.is_dir() and not d.exists()
    # 기억은 새 이름으로 따라온다 — 안 그러면 다음 차수가 엉뚱한 덱으로 간다
    assert M._remembered_ppt(str(new)) == deck.name


def test_이름을_바꿀_수_없는_경우는_막는다(tmp_path):
    a = M.ROOT / "이서희_123456789_20001"; a.mkdir(parents=True)
    b = M.ROOT / "김철수_123456789_20002"; b.mkdir(parents=True)

    # 이미 있는 이름으로는 못 바꾼다 (덮어쓰면 환자 자료가 섞인다)
    assert client.post("/api/folder/rename",
                       json={"folder": a.name, "name": b.name}).status_code == 409
    # 규칙에 안 맞는 이름은 목록에서 사라진다 — 미리 막는다
    assert client.post("/api/folder/rename",
                       json={"folder": a.name, "name": "그냥아무이름"}).status_code == 400
    # 저장 위치 밖으로는 못 나간다
    assert client.post("/api/folder/rename",
                       json={"folder": a.name, "name": "../밖으로"}).status_code == 400
    assert a.is_dir(), "막힌 요청은 아무것도 바꾸지 않는다"


# ── 사진 통째로 비우기 ───────────────────────────────────────────────────────
def test_사진을_비우면_딸린_상태도_함께_비운다():
    """목록만 비우면 겉보기엔 깨끗한데 속은 아닌 세션이 남는다 — 상자에 없는
    사진의 열쇠가 남으면 그 뒤 모든 응답이 404 로 넘어지고, 계산 기록이 남으면
    새로 올린 사진이 '이미 했다' 로 정합을 건너뛴다."""
    s = _ready(_session(fast=False))              # 본편 세션, 다섯 자리 채움
    s.framed = {M.cfg.ppt.slot_names[0]: s.slots[M.cfg.ppt.slot_names[0]]}
    s.face_slots = {"4L": s.photos[0].id}
    s.first_date = "26.09.04"
    paths = [p.path for p in s.photos]

    r = client.delete(f"/api/photos/{s.id}")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 5 and r.json()["photos"] == []
    assert s.photos == [] and s.bins == {} and s.framed == {}
    assert s.face_slots == {} and s.face_manual is False
    assert s.first_date is None, "초진일은 사진 EXIF 에서 왔다"
    assert not any(q.exists() for q in paths), "임시 파일도 지운다"
    # 세션은 살아 있다 — 고른 환자와 차수는 그대로다
    assert client.get(f"/api/notes/{s.id}").status_code == 200


def test_본편_기준영상은_사진을_비워도_남는다():
    """그것은 이전 차수 슬라이드에서 복원한 것이라 사진과 무관하다."""
    s = _ready(_session(fast=False))
    s.references = {"SLOT_UPPER": {"A": "그림"}}
    client.delete(f"/api/photos/{s.id}")
    assert s.references == {"SLOT_UPPER": {"A": "그림"}}


def test_fast_는_칸마다_따로_비운다():
    """왼쪽만 잘못 넣었는데 오른쪽까지 지워지면 처음 실수보다 나쁘다."""
    s = _session(fast=True)
    ref, cur = _photo(s, 1200, "ref"), _photo(s, 1201, "cur")
    FL._put(s, ref, "SLOT_UPPER")
    FL._put(s, cur, "SLOT_UPPER")
    FL._ref_bake(s, "SLOT_UPPER")
    assert "SLOT_UPPER" in s.references

    r = client.delete(f"/api/fl/photos/{s.id}?pool=ref")
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert [p.id for p in s.photos] == [cur.id], "오늘 사진은 그대로"
    assert s.ref_bins["SLOT_UPPER"] == []
    assert "SLOT_UPPER" not in s.references, "그 사진으로 구운 기준영상도 함께"
    assert client.delete(f"/api/fl/photos/{s.id}?pool=없는풀").status_code == 400
