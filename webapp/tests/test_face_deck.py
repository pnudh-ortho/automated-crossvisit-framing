"""
얼굴 자리 자동 배치 · 자리별 편집기 검증.

두 가지를 고정한다:
  1) 자동분류에서 세운 순서 n번째 사진 → config.face_auto_order 의 n번째 자리
  2) 파생 자리(10·11)는 슬라이드 4 좌측과 **같은 사진 영역**을 보여준다
     — 창이 1.083배 크므로 dx·dy 를 같이 키워야 구도가 밀리지 않는다.

실행: cd webapp && python tests/test_face_deck.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402
from coords import EditorState, cover_base_ext_cm, editor_to_placement, emu_to_cm  # noqa: E402

OUT = __import__("pathlib").Path(os.environ.get("TMPDIR", "/tmp")) / "face_deck_test"


_IMG = None


def _tiny_jpeg():
    """실제로 디코드되는 작은 JPEG 하나. 없으면 cv2.imread 경고만 잔뜩 찍힌다.

    크기는 Photo(4000x3000) 와 다르지만 기하 계산은 Photo 의 w/h 만 쓰므로
    결과에 영향이 없다 — 여기서는 "읽히는 파일"이라는 사실만 필요하다.
    """
    global _IMG
    if _IMG is None:
        import numpy as np
        from PIL import Image
        p = OUT / "face.jpg"
        OUT.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(np.random.rand(120, 160, 3) * 255)).save(p)
        _IMG = str(p)
    return _IMG


def _session_with_faces(n, model=False):
    """얼굴 n장이 든 세션.

    기본은 **모델 없음**(face_frames 를 None 으로 미리 채움)이다. 이 파일이 보는
    것은 기하이지 모델 정확도가 아니고, 실제 ONNX 예측이 끼면 결과가 모델에
    좌우되어 회귀 판정이 불가능해진다. 예측이 필요한 테스트만 직접 주입한다.
    """
    s = M.Session("first", None, "A")
    M.SESSIONS[s.id] = s          # 엔드포인트를 직접 부르는 테스트가 있다
    for i in range(n):
        p = M.Photo(f"f{i + 1}", _tiny_jpeg(), 4000, 3000)
        p.orig_name = f"IMG_{i + 1:04d}.JPG"
        s.photos.append(p)
        M._put(s, p, "FACE")
    if not model:
        s.face_frames = {p.id: None for p in s.photos}
    return s


def _crop_box(photo_wh, win, st):
    """이 배치가 사진의 어느 영역을 창에 보여주는가 — 사진 크기로 정규화한 (x,y,w,h).

    창 rect 를 사진 rect 기준 비율로 환산한다. 창 크기가 달라도 같은 영역을
    보여준다면 이 네 값이 같아야 한다.
    """
    bw, bh = cover_base_ext_cm(photo_wh[0], photo_wh[1], win)
    pl = editor_to_placement(st, win, bw, bh, M.PPC)
    ox, oy = emu_to_cm(pl.off_x), emu_to_cm(pl.off_y)
    ew, eh = emu_to_cm(pl.ext_cx), emu_to_cm(pl.ext_cy)
    return ((win.x - ox) / ew, (win.y - oy) / eh, win.w / ew, win.h / eh)


def test_auto_order_matches_spec():
    """슬라이드 4(1,2) · 5(4,5) · 6(6,7) · 7(3) · 8(8) · 9(9)."""
    s = _session_with_faces(9)
    placed = M._auto_assign_faces(s)
    assert placed == 9, placed
    got = {cell: s.face_slots[cell] for cell in s.face_slots}
    want = {"4L": "f1", "4R": "f2", "7C": "f3", "5L": "f4", "5R": "f5",
            "6L": "f6", "6R": "f7", "8C": "f8", "9C": "f9"}
    assert got == want, got
    print("PASS 자동 배치 순서 =", {k: want[k] for k in sorted(want)})


def test_derived_cells_follow_slide4_left():
    s = _session_with_faces(9)
    M._auto_assign_faces(s)
    full = M._face_slots_json(s)
    for k in ("10BIG", "11BIG"):
        assert full[k] == full["4L"], f"{k} 가 4L 을 따라가지 않는다"
    print("PASS 파생 자리 10·11 = 4L 사진")


def test_fewer_photos_leaves_rest_empty():
    """9장이 안 되면 앞에서부터만 채우고 나머지는 비워 둔다."""
    s = _session_with_faces(4)
    placed = M._auto_assign_faces(s)
    assert placed == 4
    assert set(s.face_slots) == {"4L", "4R", "7C", "5L"}, s.face_slots
    print("PASS 4장 → 앞 4자리만 채움")


def test_derived_editor_preserves_crop_region():
    """이 기능의 핵심 — 창 크기가 달라도 같은 사진 영역이 나와야 한다."""
    s = _session_with_faces(9)
    M._auto_assign_faces(s)
    st = EditorState(dx_px=41.0, dy_px=-27.0, scale=1.22, angle_deg=0.0)
    s.face_editors["4L"] = st

    src_win = M.CASE_ANCHORS["4L"].window
    dst_win = M.CASE_ANCHORS["10BIG"].window
    assert abs(src_win.w / src_win.h - dst_win.w / dst_win.h) < 1e-3, "비율 전제가 깨졌다"

    wh = (4000, 3000)
    a = _crop_box(wh, src_win, M._face_editor(s, "4L"))
    b = _crop_box(wh, dst_win, M._face_editor(s, "10BIG"))
    worst = max(abs(x - y) for x, y in zip(a, b))
    print(f"  4L    crop={tuple(round(v, 5) for v in a)}")
    print(f"  10BIG crop={tuple(round(v, 5) for v in b)}  최대차={worst:.2e}")
    assert worst < 1e-4, f"파생 자리 구도가 밀렸다 ({worst})"

    # 환산 없이 그대로 썼다면 어긋난다는 것도 같이 못박는다 — 회귀 방지
    naive = _crop_box(wh, dst_win, st)
    assert max(abs(x - y) for x, y in zip(a, naive)) > 1e-3, \
        "환산이 필요 없는 상태라면 이 테스트는 의미가 없다"
    print("PASS 파생 자리 구도 보존 (그대로 썼다면 어긋남도 확인)")


def test_reassign_clears_that_cells_framing():
    s = _session_with_faces(9)
    M._auto_assign_faces(s)
    s.face_editors["4L"] = EditorState(dx_px=10, dy_px=10, scale=1.1, angle_deg=2)
    # 다른 사진을 4L 로 옮기면 그 구도는 다른 사진 기준이라 버려야 한다
    M.face_assign(M.FaceAssignReq(session_id=s.id, cell="4L", photo_id="f5"))
    assert "4L" not in s.face_editors, "사진이 바뀌었는데 구도가 남아 있다"
    print("PASS 자리의 사진이 바뀌면 구도 초기화")


def _fake_face_frame():
    """사진 정중앙을 3:4 로 자른 예측. (모델 없이 기하만 검증하기 위한 것)"""
    import framing as Fr
    k = 3.0 / 2250.0                       # 4000x3000 에서 높이가 짧은 변 → w=2250
    m = [[k, 0.0, -(2000 - 1125) * k], [0.0, k, -(1500 - 1500) * k]]
    return Fr.FramingResult(ok=True, method="fake", scale=k, angle_deg=0.0,
                            crop_frac=0.5625, n_models=1, spread_pct=float("nan"),
                            score=99.0, matrix=m, canon_wh=(3.0, 4.0), corners_raw=[])


def test_face_framing_leaves_no_letterbox(monkeypatch=None):
    """모델 crop(3:4)과 얼굴 자리(0.725)가 어긋나므로 cover 로 끌어올려야 한다.

    끌어올리지 않으면 위아래에 검은 띠가 남는다 — 케이스 발표용 얼굴 사진에서
    가장 눈에 띄는 결함이다. 여기서는 그 두 가지를 같이 못박는다:
      · 예측을 그대로 앉히면 실제로 띠가 생긴다(= clamp 가 하는 일이 있다)
      · _frame_face_cell 이 돌려준 구도에는 띠가 없다
    """
    from coords import cover_base_ext_cm as cbe, min_cover_scale
    s = _session_with_faces(9)
    M._auto_assign_faces(s)

    fake = _fake_face_frame()
    s.face_frames = {p.id: fake for p in s.photos}       # 모델 대신 합성 예측
    s.face_editors.clear()

    win = M.CASE_ANCHORS["4L"].window
    photo = M._photo(s, s.face_slots["4L"])
    bw, bh = cbe(photo.w, photo.h, win)
    need = min_cover_scale(0.0, bw, bh, win)

    raw = M.framing_to_editor(fake, win, photo.w, photo.h)
    assert raw.scale < need - 1e-9, "이 양식에서는 clamp 가 필요 없다 — 테스트 전제 확인 필요"

    how = M._frame_face_cell(s, "4L")
    assert how == "model", how
    got = s.face_editors["4L"]
    assert got.scale >= need - 1e-9, f"레터박스가 남는다 (scale={got.scale} < {need})"
    print(f"PASS 얼굴 프레이밍 cover 보장: 예측 {raw.scale:.4f} → 보정 {got.scale:.4f} "
          f"(필요 {need:.4f})")


def test_face_framing_falls_back_to_cover():
    """모델이 없거나 예측을 기각하면 cover-fit 으로 물러난다."""
    s = _session_with_faces(9)
    M._auto_assign_faces(s)
    s.face_editors.clear()
    s.face_frames = {p.id: None for p in s.photos}       # 모델 없음
    assert M._frame_face_cell(s, "4L") == "cover"
    assert "4L" not in s.face_editors, "실패했는데 구도를 남겼다"
    print("PASS 예측 없음 → cover-fit 으로 물러남")


def test_face_frame_result_is_cached_per_photo():
    """예측은 사진에만 달렸다 — 자리를 바꿔 배치해도 다시 추론하지 않는다.

    여기만 실제 추론 경로를 탄다(model=True). 예측값 자체는 보지 않고,
    "사진당 한 번만 부르고 재배치해도 다시 부르지 않는다"만 확인한다.
    """
    s = _session_with_faces(9, model=True)
    calls = []
    real = M.framer.predict if M.framer else None
    if real:
        M.framer.predict = lambda arr, cls: (calls.append(cls), real(arr, cls))[1]
    try:
        M._auto_assign_faces(s)
        first = len(calls)
        before = dict(s.face_frames)
        assert len(before) == 9, f"사진마다 한 번씩 캐시돼야 한다 ({len(before)})"
        M._auto_assign_faces(s)                          # 다시 배치
        assert s.face_frames == before, "재배치가 캐시를 버렸다"
        assert len(calls) == first, f"재배치에서 다시 추론했다 ({first} → {len(calls)})"
    finally:
        if real:
            M.framer.predict = real
    print(f"PASS 예측 캐시 (사진 9장 · 추론 {first}회, 재배치 시 추가 0회)")


if __name__ == "__main__":
    test_face_framing_leaves_no_letterbox()
    test_face_framing_falls_back_to_cover()
    test_face_frame_result_is_cached_per_photo()
    test_auto_order_matches_spec()
    test_derived_cells_follow_slide4_left()
    test_fewer_photos_leaves_rest_empty()
    test_derived_editor_preserves_crop_region()
    test_reassign_clears_that_cells_framing()
    print("\n✅ 얼굴 덱 테스트 통과")
