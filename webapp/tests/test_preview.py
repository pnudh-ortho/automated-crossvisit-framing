"""
'PPT에서 확인하기' — 확정 전 미리보기 (2026-08-21)

여기서 못박는 것은 셋이다.

1. **미리보기는 환자 폴더를 건드리지 않는다.** 확정 저장 전까지 기록은 하나도
   생기면 안 된다. 이게 깨지면 미리보기라는 말 자체가 거짓이 된다.
2. **미리보기와 확정이 같은 것을 만든다.** 둘은 `_compose_deck` 하나를 함께 쓰는데,
   누가 나중에 한쪽만 고치면 사람은 A를 보고 B를 저장하게 된다.
3. **여러 번 눌러도 서로 다른 파일에 만든다.** 앞서 연 미리보기를 파워포인트가
   붙들고 있어도 다음 미리보기가 막히면 안 된다(윈도우는 열린 파일을 못 덮는다).

실행: cd webapp && python -m pytest tests/test_preview.py -q
"""
import io
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import case_deck as CD          # noqa: E402
import main                     # noqa: E402
import template as T            # noqa: E402

# 아래 `_no_launch` 는 autouse 라 이 파일의 **모든** 테스트에서 `_os_open` 이
# 가짜로 바뀐다. 진짜 동작을 봐야 하는 테스트를 위해 원본을 미리 붙들어 둔다.
REAL_OS_OPEN = main._os_open

SLOTS = ["front", "left", "right", "upper", "lower"]
NAME, HOSP, ORTHO = "미리보기환자", "123456789", "54321"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _no_launch(monkeypatch):
    """운영체제 기본 프로그램을 실제로 띄우지 않는다 — 연 경로만 받아 둔다."""
    opened: list[Path] = []
    monkeypatch.setattr(main, "_os_open", lambda p: opened.append(Path(p)))
    return opened


def _synth(slot, seed):
    rng = np.random.default_rng(seed)
    img = np.full((1200, 1600, 3), 25, np.uint8)
    for _ in range(150):
        c = tuple(int(x) for x in rng.integers(60, 255, 3))
        p = tuple(int(x) for x in rng.integers(0, [1600, 1200], 2))
        cv2.circle(img, p, int(rng.integers(8, 30)), c, -1)
    cv2.putText(img, slot, (500, 620), cv2.FONT_HERSHEY_SIMPLEX, 5, (255, 255, 255), 10)
    return cv2.imencode(".jpg", img)[1].tobytes()


SLOT_KEYS = ["SLOT_FRONT", "SLOT_LEFT", "SLOT_RIGHT", "SLOT_UPPER", "SLOT_LOWER"]


def _upload(client, sid):
    """사진 다섯 장을 올리고 다섯 자리에 하나씩 앉힌다.

    분류 모델 가중치가 없는 환경에서도 돌아야 하므로 자리는 손으로 배정한다 —
    여기서 보려는 것은 분류가 아니라 미리보기다.
    """
    files = [("files", (f"{s}.jpg", io.BytesIO(_synth(s, 100 + i)), "image/jpeg"))
             for i, s in enumerate(SLOTS)]
    r = client.post(f"/api/upload/{sid}", files=files)
    assert r.status_code == 200, r.text
    photos = r.json()["photos"]
    assert len(photos) == len(SLOT_KEYS)
    for photo, slot in zip(photos, SLOT_KEYS):
        a = client.post("/api/assign", json={"session_id": sid,
                                             "photo_id": photo["id"],
                                             "slot": slot, "at": 0})
        assert a.status_code == 200, a.text
    return r


def _first_session(client):
    r = client.post("/api/session/first",
                    json={"name": NAME, "hospital_id": HOSP, "ortho_id": ORTHO})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    _upload(client, sid)
    return sid


