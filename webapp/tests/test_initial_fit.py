"""
initial-fit — '자동으로 잡아 준 첫 구도' 로 되돌리기 (2026-08-21)

되돌리기 버튼이 예전에는 무조건 cover-fit(가운데·무회전)으로 갔다. 그러면 손이
미끄러져 되돌릴 때 **차수 간 정합까지 함께 버려져서**, 그 자리를 다시 잡으려면
눈대중으로 맞추는 수밖에 없었다. 이제는 `_frame` 이 잡아 준 값(재진=정합,
초진=프레이밍 예측, 둘 다 못 쓰면 cover-fit)으로 돌아간다.

여기서 못박는 것은 둘이다.

1. **초기 구도는 계산이 끝난 그 값이다** — `_frame` 직후 editor 와 같다.
2. **조작으로는 절대 바뀌지 않는다** — 사람이 아무리 움직여도, 다시 검수로
   들어와도 되돌아갈 자리는 그대로다. 이게 깨지면 되돌리기가 '방금 만진 값으로
   되돌리기' 가 되어 아무 일도 하지 않는다.

실행: cd webapp && python -m pytest tests/test_initial_fit.py -q
"""
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from coords import EditorState                                # noqa: E402
import main                                                    # noqa: E402
from test_preview import SLOT_KEYS, _first_session             # noqa: E402

# 이 환경에는 프레이밍 모델 가중치가 없어 자동 구도가 전부 cover-fit 으로 떨어진다.
# 그러면 "initial-fit 이 cover-fit 과 다르다" 를 값으로 볼 수 없으므로, 모델이
# 있었다면 나왔을 값을 직접 심어 배선을 확인한다.
FRAMED = EditorState(dx_px=12.5, dy_px=-8.0, scale=1.14, angle_deg=2.5)


@pytest.fixture
def client():
    return TestClient(main.app)


def _review(client, sid, force=False):
    r = client.post(f"/api/register/{sid}", json={"force": force})
    assert r.status_code == 200, r.text
    return r.json()["review"]


def _slot_editors(review):
    return {k: (v["editor"], v["editor0"]) for k, v in review["slots"].items() if v}


def test_initial_fit_is_the_framed_value(client):
    """정합·프레이밍이 끝난 직후에는 initial-fit 이 곧 현재 구도다."""
    sid = _first_session(client)
    pairs = _slot_editors(_review(client, sid))
    assert len(pairs) == len(SLOT_KEYS)
    for slot, (cur, init) in pairs.items():
        assert cur == init, slot


def test_framing_result_lands_in_initial_fit(client, monkeypatch):
    """자동 구도가 잡아 준 값이 그대로 initial-fit 이 된다 — cover-fit 이 아니라.

    모델이 없는 환경이라 `_auto_frame` 을 가로채 '모델이 이렇게 잡았다' 고
    흉내낸다. 보려는 것은 모델의 정확도가 아니라 **그 결과가 되돌리기의 자리로
    이어지는가** 하나다.
    """
    def fake(s, photo, win, fallback_badge=None, bgr=None):
        photo.editor = FRAMED
        photo.framing = "model"

    monkeypatch.setattr(main, "_auto_frame", fake)
    sid = _first_session(client)
    # 반전은 `_auto_frame` 안에서 이미 끝난다 — 자리가 정해진 뒤에 구도를 잡으므로
    # 여기 나오는 값은 어느 자리든 그 자리의 프레임 기준 그대로다.
    want = {"dx": 12.5, "dy": -8.0, "scale": 1.14, "angle": 2.5}
    pairs = _slot_editors(_review(client, sid))
    assert len(pairs) == len(SLOT_KEYS)
    for slot, (cur, init) in pairs.items():
        assert init == want, (slot, init)
        assert cur == init, slot


