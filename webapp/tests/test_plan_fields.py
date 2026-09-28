"""`/api/plan` 의 검토용 사실 — 저장 검토 화면이 "무엇을 근거로 어디에 끼우나" 를 보인다.

전부 읽기 전용 추가다. 초진과 재진 모양의 세션 둘 다에서 열쇠가 있고 값의 뜻이
맞는지 본다.

실행: cd webapp && python -m pytest tests/test_plan_fields.py -q
"""
import io
import os
import shutil
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main                                                      # noqa: E402
from test_preview import SLOT_KEYS, SLOTS, _first_session, _synth   # noqa: E402
from test_visit_confirm import FOLDER, PPT, _deck                # noqa: E402

TOP = {"visit", "mode", "prev_visits", "ref_visit", "insert_after", "ppt_slides", "label",
       "extras", "extra_dir"}
PER_SLOT = {"editor", "flip_v", "ref_visit", "initial"}


class _Pred:
    def __init__(self, label):
        self.label, self.confidence, self.probs = label, 0.99, {label: 0.99}


@pytest.fixture
def client():
    return TestClient(main.app)


def _filled(plan):
    return [e for e in plan["slots"] if not e["empty"]]


def test_expired_session_is_410_with_detail(client):
    """화면이 '세션 만료' 를 알아보는 표지 — 상태 410 과 `detail` 한 줄."""
    r = client.get("/api/plan/no-such-session")
    assert r.status_code == 410
    assert "detail" in r.json() and "세션" in r.json()["detail"]


def test_first_visit_plan_carries_the_review_facts(client):
    sid = _first_session(client)
    p = client.get(f"/api/plan/{sid}").json()
    assert TOP <= set(p)
    assert p["mode"] == "first" and p["visit"] == "A"
    assert p["prev_visits"] == [] and p["ref_visit"] is None
    assert p["insert_after"] is None and p["ppt_slides"] is None
    assert "초진 A" in p["label"]
    for e in _filled(p):
        assert PER_SLOT <= set(e), e["slot"]
        assert set(e["editor"]) == {"dx", "dy", "scale", "angle"}
        assert isinstance(e["flip_v"], bool)
        assert e["initial"] in ("registered", "model", "cover", "manual")
        assert e["flip_v"] == (e["slot"] in main.cfg.flip_v_slots)
    assert len(_filled(p)) == 5


@pytest.fixture
def revisit(monkeypatch):
    """차수 A·B 가 든 덱을 가진 환자. 분류기는 전부 OTHERS 로 답해 자리는 손으로 준다."""
    class Stub:
        def predict(self, im, filename=""):
            return _Pred("OTHERS")
    monkeypatch.setattr(main, "classifier", Stub())
    d = main.ROOT / FOLDER
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    _deck(d / PPT, ["24.06.05 (초진 A)", "24.09.04 (재진 B)"])
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_revisit_plan_carries_the_review_facts(client, revisit):
    r = client.post("/api/session", json={"folder": FOLDER})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    files = [("files", (f"{s}.jpg", io.BytesIO(_synth(s, 500 + i)), "image/jpeg"))
             for i, s in enumerate(SLOTS)]
    photos = client.post(f"/api/upload/{sid}", files=files).json()["photos"]
    for photo, slot in zip(photos, SLOT_KEYS):
        client.post("/api/assign", json={"session_id": sid, "photo_id": photo["id"],
                                         "slot": slot, "at": 0})
    p = client.get(f"/api/plan/{sid}").json()
    assert TOP <= set(p)
    assert p["mode"] == "revisit" and p["visit"] == "C"
    assert p["prev_visits"] == ["A", "B"]
    assert p["ppt_slides"] == 2
    assert p["insert_after"] == 2, "기본은 마지막 차수 장(2번) 뒤"
    assert "재진 C" in p["label"]
    for e in _filled(p):
        assert PER_SLOT <= set(e), e["slot"]
        assert e["initial"] == "manual"      # 아직 정합 전 — 아무것도 잡아 주지 않았다
        assert e["ref_visit"] is None

    # 확인 줄에서 자리를 고치면 그 값이 실린다
    r = client.post("/api/session", json={"folder": FOLDER, "insert_after": 1})
    p = client.get(f"/api/plan/{r.json()['session_id']}").json()
    assert p["insert_after"] == 1
