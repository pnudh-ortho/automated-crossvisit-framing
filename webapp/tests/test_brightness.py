"""밝기(감마) 검증.

이 기능의 위험은 두 가지고, 둘 다 조용히 어긋난다:

  ① **흰 배경과 치아가 날아가는 것.** 곱셈(게인)으로 밝히면 밝은 쪽부터 255 로
     몰린다. 교정 사진은 배경이 밝은 회색이고 치아가 흰색이라 정확히 그 부분이
     먼저 망가진다. 감마는 0 과 255 를 고정점으로 남기므로 그 일이 없다 — 그
     성질을 여기서 못박는다.
  ② **미리보기와 결과물이 다른 것.** 화면은 SVG feComponentTransfer, 결과물은
     cv2 LUT 로 따로 계산한다. 지수 식이 갈라지면 예외 없이 그냥 다르게 나온다.

실행: cd webapp && python -m pytest tests/test_brightness.py -q
"""
import os
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import crop as Cr  # noqa: E402
from coords import EditorState, WindowCm  # noqa: E402

HERE = os.path.dirname(__file__)
WIN = WindowCm(x=0.0, y=0.0, w=8.4, h=6.3)
PPC = 100.0


def test_zero_leaves_the_photo_alone():
    """0 은 '손대지 않는다'는 뜻이다 — LUT 를 만들지도 않는다."""
    assert Cr.gamma_lut(0) is None
    assert Cr.gamma_lut(0.0) is None
    assert Cr.bright_exponent(0) == 1.0
    print("PASS 밝기 0 → 원본 그대로")


def test_black_and_white_are_fixed_points():
    """0 과 255 는 어떤 눈금에서도 움직이지 않는다. 곱셈과 갈리는 지점이다."""
    for v in (-50, -25, -1, 1, 25, 50):
        lut = Cr.gamma_lut(v)
        assert lut[0] == 0, f"눈금 {v}: 검정이 {lut[0]} 로 떴다"
        assert lut[255] == 255, f"눈금 {v}: 흰색이 {lut[255]} 로 내려갔다"
    print("PASS 0·255 고정 — 흰 배경과 치아가 날아가지 않는다")


def test_plus_brightens_minus_darkens_and_stays_monotonic():
    lut_up, lut_dn = Cr.gamma_lut(25), Cr.gamma_lut(-25)
    mid = np.arange(1, 255)
    # 흰색 바로 아래는 올릴 자리가 없어 제자리다(254→254). 고정점이 있다는 뜻이라
    # 결함이 아니다 — 그래서 전 구간은 '내려가지 않음', 중간은 '실제로 오름'을 본다.
    assert (lut_up[mid] >= mid).all(), "+ 인데 어두워진 칸이 있다"
    assert (lut_dn[mid] <= mid).all(), "- 인데 밝아진 칸이 있다"
    core = np.arange(16, 240)
    assert (lut_up[core] > core).all(), "+ 인데 중간톤이 안 밝아졌다"
    assert (lut_dn[core] < core).all(), "- 인데 중간톤이 안 어두워졌다"
    for lut in (lut_up, lut_dn):
        assert (np.diff(lut.astype(int)) >= 0).all(), "단조가 깨졌다 — 계조가 뒤집힌다"
    print(f"PASS +25 는 밝게(128→{lut_up[128]}) · -25 는 어둡게(128→{lut_dn[128]})")


def test_range_is_clamped():
    """범위 밖 눈금이 와도 끝값으로 잘라 받는다 — 예외로 저장을 막지 않는다."""
    assert Cr.bright_exponent(999) == Cr.bright_exponent(Cr.BRIGHT_MAX)
    assert Cr.bright_exponent(-999) == Cr.bright_exponent(Cr.BRIGHT_MIN)
    print("PASS 범위 밖 눈금은 끝값으로 잘림")