def _visit_labels(prs) -> list[str]:
    """덱 안의 차수 라벨 전부 — 양식 장수는 덱마다 다르므로 '몇 장'이 아니라
    '무슨 라벨이 몇 개' 로 본다.

    라벨 상자 이름은 덱에 따라 다르다: 십자뷰 양식은 INFO_BOX, 케이스 덱은
    NOTE_DATE 가 그 자리를 겸한다. 둘 다 본다.
    """
    out = []
    for sl in prs.slides:
        for nm in (main.cfg.ppt.info_box_name, CD.NOTE_DATE):
            box = T.find_shape(sl, nm)
            if box is not None and box.text_frame.text.strip():
                out.append(box.text_frame.text.strip())
    return out


def _texts(prs) -> list[dict]:
    """장마다 {도형이름: 글}. 미리보기와 확정이 같은지 볼 때 쓴다."""
    return [{sh.name: sh.text_frame.text for sh in sl.shapes if sh.has_text_frame}
            for sl in prs.slides]


def _preview(client, sid):
    r = client.post(f"/api/preview/{sid}")
    assert r.status_code == 200, r.text
    return Path(r.json()["path"])


def test_preview_leaves_patient_folder_untouched(client, _no_launch):
    """미리보기를 아무리 눌러도 환자 폴더에는 아무것도 생기지 않는다."""
    sid = _first_session(client)
    patient_dir = main.ROOT / f"{NAME}_{HOSP}_{ORTHO}"

    for _ in range(2):
        _preview(client, sid)
        assert not patient_dir.exists(), "미리보기가 환자 폴더를 만들었다"

    # 확정하고 나서야 생긴다
    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    assert patient_dir.is_dir()


def test_preview_opens_a_real_deck(client, _no_launch):
    """열어 주는 파일은 실제로 열리는 PPT이고, 이번 차수 라벨을 달고 있다."""
    sid = _first_session(client)
    made = _preview(client, sid)

    assert made.exists() and made.suffix == ".pptx"
    assert made.name.startswith("[미리보기] "), made.name
    assert _no_launch == [made], "만든 파일을 그대로 열어야 한다"

    labels = _visit_labels(T.load_presentation(made))
    assert any("초진" in x for x in labels), labels


def test_preview_matches_commit(client, _no_launch):
    """미리보기와 확정 저장의 슬라이드가 같다 — 장수·라벨·사진 도형 이름.

    바이트까지 같을 수는 없다(사진 파일 경로가 다르다). 같아야 하는 것은 사람이
    미리보기에서 판단한 내용 — 몇 장인지, 무슨 차수인지, 어느 자리에 사진이
    들어갔는지 — 이다.
    """
    sid = _first_session(client)
    pre = T.load_presentation(_preview(client, sid))

    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    patient_dir = Path(r.json()["patient_dir"])
    post = T.load_presentation(patient_dir / r.json()["ppt"])

    assert len(pre.slides) == len(post.slides)
    for a, b in zip(pre.slides, post.slides):
        assert sorted(sh.name for sh in a.shapes) == sorted(sh.name for sh in b.shapes)
    assert _texts(pre) == _texts(post)
    labels = _visit_labels(pre)
    assert labels and labels == _visit_labels(post), labels


def test_preview_on_revisit_keeps_original_deck(client, _no_launch):
    """재진 미리보기는 기존 덱을 늘리지 않는다 — 원본은 그대로 1장이다."""
    sid = _first_session(client)
    r = client.post(f"/api/commit/{sid}")
    assert r.status_code == 200, r.text
    patient_dir = Path(r.json()["patient_dir"])
    deck = patient_dir / r.json()["ppt"]
    before = deck.read_bytes()

    folder = patient_dir.name
    r = client.post("/api/session/revisit",
                    json={"ppt_path": f"{folder}/{deck.name}"})
    assert r.status_code == 200, r.text
    sid2 = r.json()["session_id"]
    _upload(client, sid2)

    was = len(T.load_presentation(deck).slides)
    made = T.load_presentation(_preview(client, sid2))
    assert len(made.slides) == was + 1, "미리보기에 이번 차수 장이 없다"
    assert any("B" in x for x in _visit_labels(made)), _visit_labels(made)
    assert deck.read_bytes() == before, "미리보기가 원본 덱을 고쳤다"

    # 확정하고 나서야 원본이 한 장 늘어난다
    assert client.post(f"/api/commit/{sid2}").status_code == 200
    assert len(T.load_presentation(deck).slides) == was + 1


