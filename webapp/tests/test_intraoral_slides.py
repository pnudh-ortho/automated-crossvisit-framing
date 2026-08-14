"""초진 덱의 **구내 한 장짜리 슬라이드(12~16)** 와 **원본 저장(`_raw`)**.

두 가지를 종단으로 확인한다. 분류기를 태우지 않고 `/api/assign` 으로 자리를
직접 지정한다 — 여기서 보려는 것은 분류가 아니라 **어디에 무엇이 저장되는가** 다.

    12 FRONT   13 RIGHT   14 LEFT   15 UPPER   16 LOWER

원본 저장이 꺼져 있으면(기본) 환자 폴더에는 잘린 사진만 남는다. 폴더와 PPT 가
다른 그림이면 나중에 어느 쪽이 진짜인지 다투게 된다. 켜면 원본이 `_raw` 로 함께
남는다 — 끈 상태에서는 **원본이 어디에도 남지 않으므로** 이 선택은 되돌릴 수 없다.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main                                                       # noqa: E402
import naming as N                                                # noqa: E402
from template import load_presentation                            # noqa: E402

pytestmark = pytest.mark.skipif(not main._case_deck_ready(),
                                reason="케이스 양식이 없습니다")

_PICTURE = 13
HOSP, ORTHO = "987654321", "13579"
NAME = "구내검사"
# 잘린 완성본은 '교정번호_차수/', 원본 사본은 '교정번호_차수_raw/' 폴더로 간다.
VDIR, RDIR = f"{ORTHO}_A", f"{ORTHO}_A_raw"
# 촬영 순서 = 파일 순번. 슬라이드 12~16 이 이 순서를 그대로 따라야 한다.
#
#   (1) 정면   (2) 우측방   (3) 좌측방   (4) 상악   (5) 하악
#
# `SLOT_*` 은 **슬라이드에서의 자리**지 환자의 좌우가 아니다 — 환자를 마주본 것처럼
# 놓으므로 우측방이 SLOT_LEFT 로 간다. 이름의 right/left 를 보고 짝지으면 13·14 가
# 뒤집히고, 실제로 뒤집혀 있었다. 그래서 여기서는 자리 이름이 아니라 **순번**으로
# 확인한다 — 단어가 어긋나도 이 테스트는 안 속는다.
ORDER = [("SLOT_FRONT", 12, 1), ("SLOT_LEFT", 13, 2), ("SLOT_RIGHT", 14, 3),
         ("SLOT_UPPER", 15, 4), ("SLOT_LOWER", 16, 5)]


# 순번마다 확실히 다른 **평균색**. 12~16 은 창이 커서 십자뷰와 다른 크기로 굽는다 —
# 바이트가 다르므로 같은 사진인지는 내용으로 봐야 한다. 평균색은 축소·JPEG 을
# 지나도 남는다.
HUES = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40), (200, 40, 200)]


def _jpg(idx: int) -> bytes:
    img = np.full((1200, 1600, 3), HUES[idx], np.uint8)
    rng = np.random.default_rng(idx)
    for _ in range(40):                       # 정합·프레이밍이 볼 무늬
        p = tuple(int(x) for x in rng.integers(0, [1600, 1200], 2))
        cv2.circle(img, p, 90, (255, 255, 255), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _mean(blob: bytes):
    a = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
    return a.reshape(-1, 3).mean(0)


@pytest.fixture
def app(_isolate_paths):
    """저장 위치·감사 로그는 conftest 가 임시 폴더로 돌려 둔다."""
    with TestClient(main.app) as c:
        yield c, _isolate_paths


def _commit(c, root, *, save_raw: bool):
    if save_raw:
        assert c.post("/api/prefs", json={"save_raw": True}).json()["save_raw"] is True
    r = c.post("/api/session/first",
               json={"name": NAME, "hospital_id": HOSP, "ortho_id": ORTHO})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    files = [("files", (f"{slot}.jpg", io.BytesIO(_jpg(i)), "image/jpeg"))
             for i, (slot, _, _n) in enumerate(ORDER)]
    up = c.post(f"/api/upload/{sid}", files=files)
    assert up.status_code == 200, up.text
    photos = up.json()["photos"]
    assert len(photos) == len(ORDER)

    # 분류 결과와 무관하게 자리를 직접 지정한다 (파일명 순서 = ORDER 순서)
    for (slot, _, _n), p in zip(ORDER, photos):
        r = c.post("/api/assign", json={"session_id": sid, "photo_id": p["id"],
                                        "slot": slot, "at": 0})
        assert r.status_code == 200, r.text

    plan = c.get(f"/api/plan/{sid}").json()
    r = c.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    return plan, r.json(), root / f"{NAME}_{HOSP}_{ORTHO}"


def test_구내_슬라이드_12_16에_한_장씩_들어간다(app):
    c, root = app
    _, res, pdir = _commit(c, root, save_raw=False)
    prs = load_presentation(pdir / res["ppt"])
    assert len(prs.slides) == 17, "16장 + 십자뷰"

    for slot, no, idx in ORDER:
        pics = [sh for sh in prs.slides[no - 1].shapes if sh.shape_type == _PICTURE]
        assert len(pics) == 1, f"슬라이드 {no}({slot}) 그림 {len(pics)}장"
        assert pics[0].name.endswith(f"IO_{slot}"), pics[0].name
        # **순번으로** 확인한다. 슬라이드에 실린 그림이 그 순번의 사진과 같은
        # 그림이어야 한다 — 자리 이름의 right/left 에 기대지 않는다.
        saved = (pdir / VDIR / f"{ORTHO}_A ({idx}).jpg").read_bytes()
        d = float(np.abs(_mean(pics[0].image.blob) - _mean(saved)).max())
        assert d < 12, f"슬라이드 {no} 에 ({idx}) 가 아닌 사진 (평균색 차 {d:.0f})"
        assert not [sh for sh in prs.slides[no - 1].shapes if sh.is_placeholder], no


def test_기본은_잘린_사진만_저장한다(app):
    c, root = app
    plan, res, pdir = _commit(c, root, save_raw=False)
    assert all(e.get("raw") is None for e in plan["slots"] if not e["empty"])
    saved = sorted(p.name for p in (pdir / VDIR).glob("*.jpg"))
    assert saved and not [n for n in saved if n.rsplit(".", 1)[0].endswith("_raw")], saved
    assert set(res["files"]) == {f"{VDIR}/{n}" for n in saved} | {res["ppt"]}

    # 저장된 것이 **슬라이드와 같은 그림**인가 — 창 비율(4:3)로 잘려 있어야 한다
    img = cv2.imread(str(pdir / VDIR / f"{ORTHO}_A (1).jpg"))
    h, w = img.shape[:2]  # noqa: F841
    assert abs(w / h - 8.40 / 6.30) < 0.01, f"{w}x{h} — 원본 비율 그대로다"


def test_원본_저장을_켜면_raw가_함께_남는다(app):
    c, root = app
    plan, res, pdir = _commit(c, root, save_raw=True)
    raws = {e["raw"] for e in plan["slots"] if not e["empty"]}
    # 원본 사본은 '교정번호_차수_raw/' 폴더로 간다 — 완성본 폴더와 나란히 선다
    assert raws == {f"{RDIR}/{ORTHO}_A ({i})_raw.jpg" for i in range(1, 6)}, raws
    for name in raws:
        assert (pdir / name).exists(), f"{name} 없음"
    on_disk = {p.relative_to(pdir).as_posix() for p in pdir.rglob("*") if p.is_file()}
    assert set(res["files"]) == on_disk

    # 원본은 자르지 않은 그대로여야 한다
    raw = cv2.imread(str(pdir / RDIR / f"{ORTHO}_A (1)_raw.jpg"))
    assert raw.shape[:2] == (1200, 1600), raw.shape


def test_사진은_읽지_않는다_차수는_PPT_라벨로(app):
    """환자 목록·폴더 보기 어디에도 사진 스캔이 없다 — 차수는 PPT 라벨이 진실."""
    c, root = app
    _, _, pdir = _commit(c, root, save_raw=True)
    rec = c.get("/api/patients").json()
    me = [p for p in rec["patients"] if p["ortho_id"] == ORTHO]
    assert me, rec
    assert "photos" not in me[0]                      # 사진 장수 집계는 없다
    # 차수는 환자를 열 때 PPT 라벨에서 온다 — 사진 파일명은 어디서도 안 읽는다
    one = c.get("/api/patient", params={"folder": me[0]["folder"]}).json()
    assert one["visits"] == ["A"], one
    fc = c.get("/api/folder", params={"folder": pdir.name}).json()
    assert "visits" not in fc                         # 파일명 차수 해석도 없다
    # 자동 선택된 PPT 가 무엇인지 알려준다 — 화면이 "선택됨"으로 표시한다
    assert fc["ppt"], fc
    sel = [i for i in fc["items"] if i.get("selected")]
    assert len(sel) == 1 and sel[0]["name"].endswith(fc["ppt"]), fc["items"]


def test_촬영시각이_굽는_동안_바뀌지_않는다(app):
    """완성본과 원본 사본의 mtime 이 같아야 한다 — 촬영시각(mtime) 보존."""
    c, root = app
    _, _, pdir = _commit(c, root, save_raw=True)
    cropped = (pdir / VDIR / f"{ORTHO}_A (1).jpg").stat().st_mtime
    raw = (pdir / RDIR / f"{ORTHO}_A (1)_raw.jpg").stat().st_mtime
    assert abs(cropped - raw) < 2, (cropped, raw)


def test_구운_사진은_창_크기_그대로_들어간다():
    """직전 차수 사진과 크기가 어긋나면 안 된다.

    창에 맞춰 구운 사진은 창이 곧 제 크기인데, 배치를 cover-fit 에 맡기면 구운
    파일의 **정수 픽셀 비율**로 크기를 다시 셈한다. 0.002cm 남짓이지만 소수 둘째
    자리 경계를 넘으면 PowerPoint 에 8.38 이 8.39 로 보인다.
    """
    from coords import WindowCm, cover_fit_placement, emu_to_cm

    for w, h in [(8.3833, 6.2900), (8.3805, 6.2938), (8.3750, 6.2875), (8.38, 6.29)]:
        win = WindowCm(x=1.0, y=2.0, w=w, h=h)
        pl = main._exact_placement(win)
        assert (round(emu_to_cm(pl.ext_cx), 4), round(emu_to_cm(pl.ext_cy), 4)) == (w, h)
        assert (round(emu_to_cm(pl.off_x), 4), round(emu_to_cm(pl.off_y), 4)) == (1.0, 2.0)
        assert pl.rot == 0          # 회전은 픽셀에 구워져 있다

    # cover-fit 은 실제로 어긋났다 — 이 테스트가 지키려는 것이 그 차이다
    win = WindowCm(x=0, y=0, w=8.3805, h=6.2938)
    off = cover_fit_placement(round(8.3805 * 200), round(6.2938 * 200), win)
    assert round(emu_to_cm(off.ext_cy), 4) != 6.2938
