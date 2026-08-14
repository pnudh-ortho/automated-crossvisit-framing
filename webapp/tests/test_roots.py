"""저장 위치를 여러 곳 등록해 두고 골라 쓴다.

병원 안 공유 폴더와 집 외장 드라이브를 오가는 식으로 쓰는 사람이 있다. 예전에는
저장 위치가 하나뿐이라 옮길 때마다 폴더 창을 다시 열어야 했고, 지금 닿지 않는
곳은 아예 기록에서 사라져 어디에 뒀는지조차 잊었다.

여기서 지키려는 것은 셋이다.
  · 목록은 **닿지 않아도 남는다** — 드라이브를 꽂으면 그대로 돌아갈 수 있어야 한다.
  · 갈아탈 때 **함께 움직여야 하는 것**(임시 세션·감사 로그·캐시)이 빠짐없이 따라간다.
  · 목록에서 빼는 것은 **목록에서만** — 폴더와 그 안의 환자 자료는 그대로 둔다.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M                                                  # noqa: E402


def _settings(d: dict):
    M.SETTINGS_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _read():
    return json.loads(M.SETTINGS_FILE.read_text(encoding="utf-8"))


def _add(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return p


# ── 목록 만들기 ────────────────────────────────────────────────────────────
def test_폴더를_고르면_목록에_쌓인다(_isolate_paths, tmp_path):
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    M.set_root(M.RootReq(path=str(a)))
    r = M.set_root(M.RootReq(path=str(b)))

    assert r["root"] == str(b)
    assert [x["path"] for x in r["roots"]] == [str(a), str(b)]
    assert _read()["roots"] == [str(a), str(b)]


def test_같은_곳을_다시_골라도_두_번_들어가지_않는다(_isolate_paths, tmp_path):
    a = _add(tmp_path, "병원")
    M.set_root(M.RootReq(path=str(a)))
    r = M.set_root(M.RootReq(path=str(a)))
    assert [x["path"] for x in r["roots"]] == [str(a)]


def test_예전_설정의_한_곳도_목록에_들어온다(_isolate_paths, tmp_path):
    """roots 키가 없던 시절에 쓰던 사람 — 켜자마자 목록이 비어 보이면 안 된다."""
    old = _add(tmp_path, "쓰던곳")
    _settings({"root": str(old)})
    assert M._saved_roots() == [str(old)]


# ── 갈아타기 ──────────────────────────────────────────────────────────────
def test_고른_곳으로_갈아탄다(_isolate_paths, tmp_path):
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    M.set_root(M.RootReq(path=str(a)))
    M.set_root(M.RootReq(path=str(b)))

    r = M.root_select(M.RootSelReq(path=str(a)))
    assert r["root"] == str(a) and M.ROOT == a
    assert [x["current"] for x in r["roots"]] == [True, False]
    assert _read()["root"] == str(a)
    assert _read()["roots"] == [str(a), str(b)]     # 목록은 그대로


def test_갈아타면_감사_로그도_그_위치로_간다(_isolate_paths, tmp_path):
    """예전에는 로그 자리가 켤 때 한 번 정해져 위치를 바꿔도 따라가지 않았다 —
    A 환자 자료의 기록이 B 위치에 쌓인다."""
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    M.set_root(M.RootReq(path=str(a)))
    M.set_root(M.RootReq(path=str(b)))
    assert M.LOG_FILE.parent == b
    M.root_select(M.RootSelReq(path=str(a)))
    assert M.LOG_FILE.parent == a


def test_갈아타면_임시_폴더도_새_위치에_생긴다(_isolate_paths, tmp_path):
    a = _add(tmp_path, "병원")
    M.set_root(M.RootReq(path=str(a)))
    assert M.SESS_ROOT == a / "_sessions_tmp" and M.SESS_ROOT.is_dir()


def test_목록에_없는_곳으로는_갈아타지_않는다(_isolate_paths, tmp_path):
    """새 위치는 폴더를 골라 더하는 길로만 들어온다 — 드롭다운이 아무 경로나
    받으면 오타 하나로 엉뚱한 폴더가 저장 위치가 된다."""
    a, unknown = _add(tmp_path, "병원"), _add(tmp_path, "낯선곳")
    M.set_root(M.RootReq(path=str(a)))
    with pytest.raises(M.HTTPException) as e:
        M.root_select(M.RootSelReq(path=str(unknown)))
    assert e.value.status_code == 400
    assert M.ROOT == a


def test_지금_닿지_않는_곳은_고를_수_없다(_isolate_paths, tmp_path):
    a = _add(tmp_path, "병원")
    gone = tmp_path / "외장하드"
    _settings({"root": str(a), "roots": [str(a), str(gone)]})
    M.ROOT = a
    with pytest.raises(M.HTTPException) as e:
        M.root_select(M.RootSelReq(path=str(gone)))
    assert e.value.status_code == 404
    assert M.ROOT == a                       # 있던 자리는 그대로


def test_닿지_않는_곳도_목록에는_남는다(_isolate_paths, tmp_path):
    """빼 버리면 드라이브를 꽂았을 때 돌아갈 길이 사라진다."""
    a = _add(tmp_path, "병원")
    gone = tmp_path / "외장하드"
    _settings({"root": str(a), "roots": [str(a), str(gone)]})
    M.ROOT = a
    rows = {x["path"]: x for x in M.roots_list()["roots"]}
    assert rows[str(gone)]["exists"] is False
    assert rows[str(a)]["exists"] is True and rows[str(a)]["current"] is True


# ── 목록에서 빼기 ─────────────────────────────────────────────────────────
def test_목록에서_빼도_폴더는_남는다(_isolate_paths, tmp_path):
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    (b / "홍길동_1_2").mkdir()
    M.set_root(M.RootReq(path=str(b)))
    M.set_root(M.RootReq(path=str(a)))

    r = M.root_forget(M.RootSelReq(path=str(b)))
    assert [x["path"] for x in r["roots"]] == [str(a)]
    assert b.is_dir() and (b / "홍길동_1_2").is_dir()      # 자료는 그대로


def test_지금_쓰는_곳은_뺄_수_없다(_isolate_paths, tmp_path):
    a = _add(tmp_path, "병원")
    M.set_root(M.RootReq(path=str(a)))
    with pytest.raises(M.HTTPException) as e:
        M.root_forget(M.RootSelReq(path=str(a)))
    assert e.value.status_code == 400
    assert M._saved_roots() == [str(a)]


# ── 다른 설정은 건드리지 않는다 ────────────────────────────────────────────
def test_갈아타도_이름_양식은_그대로다(_isolate_paths, tmp_path):
    """블록 형식은 저장 위치와 무관하게 하나로 쓴다 — 옮길 때마다 다시 짤 수 없다."""
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    _settings({"root": str(a), "roots": [str(a), str(b)],
               "folder_patterns": ["{seq}.{name}({ortho_id})"],
               "months_unit": "half", "letterbox_color": "FFFFFF"})
    M.ROOT = a
    M.root_select(M.RootSelReq(path=str(b)))

    d = _read()
    assert d["root"] == str(b)
    assert d["folder_patterns"] == ["{seq}.{name}({ortho_id})"]
    assert d["months_unit"] == "half" and d["letterbox_color"] == "FFFFFF"


def test_PPT_기억은_위치까지_담은_경로로_구분된다(_isolate_paths, tmp_path):
    """두 위치에 같은 이름의 환자 폴더가 있을 수 있다. 폴더 이름만 열쇠로 쓰면
    한쪽에서 고른 덱이 다른 쪽 환자에게 넘어간다."""
    a, b = _add(tmp_path, "병원"), _add(tmp_path, "집")
    da, db = a / "홍길동_1_2", b / "홍길동_1_2"
    da.mkdir()
    db.mkdir()

    _settings({"root": str(a)})
    M._remember_ppt(str(da), "보관/a.pptx")
    M._remember_ppt(str(db), "b.pptx")

    assert M._remembered_ppt(str(da)) == "보관/a.pptx"
    assert M._remembered_ppt(str(db)) == "b.pptx"


def test_예전_기억은_폴더_이름으로도_읽힌다(_isolate_paths, tmp_path):
    """열쇠를 전체 경로로 바꾸기 전에 남은 기록 — 읽어는 줘야 한다."""
    a = _add(tmp_path, "병원")
    d = a / "홍길동_1_2"
    d.mkdir()
    _settings({"root": str(a), "ppt_choice": {"홍길동_1_2": "옛덱.pptx"}})
    assert M._remembered_ppt(str(d)) == "옛덱.pptx"