def test_editing_never_moves_initial_fit(client):
    """사람이 만져도 되돌아갈 자리는 그대로다 — 되돌리기가 뜻을 잃지 않게."""
    sid = _first_session(client)
    before = _slot_editors(_review(client, sid))
    slot = SLOT_KEYS[0]
    init = before[slot][1]

    moved = {"dx": 37.0, "dy": -21.0, "scale": 1.18, "angle": 2.5}
    r = client.post("/api/adjust", json={"session_id": sid, "slot": slot, **moved})
    assert r.status_code == 200, r.text

    after = _slot_editors(_review(client, sid))
    cur, init2 = after[slot]
    assert init2 == init, "조작이 initial-fit 을 밀었다"
    assert cur != init2, "테스트가 실제로 구도를 움직이지 못했다"
    # 손대지 않은 자리는 현재 구도까지 그대로여야 한다(_frame 이 건너뛴다)
    for other in SLOT_KEYS[1:]:
        assert after[other] == before[other], other


def test_reframing_refreshes_initial_fit(client):
    """force 로 다시 계산하면 initial-fit 도 그 결과로 갱신된다.

    되돌리기가 가리키는 곳은 '맨 처음 한 번' 이 아니라 **지금 자동 계산의 결과**다.
    좌·우를 바꿔 넣고 돌아오면 그 자리는 새 기준으로 다시 잡히기 때문이다.
    """
    sid = _first_session(client)
    slot = SLOT_KEYS[0]
    client.post("/api/adjust", json={"session_id": sid, "slot": slot,
                                     "dx": 40.0, "dy": 0.0, "scale": 1.2, "angle": 3.0})
    cur, init = _slot_editors(_review(client, sid, force=True))[slot]
    assert cur == init, "다시 계산했는데 두 값이 갈라졌다"


def test_face_cells_carry_their_own_initial_fit(client):
    """얼굴 자리도 같은 규약 — 자리마다 initial-fit 을 따로 들고 있다."""
    sid = _first_session(client)
    review = _review(client, sid)
    assert "face_editors0" in review
    # 얼굴 사진이 없는 세션이라 표는 비어 있지만, 규약 자체는 내려와야 한다
    assert isinstance(review["face_editors0"], dict)


def test_initial_fit_survives_a_slot_move(client):
    """교합면을 다른 자리로 옮겨도 되돌아갈 자리가 뒤집히지 않는다.

    교합면은 사람이 상하반전된 화면으로 본다. 옮길 때 현재 구도만 그 프레임으로
    옮기고 initial-fit 을 두고 오면, 되돌리기가 위아래가 뒤집힌 구도를 불러온다.
    """
    sid = _first_session(client)
    review = _review(client, sid)
    upper = review["slots"]["SLOT_UPPER"]
    assert upper["flip_v"] is True, "교합면은 반전 프레임이어야 한다"
    # 모델이 없어 초기 구도가 0 이면 부호를 봐도 아무것도 알 수 없다 — 심어 둔다
    photo = next(p for p in main.SESSIONS[sid].photos if p.id == upper["id"])
    photo.editor0 = FRAMED
    init_flipped = {"dx": FRAMED.dx_px, "dy": FRAMED.dy_px,
                    "scale": FRAMED.scale, "angle": FRAMED.angle_deg}

    # 반전하지 않는 자리로 옮긴다 → dy·angle 부호가 뒤집혀야 한다
    r = client.post("/api/assign", json={"session_id": sid, "photo_id": upper["id"],
                                         "slot": "SLOT_FRONT", "at": 0})
    assert r.status_code == 200, r.text
    moved = next(p for p in r.json()["photos"] if p["id"] == upper["id"])
    assert moved["flip_v"] is False
    assert moved["editor0"]["dy"] == pytest.approx(-init_flipped["dy"])
    assert moved["editor0"]["angle"] == pytest.approx(-init_flipped["angle"])
    assert moved["editor0"]["dx"] == pytest.approx(init_flipped["dx"])