def test_letterbox_colour_is_not_brightened():
    """밝기는 **사진에만** 먹는다. 창을 못 덮은 자리의 양식 색까지 밝아지면 안 된다.

    그래서 워프 전에 LUT 를 태운다. 뒤에 태우면 borderValue 로 채운 픽셀까지
    같이 지나가 양식이 정한 색이 아니게 된다.
    """
    img = np.full((300, 400, 3), 100, np.uint8)
    st = EditorState(0.0, 0.0, 0.4, 0.0)          # 많이 줄여 가장자리를 비운다
    letterbox = (0, 0, 200)                        # 고정점이 아닌 색이어야 검증이 된다
    out = Cr.render_window(img, WIN, st, False, 100.0, PPC, letterbox, brightness=40)
    corner = out[2, 2]
    assert tuple(int(c) for c in corner) == letterbox, f"레터박스가 {corner} 로 변했다"
    assert out[out.shape[0] // 2, out.shape[1] // 2][0] > 100, "사진은 밝아져야 한다"
    print(f"PASS 레터박스 {letterbox} 그대로 · 사진만 밝아짐")


def test_bake_applies_the_photo_s_brightness():
    """실제 굽기 경로(_bake_window)가 Photo.brightness 를 집어 든다.

    PPT 에 들어갈 그림과 환자 폴더에 저장될 파일이 같은 함수에서 나오므로,
    여기가 통하면 둘 다 통한다.
    """
    import main as M

    tmp = M.Path(os.environ.get("TMPDIR", "/tmp")) / "bright_test"
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / "src.jpg"
    cv2.imwrite(str(src), np.full((600, 800, 3), 90, np.uint8))

    photo = M.Photo("p1", src, 800, 600)
    assert photo.brightness == 0.0, "기본값은 원본이어야 한다"
    st = EditorState()

    plain, _ = M._bake_window(photo, WIN, st, False, tmp / "plain.jpg")
    photo.brightness = 30
    bright, _ = M._bake_window(photo, WIN, st, False, tmp / "bright.jpg")
    assert plain and bright
    a = float(cv2.imread(str(plain)).mean())
    b = float(cv2.imread(str(bright)).mean())
    assert b > a + 5, f"구운 그림이 안 밝아졌다 ({a:.1f} → {b:.1f})"
    print(f"PASS 굽기에 밝기 반영 (평균 {a:.1f} → {b:.1f})")


def test_endpoint_stores_and_clamps():
    import main as M

    s = M.Session("first", M.N.Identifiers("홍길동", "", "54321"), "A")
    photo = M.Photo("p9", M.Path("/x/none.jpg"), 100, 100)
    s.photos.append(photo)
    M.SESSIONS[s.id] = s
    try:
        r = M.set_brightness(M.BrightReq(session_id=s.id, photo_id="p9", value=20))
        assert r["brightness"] == 20 and photo.brightness == 20
        M.set_brightness(M.BrightReq(session_id=s.id, photo_id="p9", value=999))
        assert photo.brightness == Cr.BRIGHT_MAX, photo.brightness
    finally:
        M.SESSIONS.pop(s.id, None)
    print("PASS /api/brightness 저장 · 범위 자름")


def test_screen_and_result_use_the_same_exponent():
    """미리보기(app.js)와 결과물(crop.py)의 지수 식이 갈라지지 않았는지 본다.

    갈라져도 예외가 나지 않는다 — 화면에서 맞춘 밝기와 저장된 사진이 그냥 다를
    뿐이라, 사람이 눈으로 알아채기 전까지 아무도 모른다. 그래서 글자로 못박는다.
    """
    js = open(os.path.join(HERE, "..", "frontend", "app.js"), encoding="utf-8").read()
    py = open(os.path.join(HERE, "..", "backend", "crop.py"), encoding="utf-8").read()
    assert re.search(r"Math\.pow\(2,\s*-n\s*/\s*50\)", js), "app.js 의 지수 식이 바뀌었다"
    assert re.search(r"2\.0\s*\*\*\s*\(-v\s*/\s*50\.0\)", py), "crop.py 의 지수 식이 바뀌었다"
    # SVG 필터의 기본 계산 공간은 linearRGB 다. 이게 빠지면 서버와 다른 곳에서 푼다.
    assert 'color-interpolation-filters", "sRGB"' in js, "SVG 필터가 sRGB 로 안 묶여 있다"
    print("PASS 화면·결과물 지수 식 일치 (2 ** (-눈금/50), sRGB)")


def test_shortcut_moves_exactly_one_notch():
    """W/S 한 번 = 조절 바 한 칸.

    이 앱은 "단축키·조절 바·◀ ▶ 가 모두 같은 눈금을 쓴다"를 약속으로 걸어 뒀다
    (업데이트 로그 26.08.19). 눈금이 갈리면 조절 바로 한 칸 올린 것을 키로는
    되돌릴 수 없게 되고, 같은 화면의 두 도구가 서로 다른 자를 들게 된다.
    """
    js = open(os.path.join(HERE, "..", "frontend", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "..", "frontend", "index.html"), encoding="utf-8").read()

    assert re.search(r'case "KeyW":.*E\.bright = clamp\(E\.bright \+ br', js), "W 가 밝기에 안 걸려 있다"
    assert re.search(r'case "KeyS":.*E\.bright = clamp\(E\.bright - br', js), "S 가 밝기에 안 걸려 있다"

    step = re.search(r"const mv = 1, rot = \.1, sc = \.01, br = (\S+?);", js)
    assert step and step.group(1) == "1", f"키 눈금이 {step and step.group(1)} 로 바뀌었다"
    for box in ("v-bright", "fv-bright"):
        m = re.search(r'id="%s"[^>]*step="([^"]+)"' % box, html)
        assert m and m.group(1) == "1", f"{box} 의 눈금이 {m and m.group(1)}"

    # 파생 자리(10·11)는 조절 바가 잠긴다(setFaceKnobsEnabled). 키도 같이 잠겨야
    # 한다 — 서버가 400 으로 되받는 자리라, 안 잠그면 화면만 움직이고 오류가 뜬다.
    assert "if(face && (cellOf(FED.cell) || {}).from) return;" in js, "파생 자리 잠금이 없다"
    body = js[js.index("const E = face ? FED : ED;"):js.index('case "KeyW"')]
    assert "(cellOf(FED.cell) || {}).from) return;" in body, \
        "잠금이 switch 뒤에 있다 — 회전·배율·이동 키에는 안 걸린다"
    # 화면의 단축키 안내(.tip)도 같이 늙지 않게 묶는다. 키를 더해 놓고 안내를
    # 안 고치면, 있는 기능을 아무도 모른 채로 지나간다.
    tips = re.findall(r'<p class="tip">(.*?)</p>', html, re.S)
    assert len(tips) >= 2, f"안내 문구가 {len(tips)}개뿐이다"
    for tip in tips:
        if "Q/E" in tip:
            assert "W/S" in tip, f"이 안내에 W/S 가 빠졌다: {tip[:60]}"
    print("PASS W/S = 조절 바 한 칸 · 파생 자리 잠김 · 화면 안내에도 표기")


if __name__ == "__main__":
    test_zero_leaves_the_photo_alone()
    test_black_and_white_are_fixed_points()
    test_plus_brightens_minus_darkens_and_stays_monotonic()
    test_range_is_clamped()
    test_letterbox_colour_is_not_brightened()
    test_bake_applies_the_photo_s_brightness()
    test_endpoint_stores_and_clamps()
    test_screen_and_result_use_the_same_exponent()
    test_shortcut_moves_exactly_one_notch()
    print("\n✅ 밝기 테스트 통과")
