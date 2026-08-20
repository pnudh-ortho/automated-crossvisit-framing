"""교합면(상·하악)은 **설정이 선언한 방향으로 맞춰** 읽는다.

분류기는 거울 촬영 원본으로 배웠고, 치열이 열린 방향을 상당히 본다. 그래서
이미 뒤집어 저장한 사진(정합용 기준 풀이 그렇다)을 그대로 넣으면 상·하악이
뒤바뀐다 — 실측에서 6장 중 4장이 뒤집혔고 둘은 0.98 로 자신 있게 틀렸다.

설정의 상하반전 값이 곧 "이 사진이 거울 원본인가"라는 선언이므로, 꺼져 있는
풀에서만 뒤집어 읽는다. 화면·저장 방향은 설정 그대로다.

실행: cd webapp && python -m pytest tests/test_occlusal.py -q
"""
import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402

UP, LO, FACE = "IO_UPPER", "IO_LOWER", "FACE"


class _Pred:
    def __init__(self, label, probs):
        self.label, self.probs = label, probs
        self.confidence = probs[label]


class _Stub:
    """학습 방향(위가 밝음)에서는 맞히고, 뒤집힌 방향에서는 **자신 있게 틀린다**.

    실측 그대로다. 참 라벨은 사진 폭으로 준다 — 뒤집어도 폭은 그대로라
    시험이 성립한다. 폭 102 는 방향을 안 타는 클래스(FACE)다.
    """

    def predict(self, im, filename=""):
        if im.size[0] == 102:
            return _Pred(FACE, {FACE: 0.99})
        a = np.asarray(im.convert("L"), float)
        h = a.shape[0] // 2
        trained = a[:h].mean() > a[h:].mean()          # 위가 밝다 = 학습 방향
        true = UP if im.size[0] == 100 else LO
        other = LO if true == UP else UP
        probs = ({true: 0.99, other: 0.01} if trained
                 else {other: 0.98, true: 0.02})
        return _Pred(max(probs, key=probs.get), probs)


def _photo(tmp_path, tag, true_label, mirrored, pool):
    """위/아래 밝기로 방향을, 폭으로 참 라벨을 인코딩한 시험용 사진.

    mirrored=True 는 거울 원본(=학습 방향), False 는 이미 뒤집어 저장한 사진.
    """
    w = {UP: 100, LO: 101, FACE: 102}[true_label]
    arr = np.zeros((80, w), np.uint8)
    arr[slice(0, 40) if mirrored else slice(40, None), :] = 240
    f = tmp_path / f"{tag}.jpg"
    Image.fromarray(arr).convert("RGB").save(f)
    p = M.Photo(tag, f, w, 80, pool)
    p.orig_name = f.name
    return p


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(M, "classifier", _Stub())


def _run(tmp_path, pool, mirrored, labels=(UP, LO)):
    s = M.Session()
    s.photos = [_photo(tmp_path, f"p{i}", lab, mirrored, pool)
                for i, lab in enumerate(labels)]
    M._classify(s, s.photos)
    return s


def test_이미_반전된_기준_사진은_뒤집어_읽는다(_isolate_paths, tmp_path, stub):
    """기준 풀은 상하반전이 꺼져 있다 = 이미 뒤집힌 사진이라는 선언."""
    assert not M._occlusal_mirrored("ref"), "기준 풀 기본값은 반전 꺼짐"
    s = _run(tmp_path, "ref", mirrored=False)
    assert [p.label for p in s.photos] == [UP, LO]
    assert min(p.confidence for p in s.photos) > 0.9
    # 화면·저장 방향은 설정 그대로 — 뒤집지 않는다
    assert not any(p.flip for p in s.photos)
    assert set(s.ref_bins) == {"SLOT_UPPER", "SLOT_LOWER"}


def test_현재_사진은_거울_원본이라_그대로_읽는다(_isolate_paths, tmp_path, stub):
    """현재 풀은 상하반전이 켜져 있다 = 거울로 찍은 원본이라는 선언."""
    assert M._occlusal_mirrored("cur"), "현재 풀 기본값은 교합면 반전 켜짐"
    s = _run(tmp_path, "cur", mirrored=True)
    assert [p.label for p in s.photos] == [UP, LO]
    # 거울 원본이므로 화면에서는 뒤집어 보여준다
    assert all(p.flip for p in s.photos)


def test_방향을_안_타는_클래스는_다시_읽지_않는다(_isolate_paths, tmp_path, stub):
    """FACE·정면·측방은 뒤집어도 라벨이 그대로였다 — 두 번 돌릴 이유가 없다."""
    s = _run(tmp_path, "ref", mirrored=False, labels=(FACE,))
    assert [p.label for p in s.photos] == [FACE]
    assert not s.photos[0].flip


def test_설정을_켜면_기준_풀도_그대로_읽는다(_isolate_paths, tmp_path, stub, monkeypatch):
    """기준으로 거울 원본을 올리는 사람도 있다 — 설정이 그 선언을 담는다."""
    monkeypatch.setattr(M, "_flip_defaults",
                        lambda: {"ref": {UP: True, LO: True}, "cur": {}})
    assert M._occlusal_mirrored("ref")
    s = _run(tmp_path, "ref", mirrored=True)
    assert [p.label for p in s.photos] == [UP, LO]
    assert all(p.flip for p in s.photos)
