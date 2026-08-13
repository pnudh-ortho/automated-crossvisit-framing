"""
차수 노트 검증.

슬라이드의 텍스트 박스를 직접 타이핑하지 않고, 칸을 채우면 서버가 서식에 끼워
박스 텍스트를 만든다. 여기서 확인하는 것:

  - 비워 둔 칸이 만든 줄은 통째로 빠지는가 ("U: " 같은 껍데기를 안 남기는가)
  - 모르는 칸은 거절하는가
  - 확정하면 십자뷰 슬라이드의 박스에 실제로 적히는가
  - 아무것도 안 채우면 양식의 원래 문구를 건드리지 않는가

실행: cd webapp && python -m pytest tests/test_notes.py -q
"""
import io
import os
import shutil
import sys

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import case_deck as CD  # noqa: E402
import main as M  # noqa: E402
import naming as N  # noqa: E402
import template as T  # noqa: E402

client = TestClient(M.app)

NAME, HOSP, ORTHO = "노트검사", "111222666", "54324"
IDS = N.Identifiers(NAME, HOSP, ORTHO)
FOLDER = N.folder_name(IDS, M.cfg.naming.folder_pattern)


@pytest.fixture
def patient():
    d = M.ROOT / FOLDER
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sid(patient):
    s = client.post("/api/session", json={"folder": FOLDER}).json()["session_id"]
    img = np.full((1200, 900, 3), 40, np.uint8)
    cv2.putText(img, "x", (100, 600), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
    blob = cv2.imencode(".jpg", img)[1].tobytes()
    client.post(f"/api/photos/{s}", files=[("files", ("a.jpg", io.BytesIO(blob), "image/jpeg"))])
    client.post(f"/api/classify/{s}")
    return s


def test_fields_are_declared(sid):
    js = client.get(f"/api/notes/{sid}").json()
    keys = [f["key"] for f in js["fields"]]
    assert "wire_u" in keys and "subj" in keys and "next" in keys
    # preview 는 서식 박스 + 날짜 칸이다. 날짜는 서식이 아니라 차수 라벨에서
    # 오지만, 검수 화면 오버레이가 네 칸을 한자리에서 보여줘야 해서 같이 나간다.
    assert set(js["preview"]) == set(M.cfg.notes.boxes) | {"NOTE_DATE"}


def test_empty_fields_drop_their_lines():
    """빈 칸이 만든 줄은 사라져야 한다 — 슬라이드에 'U: '만 남으면 안 된다."""
    out = M._render_notes({"wire_u": "018 NT", "tx_period": "3 month"})
    status = out["NOTE_STATUS"]
    assert "U: 018 NT" in status
    assert "L:" not in status                 # 하악은 안 채웠다
    assert "Tx. Period: 3 month" in status
    assert "Rx. Period" not in status         # 안 채웠다
    assert "App. Period" not in status

    # 아무것도 안 채우면 내용이 없다 — 커밋이 박스를 건드리지 않는 신호.
    # 서식의 빈 줄은 그대로 남으므로(양식의 글 시작 높이) 공백을 걷어내고 본다.
    assert all(not v.strip() for v in M._render_notes({}).values())


def test_whitespace_only_counts_as_empty():
    out = M._render_notes({"wire_u": "   ", "wire_l": "016 NT"})
    assert "U:" not in out["NOTE_STATUS"]
    assert "L: 016 NT" in out["NOTE_STATUS"]


def test_unknown_field_rejected(sid):
    r = client.post("/api/notes", json={"session_id": sid, "values": {"헛소리": "x"}})
    assert r.status_code == 400


def test_values_round_trip(sid):
    r = client.post("/api/notes", json={"session_id": sid,
                                        "values": {"subj": "n/s", "plan": "AWC"}})
    js = r.json()
    assert js["values"]["subj"] == "n/s"
    assert js["preview"]["NOTE_SOAP"] == "s) n/s\np) AWC"
    # 다시 읽어도 남아 있다
    assert client.get(f"/api/notes/{sid}").json()["values"]["plan"] == "AWC"


@pytest.mark.skipif(not M.CASE_ANCHORS, reason="케이스 양식이 없습니다")
def test_commit_writes_notes_onto_cross_slide(sid, patient):
    client.post("/api/notes", json={"session_id": sid, "values": {
        "wire_u": "018 NT /c loose cinch", "tx_period": "0 month",
        "subj": "n/s", "plan": "bonding", "next": "AWC",
    }})
    assert client.post(f"/api/commit/{sid}?allow_missing=true").status_code == 200

    prs = T.load_presentation(patient / N.ppt_filename(IDS, M.cfg.naming.ppt_pattern))
    cross = prs.slides[CD.cross_slide_index(prs)]
    assert CD.get_note_text(cross, CD.NOTE_SOAP) == "s) n/s\np) bonding"
    assert CD.get_note_text(cross, CD.NOTE_NEXT) == "n) AWC"
    status = CD.get_note_text(cross, CD.NOTE_STATUS)
    assert "U: 018 NT /c loose cinch" in status and "L:" not in status
    # 차수 표시는 노트와 별개로 자기 박스에 적힌다
    assert "초진" in CD.get_note_text(cross, CD.NOTE_DATE)


@pytest.mark.skipif(not M.CASE_ANCHORS, reason="케이스 양식이 없습니다")
def test_untouched_notes_start_empty(sid, patient):
    """한 칸도 안 채우면 빈 칸으로 남는다.

    예전에는 양식 슬라이드의 텍스트박스를 글자째 복제해서 안내문("n) dx. consult")이
    남았다. 그러려면 양식이 **남의 진료기록 슬라이드**를 들고 다녀야 했고(s)/p) 칸이
    거기에만 있었다), 그 파일이 배포되면 기록이 함께 나갔다. 이제 칸은 좌표로 만들고
    글자는 임상의가 채운다.
    """
    assert client.post(f"/api/commit/{sid}?allow_missing=true").status_code == 200
    prs = T.load_presentation(patient / N.ppt_filename(IDS, M.cfg.naming.ppt_pattern))
    cross = prs.slides[CD.cross_slide_index(prs)]
    assert (CD.get_note_text(cross, CD.NOTE_NEXT) or "").strip() == ""
    # 다섯 칸이 모두 만들어져 있어야 한다 — 적을 곳이 없으면 노트가 사라진다
    names = {sh.name for sh in cross.shapes}
    assert set(CD.NOTE_ORDER) <= names


# ── 박스 번호(①~⑤)와 좌하단 칸 ──────────────────────────────────────────────
def test_note_key_splits_left_column_by_height():
    """왼쪽 열은 높이로 갈린다 — ①(날짜) / ②(s,p) / ④(좌하단)."""
    W = 25.4
    assert CD.note_key(0.37, 0.63, W) == CD.NOTE_DATE      # ① 맨 위
    assert CD.note_key(0.15, 1.53, W) == CD.NOTE_SOAP      # ② 그 아래
    assert CD.note_key(0.15, 12.90, W) == CD.NOTE_LL       # ④ 십자뷰 아래칸
    assert CD.note_key(17.44, 0.63, W) == CD.NOTE_STATUS   # ③
    assert CD.note_key(16.88, 12.58, W) == CD.NOTE_NEXT    # ⑤
    print("PASS 위치 → 박스 판정 (①②③④⑤)")


def test_note_order_is_the_screen_numbering():
    assert CD.NOTE_ORDER == [CD.NOTE_DATE, CD.NOTE_SOAP, CD.NOTE_STATUS,
                             CD.NOTE_LL, CD.NOTE_NEXT]
    # 화면이 붙이는 번호와 같은 순서로 위치도 내려간다
    assert list(M.NOTE_BOXES) == CD.NOTE_ORDER, list(M.NOTE_BOXES)
    print("PASS 번호 순서 =", " ".join(f"{i}.{k}" for i, k in enumerate(CD.NOTE_ORDER, 1)))


def test_lower_left_box_has_a_place_even_though_template_lacks_it():
    """④는 양식에 도형이 없다 — 그래도 화면에 자리를 알려 줘야 고칠 수 있다."""
    r = M.NOTE_BOXES[CD.NOTE_LL]
    assert r["w"] > 0 and r["h"] > 0
    # 십자뷰 좌하단 칸(MASK_R3_L: 0,12.72 8.50x6.30) 안에 들어가야 한다
    assert r["x"] >= 0 and r["y"] >= 12.72 - 0.01
    assert r["x"] + r["w"] <= 8.50 + 0.01
    print(f"PASS ④ 좌하단 자리 = {r['x']},{r['y']} {r['w']}x{r['h']}cm")


def test_add_note_box_creates_a_real_textbox():
    """기증할 도형이 없을 때 실제로 만들어지는지."""
    from pptx import Presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    assert CD.add_note_box(slide, CD.NOTE_LL, CD.NOTE_LL_WINDOW)
    made = [sh for sh in slide.shapes if sh.name == CD.NOTE_LL]
    assert len(made) == 1, made
    assert made[0].has_text_frame and made[0].text_frame.text == ""
    print("PASS ④ 텍스트박스 생성")


def test_blank_lines_survive_rendering():
    """양식의 빈 줄은 글의 시작 높이를 잡는다 — 다듬어 없애면 안 된다."""
    out = M._render_notes({"tx_period": "3 month"},
                          {"B": "U: {wire_u}\n\n\nTx. Period: {tx_period}"})
    # 칸이 빈 줄(U:)만 사라지고 빈 줄 두 개는 남는다
    assert out["B"] == "\n\nTx. Period: 3 month", repr(out["B"])
    print("PASS 빈 줄 보존, 빈 칸 줄만 제거")


def test_trailing_date_is_written_smaller():
    """양식은 본문 15pt / 줄 끝 날짜 9pt 다 — 갈아끼울 때도 그 구분을 지킨다."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Emu(0), Emu(0), Emu(3000000), Emu(2000000))
    tb.name = CD.NOTE_STATUS
    seed = tb.text_frame.paragraphs[0].add_run()
    seed.text = "seed"
    seed.font.size = Pt(15)
    seed.font.color.rgb = RGBColor.from_string("FFFF00")

    CD.set_note_text(sl, CD.NOTE_STATUS,
                     "Tx. Period: 23 month\nRx. Period: 23 month (24.08.12)", small_pt=9)
    paras = tb.text_frame.paragraphs
    # 날짜가 없는 줄은 한 덩어리, 본문 크기 그대로
    assert [r.text for r in paras[0].runs] == ["Tx. Period: 23 month"]
    assert paras[0].runs[0].font.size.pt == 15
    # 날짜가 붙은 줄은 둘로 갈리고 뒤쪽만 작다
    sizes = [(r.text, r.font.size.pt) for r in paras[1].runs]
    assert sizes == [("Rx. Period: 23 month", 15.0), (" (24.08.12)", 9.0)], sizes
    # 색은 원래 것을 그대로 물려받는다
    assert str(paras[1].runs[1].font.color.rgb) == "FFFF00"
    # 읽을 때는 한 줄로 합쳐진다(기존 계약 유지)
    assert CD.get_note_text(sl, CD.NOTE_STATUS).endswith("(24.08.12)")
    print("PASS 줄 끝 날짜만 9pt, 본문·색은 유지")


def test_paren_only_line_is_not_split():
    """본문 없이 괄호만 있는 줄은 통째로 본문 크기다 — 잘못 쪼개면 안 된다."""
    from pptx import Presentation
    from pptx.util import Emu, Pt
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Emu(0), Emu(0), Emu(3000000), Emu(2000000))
    tb.name = CD.NOTE_SOAP
    seed = tb.text_frame.paragraphs[0].add_run()
    seed.text = "seed"
    seed.font.size = Pt(12)

    CD.set_note_text(sl, CD.NOTE_SOAP, "(메모만 있는 줄)", small_pt=9)
    runs = tb.text_frame.paragraphs[0].runs
    assert [r.text for r in runs] == ["(메모만 있는 줄)"], [r.text for r in runs]
    assert runs[0].font.size.pt == 12
    print("PASS 괄호만 있는 줄은 안 쪼갠다")