def test_preview_uses_a_fresh_file_each_time(client, _no_launch):
    """두 번 누르면 두 파일 — 앞서 연 것을 파워포인트가 붙들고 있어도 막히지 않는다."""
    sid = _first_session(client)
    a, b = _preview(client, sid), _preview(client, sid)
    assert a != b and a.exists() and b.exists()


# ── 여는 명령 만들기 ─────────────────────────────────────────────────────────
#
# 실측에서 두 가지가 동시에 물렸다. WSL 에서만 나던 것이라 파이썬 테스트로는
# 안 잡히고, 눌러도 **아무 일도 일어나지 않는** 모습으로만 보였다.
#
#   ① `-Command` 뒤에 경로를 인자로 넘기면 PowerShell 이 공백으로 이어 붙여
#      한 줄을 만든다 — 파일명에 공백이 있으면 거기서 쪼개져 실패한다.
#   ② 대괄호는 PowerShell 의 와일드카드다. `[미리보기]` 가 붙은 이름은 따옴표를
#      씌워도 `-Path` 로는 못 찾는다.

def test_open_command_quotes_paths_with_spaces():
    """공백이 있어도 한 덩어리로 넘어간다 — 안 그러면 명령이 통째로 실패한다."""
    cmd = main._ps_open_command(r"C:\내 자료\김하늘 (재진).pptx")
    assert r"'C:\내 자료\김하늘 (재진).pptx'" in cmd


def test_open_command_treats_brackets_literally():
    """`[미리보기]` 는 와일드카드가 아니라 글자다 — -LiteralPath 여야 한다."""
    cmd = main._ps_open_command(r"C:\x\[미리보기] 김하늘.pptx")
    assert "-LiteralPath" in cmd
    assert "-Path " not in cmd


def test_open_command_escapes_quotes():
    """작은따옴표가 든 이름이 문자열을 끊고 나오지 않는다."""
    cmd = main._ps_open_command(r"C:\x\it's here.pptx")
    assert "'C:\\x\\it''s here.pptx'" in cmd


def test_open_reports_failure_instead_of_going_quiet(monkeypatch, tmp_path):
    """열지 못하면 **말을 한다.** 조용히 넘어가면 '눌렀는데 반응이 없다' 가 된다."""
    monkeypatch.setattr(main.shutil, "which", lambda n: None)   # 열 프로그램이 없다
    with pytest.raises(OSError):
        REAL_OS_OPEN(tmp_path / "x.pptx")


def test_falls_back_to_the_folder_when_the_file_will_not_open(client, monkeypatch):
    """파일을 못 열면 **폴더라도** 연다 — 만들어 둔 것을 못 보고 끝나면 안 된다."""
    sid = _first_session(client)
    revealed = []
    monkeypatch.setattr(main, "_os_open",
                        lambda p: (_ for _ in ()).throw(OSError("연결 프로그램 없음")))
    monkeypatch.setattr(main, "_os_reveal", lambda p: revealed.append(Path(p)))

    r = client.post(f"/api/preview/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["opened"] == "folder"
    assert revealed and revealed[0].name.startswith("[미리보기] ")


def test_preview_surfaces_failure_when_even_the_folder_will_not_open(client, monkeypatch):
    """둘 다 막히면 화면에 오류가 뜬다 — 성공한 척하지 않는다."""
    sid = _first_session(client)
    boom = lambda p: (_ for _ in ()).throw(OSError("열 수 없음"))
    monkeypatch.setattr(main, "_os_open", boom)
    monkeypatch.setattr(main, "_os_reveal", boom)
    r = client.post(f"/api/preview/{sid}")
    assert r.status_code == 500
    assert "열지" in r.text
