"""
양식 계측선 옮기기 검증.

슬라이드 10·11·15·16 에는 진단용 계측선이 그려져 있다. 검수 화면에서 끌어 옮긴
양(cm)만 세션에 남기고, 확정할 때 실제 도형에 더한다 — 길이·기울기는 건드리지
않는다(양식이 정한 기울기가 곧 진단 기준이라 임의로 바뀌면 안 된다).

실행: cd webapp && python -m pytest tests/test_lines.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import main as M  # noqa: E402

pytestmark = pytest.mark.skipif(not M.CASE_LINES, reason="양식에 계측선이 없습니다")


def test_lines_are_read_with_endpoints():
    """선은 도형 상자 + flip 으로 끝점이 정해진다 — 대각선도 방향이 살아야 한다."""
    assert set(M.CASE_LINES) == {10, 11, 15, 16}, sorted(M.CASE_LINES)
    ln = [x for x in M.CASE_LINES[16] if x["y1"] == x["y2"]][0]   # 가로선
    assert ln["x1"] < ln["x2"] and ln["width_pt"]
    # flip 이 걸린 대각선(s11)도 두 끝이 서로 다르다
    diag = [x for x in M.CASE_LINES[11] if x["x1"] != x["x2"] and x["y1"] != x["y2"]]
    assert diag, "대각선이 없다 — 전제 확인 필요"
    for d in diag:
        assert (d["x1"], d["y1"]) != (d["x2"], d["y2"])
    print(f"PASS 선 읽기 {[(k, len(v)) for k, v in sorted(M.CASE_LINES.items())]}")


def test_unknown_line_rejected():
    s = M.Session("first", None, "A")
    M.SESSIONS[s.id] = s
    with pytest.raises(Exception) as e:
        M.lines_set(M.LinesReq(session_id=s.id, moves={"99:없는선": [1, 1]}))
    assert "모르는 선" in str(e.value)
    print("PASS 모르는 선은 거절")


def test_move_round_trip_and_reset():
    s = M.Session("first", None, "A")
    M.SESSIONS[s.id] = s
    key = M.CASE_LINES[16][0]["id"]
    M.lines_set(M.LinesReq(session_id=s.id, moves={key: [1.5, -0.5]}))
    assert s.line_moves[key] == [1.5, -0.5]
    M.lines_set(M.LinesReq(session_id=s.id, moves={key: None}))
    assert key not in s.line_moves, "제자리로 되돌리지 못했다"
    print("PASS 이동 저장·되돌리기")


def test_apply_moves_shifts_the_real_shape():
    """확정 시 도형이 실제로 옮겨지는가 — 크기는 그대로여야 한다."""
    import config as C
    import template as T
    cfg = C.load_config()
    prs = T.load_presentation(cfg.resolve(cfg.paths.case_template_pptx))
    ln = M.CASE_LINES[16][0]
    slide = prs.slides[15]
    shape = next(sh for sh in slide.shapes if sh.name == ln["name"])
    before = (shape.left, shape.top, shape.width, shape.height)

    s = M.Session("first", None, "A")
    s.line_moves = {ln["id"]: [2.0, -1.0]}
    assert M._apply_line_moves(prs, s) == 1

    after = (shape.left, shape.top, shape.width, shape.height)
    assert after[0] - before[0] == pytest.approx(2.0 * M.EMU_PER_CM, abs=2)
    assert after[1] - before[1] == pytest.approx(-1.0 * M.EMU_PER_CM, abs=2)
    assert after[2:] == before[2:], "길이가 바뀌었다 — 자리만 옮겨야 한다"
    print("PASS 도형 이동 (크기 불변)")


if __name__ == "__main__":
    test_lines_are_read_with_endpoints()
    test_unknown_line_rejected()
    test_move_round_trip_and_reset()
    test_apply_moves_shifts_the_real_shape()
    print("\n✅ 계측선 테스트 통과")
