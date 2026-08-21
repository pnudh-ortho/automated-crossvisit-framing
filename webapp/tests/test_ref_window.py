"""정합용 기준 사진은 **자르지 않고 통째로** 창에 넣는다.

기준 사진은 지난 차수의 완성본이고, 정합이 맞춰야 할 목표는 그 프레임 자체다.
그래서 ① 프레이밍 모델을 다시 걸지 않고 ② 저장 비율을 바꿔 종횡비가 어긋나면
잘라내는 대신 여백을 남긴다 — 구내 사진에서 잘려 나가는 것은 대개 후방 치아이고,
기준이 잘리면 이번 차수 저장본까지 좁아져 기록이 차수를 거듭할수록 줄어든다.

실행: cd webapp && python -m pytest tests/test_ref_window.py -q
"""
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402

W, H = 1680, 1260          # 우리가 저장하는 완성본 규격 (4:3)


def _mark_photo(tmp_path):
    """좌우 끝에 표식을 둔 4:3 사진 — 잘리면 표식이 사라진다."""
    arr = np.full((H, W, 3), 120, np.uint8)
    arr[:, :40] = (0, 0, 255)          # 왼쪽 끝: 빨강
    arr[:, -40:] = (0, 255, 0)         # 오른쪽 끝: 초록
    f = tmp_path / "ref.jpg"
    cv2.imencode(".jpg", arr)[1].tofile(str(f))
    return f


def _bake(tmp_path, monkeypatch, ratio):
    monkeypatch.setattr(M, "_flip_defaults", lambda: {"ref": {}, "cur": {}})
    monkeypatch.setattr(M, "_output_prefs", lambda: {
        "px_per_cm": 200.0, "format": "jpg", "jpeg_quality": 95,
        "save_extras": False, "flip_save": True,
        "io_ratio": ratio, "face_ratio": "3:4"})
    s = M.Session()
    p = M.Photo("r", _mark_photo(tmp_path), W, H, "ref")
    p.orig_name, p.label = "ref.jpg", "IO_FRONT"
    s.photos = [p]
    with s.lock:
        M._put(s, p, "SLOT_FRONT", at=0)
    return M._ref_bake(s, "SLOT_FRONT"), s.slot_windows["SLOT_FRONT"]


def _edges_kept(img):
    """좌우 끝 표식이 살아 있나 — 잘렸으면 사라진다."""
    red = (img[:, :, 2] > 200) & (img[:, :, 0] < 60)
    green = (img[:, :, 1] > 200) & (img[:, :, 2] < 60)
    return bool(red.any()), bool(green.any())


def _margins(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return int(((g < 8).mean(axis=0) > 0.98).sum()), int(((g < 8).mean(axis=1) > 0.98).sum())


@pytest.mark.parametrize("ratio", ["4:3", "1:1", "3:2"])
def test_비율을_바꿔도_기준_사진은_잘리지_않는다(_isolate_paths, tmp_path, monkeypatch, ratio):
    img, win = _bake(tmp_path, monkeypatch, ratio)
    assert img.shape[1] == round(win.w * M.PPC)
    assert img.shape[0] == round(win.h * M.PPC)
    assert _edges_kept(img) == (True, True), f"{ratio}: 가장자리가 잘렸다"


def test_기본_비율에서는_여백이_없다(_isolate_paths, tmp_path, monkeypatch):
    """창과 종횡비가 같은 우리 저장본은 여백 없이 정확히 들어맞는다."""
    img, _ = _bake(tmp_path, monkeypatch, "4:3")
    assert _margins(img) == (0, 0)


def test_종횡비가_어긋나면_잘라내지_말고_여백을_남긴다(_isolate_paths, tmp_path, monkeypatch):
    """4:3 사진을 1:1 창에 — 좌우를 버리는 대신 위아래를 비운다."""
    img, _ = _bake(tmp_path, monkeypatch, "1:1")
    cols, rows = _margins(img)
    assert cols == 0 and rows > 0, (cols, rows)
    assert _edges_kept(img) == (True, True)


def test_프레이밍_모델을_다시_걸지_않는다(_isolate_paths, tmp_path, monkeypatch):
    """이미 잘린 사진을 다시 자르면 원본 경계 밖까지 잡아 여백이 생겼다."""
    called = []
    monkeypatch.setattr(M, "_auto_frame",
                        lambda *a, **k: called.append(1))
    img, _ = _bake(tmp_path, monkeypatch, "4:3")
    assert not called, "기준 사진에 프레이밍이 다시 걸렸다"
    assert _margins(img) == (0, 0)
