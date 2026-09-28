"""Fastest Lap 기준 사진 — 원본(raw)을 넣어도 정합되게 프레이밍으로 눈금을 맞춘다.

저장본(창 비율 · 긴 변 2000px 이하)은 그대로(contain), 원본(3:2 · 6000px)은
오늘 사진과 같은 프레이밍 모델로 잘라 같은 배율에 올린다. 모델이 없거나 기각하면
contain 으로 물러선다.

실행: cd webapp && python -m pytest tests/test_fastlap_ref_frame.py -q
"""
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import fastlap as FL  # noqa: E402
import main as M  # noqa: E402
from coords import EditorState, WindowCm  # noqa: E402

WIN = WindowCm(0.0, 0.0, 8.4, 6.3)


def test_saved_crop_is_recognised_and_raw_is_not():
    assert FL._looks_saved(1679, 1259, WIN)           # 저장본 (4:3 · 200px/cm)
    assert FL._looks_saved(840, 630, WIN)
    assert not FL._looks_saved(6000, 4000, WIN)       # 카메라 원본 3:2
    assert not FL._looks_saved(4000, 3000, WIN)       # 비율은 맞지만 원본 크기
    assert FL._looks_saved(0, 0, WIN)                 # 크기를 모르면 건드리지 않는다


def _photo(w, h, label="IO_FRONT", flip=False):
    p = types.SimpleNamespace(id="p1", w=w, h=h, label=label, flip_v=flip)
    return p


def test_saved_reference_stays_contain(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "framer", types.SimpleNamespace(
        has=lambda l: True, predict=lambda a, l: calls.append(l)))
    st = FL._ref_state(_photo(1679, 1259), WIN, np.zeros((10, 10, 3), np.uint8))
    assert calls == [], "저장본에는 프레이밍 모델을 걸지 않는다"
    assert st == FL._contain_state(1679, 1259, WIN)


def test_raw_reference_uses_framing_model(monkeypatch):
    res = types.SimpleNamespace(ok=True)
    want = EditorState(12.0, -3.0, 1.4, 0.0)
    monkeypatch.setattr(M, "framer", types.SimpleNamespace(has=lambda l: True,
                                                            predict=lambda a, l: res))
    monkeypatch.setattr(M, "framing_to_editor", lambda r, win, pw, ph: want)
    st = FL._ref_state(_photo(6000, 4000), WIN, np.zeros((10, 10, 3), np.uint8))
    assert st == want
    # 반전 사진은 결과 좌표를 반전 프레임으로 옮긴다 (오늘 사진과 같은 규약)
    st2 = FL._ref_state(_photo(6000, 4000, flip=True), WIN, np.zeros((10, 10, 3), np.uint8))
    assert st2 == M.flip_editor_v(want)


def test_raw_reference_falls_back_to_contain_when_model_rejects(monkeypatch):
    monkeypatch.setattr(M, "framer", types.SimpleNamespace(
        has=lambda l: True, predict=lambda a, l: types.SimpleNamespace(ok=False)))
    st = FL._ref_state(_photo(6000, 4000), WIN, np.zeros((10, 10, 3), np.uint8))
    assert st == FL._contain_state(6000, 4000, WIN)
    monkeypatch.setattr(M, "framer", None)
    assert FL._ref_state(_photo(6000, 4000), WIN, None) == FL._contain_state(6000, 4000, WIN)
