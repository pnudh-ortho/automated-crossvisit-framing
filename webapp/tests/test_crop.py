"""
창 굽기(검수 화면 → PPT 픽셀) 검증.

이 기능의 전부는 "구운 그림이 검수 화면과 같은가" 다. 어긋나면 예외가 아니라
**PPT 만 조용히 다르게** 나오므로, 편집기 모델(frontend/app.js: drawComposite +
coverDraw)을 여기서 따로 한 번 더 구현해 두 결과를 대조한다.

실행: cd webapp && python -m pytest tests/test_crop.py -q
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import crop as Cr  # noqa: E402
from coords import EditorState, WindowCm  # noqa: E402

WIN = WindowCm(x=8.5, y=6.38, w=8.4, h=6.3)
PPC = 100.0          # 편집기 배율 (config.geometry.render_px_per_cm)


def _editor_sample(img, win, st, flip_v, u, v, ppc=PPC):
    """편집기 모델대로, 창 안의 비율 좌표 (u,v) 가 원본의 어느 픽셀인지.

    app.js 의 변환을 **거꾸로** 푼 것이다:
        창픽셀 → (창중심+d 빼기) → 회전 −θ → 배율 1/s → 반전 → 1/k → 사진중심 더하기
    """
    ih, iw = img.shape[:2]
    cw, ch = win.w * ppc, win.h * ppc
    k = max(cw / iw, ch / ih)
    x = u * cw - (cw / 2 + st.dx_px)
    y = v * ch - (ch / 2 + st.dy_px)
    t = np.radians(st.angle_deg)
    c, s = np.cos(t), np.sin(t)
    x, y = c * x + s * y, -s * x + c * y          # 회전 −θ
    x, y = x / st.scale, y / st.scale
    if flip_v:
        y = -y
    return (x / k + iw / 2, y / k + ih / 2)


def _checker(w=1200, h=900):
    """어느 픽셀인지 색으로 알 수 있는 그림 — 좌표를 R·G 채널에 새긴다."""
    xx, yy = np.meshgrid(np.arange(w), np.arange(h))
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 2] = (xx * 255 // (w - 1)).astype(np.uint8)   # R = x
    img[..., 1] = (yy * 255 // (h - 1)).astype(np.uint8)   # G = y
    img[..., 0] = 128
    return img


def _compare(st, flip_v, ppcm, label, tol=2.0):
    img = _checker()
    out = Cr.render_window(img, WIN, st, flip_v, ppcm, PPC)
    oh, ow = out.shape[:2]
    assert (ow, oh) == (round(WIN.w * ppcm), round(WIN.h * ppcm)), (ow, oh)

    worst = 0.0
    for u in np.linspace(0.08, 0.92, 6):
        for v in np.linspace(0.08, 0.92, 6):
            sx, sy = _editor_sample(img, WIN, st, flip_v, u, v)
            if not (2 < sx < img.shape[1] - 2 and 2 < sy < img.shape[0] - 2):
                continue                       # 사진 밖(레터박스)은 비교 대상 아님
            want = img[int(round(sy)), int(round(sx))]
            got = out[int(round(v * oh)), int(round(u * ow))]
            # R·G 채널에 좌표가 새겨져 있으니 색 차이가 곧 위치 차이다
            worst = max(worst, abs(int(want[2]) - int(got[2])),
                        abs(int(want[1]) - int(got[1])))
    print(f"  {label:44s} 최대 색차 {worst:.1f}/255")
    assert worst <= tol * 255 / 100, f"{label}: 편집기와 어긋난다 ({worst})"
    return out


def test_cover_fit_matches_editor():
    _compare(EditorState(), False, 200, "cover-fit (배율1·회전0)")


def test_pan_and_zoom_match_editor():
    _compare(EditorState(dx_px=30, dy_px=-12, scale=1.45), False, 200, "이동+확대")


def test_rotation_matches_editor():
    _compare(EditorState(dx_px=10, dy_px=5, scale=1.35, angle_deg=8.0), False, 200,
             "회전 8°")


def test_flip_matches_editor():
    _compare(EditorState(dy_px=-8, scale=1.3, angle_deg=-4.0), True, 200,
             "상하반전(교합면) + 회전 −4°")


def test_output_is_exactly_window_aspect():
    """구운 그림은 창과 같은 비율이어야 한다 — 그래야 cover-fit 이 창에 딱 맞는다."""
    for ppcm in (100, 200, 300):
        out = Cr.render_window(_checker(), WIN, EditorState(), False, ppcm, PPC)
        h, w = out.shape[:2]
        assert abs(w / h - WIN.w / WIN.h) < 2e-3, (ppcm, w, h)
    print("PASS 출력 비율 = 창 비율")


def test_no_overflow_by_construction():
    """구운 그림을 cover-fit 으로 놓으면 창과 정확히 일치한다(삐져나옴 0)."""
    from coords import cover_base_ext_cm, editor_to_placement, emu_to_cm
    out = Cr.render_window(_checker(), WIN, EditorState(scale=1.79), False, 200, PPC)
    h, w = out.shape[:2]
    bw, bh = cover_base_ext_cm(w, h, WIN)
    pl = editor_to_placement(EditorState(), WIN, bw, bh, PPC)
    assert abs(emu_to_cm(pl.off_x) - WIN.x) < 1e-3
    assert abs(emu_to_cm(pl.off_y) - WIN.y) < 1e-3
    assert abs(emu_to_cm(pl.ext_cx) - WIN.w) < 1e-3
    assert abs(emu_to_cm(pl.ext_cy) - WIN.h) < 1e-3
    print("PASS 배율 1.79로 잡아도 도형은 창과 정확히 일치 (이웃 침범 0)")


def test_letterbox_color_fills_uncovered_area():
    """사진이 창을 못 덮으면 그 자리는 설정한 레터박스 색이다."""
    out = Cr.render_window(_checker(), WIN, EditorState(scale=0.5), False, 100, PPC,
                           letterbox_bgr=(0, 0, 0))
    assert out[2, 2].tolist() == [0, 0, 0], out[2, 2]
    print("PASS 빈 자리는 레터박스 색")


def test_hex_to_bgr():
    assert Cr.hex_to_bgr("000000") == (0, 0, 0)
    assert Cr.hex_to_bgr("FFFFFF") == (255, 255, 255)
    assert Cr.hex_to_bgr("FF0000") == (0, 0, 255)      # R → BGR 뒤집힘
    assert Cr.hex_to_bgr("엉터리") == (0, 0, 0)
    print("PASS 색 문자열 해석")


if __name__ == "__main__":
    for f in (test_cover_fit_matches_editor, test_pan_and_zoom_match_editor,
              test_rotation_matches_editor, test_flip_matches_editor,
              test_output_is_exactly_window_aspect, test_no_overflow_by_construction,
              test_letterbox_color_fills_uncovered_area, test_hex_to_bgr):
        f()
    print("\n✅ 창 굽기 테스트 통과")


# ── 확정까지 통째로 ──────────────────────────────────────────────────────────
def test_commit_bakes_images_at_window_size():
    """확정 결과는 창과 정확히 같은 비율·해상도의 이미지들뿐이어야 한다.

    fastest_lap 에는 PPT 가 없다 — 산출물은 저장 폴더의 구운 사진이 전부다.
    구운 파일의 크기가 창 × 출력 해상도와 다르면 검수 화면과 결과물이 어긋난다.
    """
    import io
    import shutil
    from PIL import Image
    from starlette.testclient import TestClient
    import main as M

    client = TestClient(M.app)
    folder = "굽기검사"
    d = M.ROOT / folder
    shutil.rmtree(d, ignore_errors=True)
    try:
        sid = client.post("/api/session", json={"folder": folder}).json()["session_id"]
        blob = cv2.imencode(".jpg", _checker(2400, 1800))[1].tobytes()
        files = [("files", (f"IMG_{i}.jpg", io.BytesIO(blob), "image/jpeg"))
                 for i in range(5)]
        client.post(f"/api/photos/{sid}", files=files)
        client.post(f"/api/classify/{sid}")

        # 합성 이미지라 분류기가 슬롯에 안 넣을 수 있다 — 여기서 보려는 것은
        # 분류가 아니라 굽기이므로 직접 꽂고, 확대·회전을 줘서 조건을 만든다.
        s = M.SESSIONS[sid]
        for slot, photo in zip(M.SLOT_NAMES, s.photos):
            M._put(s, photo, slot, at=0)
            photo.editor = EditorState(dx_px=25, dy_px=-15, scale=1.79, angle_deg=7.0)
        assert len(s.slots) == 5, dict(s.slots)
        r = client.post(f"/api/commit/{sid}", json={"overwrite": []})
        assert r.status_code == 200, r.text
        saved = r.json()["files"]
        assert len(saved) == 5, saved

        outp = M._output_prefs()
        for name in saved:
            p = d / name
            assert p.exists(), name
            assert p.suffix != ".pptx"
            with Image.open(p) as im:
                w, h = im.size
            win = M.SLOT_WINDOWS[M.cfg.slot_by_class[
                next(c for c, a in M._naming_prefs()["aliases"].items() if a in name)]]
            assert abs(w - win.w * outp["px_per_cm"]) <= 1, (name, w)
            assert abs(h - win.h * outp["px_per_cm"]) <= 1, (name, h)
        assert not list(d.glob("*.pptx")), "PPT 가 생겼다 — fastest_lap 위반"
        print(f"PASS 확정 결과: 사진 {len(saved)}장 전부 창 크기로 구워짐, PPT 없음")
    finally:
        shutil.rmtree(d, ignore_errors=True)
