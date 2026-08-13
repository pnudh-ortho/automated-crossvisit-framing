"""
레터박스(사진이 창을 다 덮지 못할 때의 빈 공간) 검증.

사진이 슬롯을 다 덮지 못하면 예전에는 슬라이드 배경(흰색)이 그대로 보였다.
검수 편집기 캔버스(frontend/app.js: fillStyle="#000")와 정합 기준영상 복원
(ppt_reader: borderValue=(0,0,0))은 이미 검정을 쓰고 있었으므로, PPT만 흰색이라
화면과 결과물이 어긋났다. 이제 슬롯마다 BACKDROP_ 사각형을 사진 뒤에 깐다.

검증 항목:
  1. 슬롯마다 BACKDROP_ 도형이 생기고 기하가 슬롯과 정확히 일치
  2. 채움색이 config의 letterbox_color (기본 검정), 테두리 없음
  3. z-order: 배경 < 사진 < 마스크
  4. ppt_reader가 BACKDROP_에 간섭받지 않음 (PHOTO_만 인식)
  5. allow_letterbox 플래그가 cover clamp를 실제로 켜고 끔

실행: cd webapp && python tests/test_letterbox.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from PIL import Image  # noqa: E402

import config as C  # noqa: E402
import template as T  # noqa: E402
import ppt_writer as W  # noqa: E402
from coords import EditorState, WindowCm, apply_cover_clamp, cover_base_ext_cm, emu_to_cm  # noqa: E402

OUT_DIR = Path(os.environ.get("LETTERBOX_OUT", "/tmp/letterbox_out"))
TOL_CM = 1e-4


def approx(a, b, tol=TOL_CM):
    return abs(a - b) <= tol


def make_image(path: Path, size=(1600, 1200)):
    Image.new("RGB", size, (30, 90, 180)).save(path)
    return size


def main():
    cfg = C.load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # config 기본값 확인
    assert cfg.geometry.letterbox_color == "000000", \
        f"기본 레터박스 색이 검정이 아님: {cfg.geometry.letterbox_color}"
    assert cfg.geometry.allow_letterbox is True, "기본값은 레터박스 허용"
    print(f"config: letterbox_color={cfg.geometry.letterbox_color} "
          f"allow_letterbox={cfg.geometry.allow_letterbox}")

    img_path = OUT_DIR / "sample.jpg"
    photo_wh = make_image(img_path)

    prs = T.load_presentation(cfg.resolve(cfg.paths.template_pptx))
    slide = prs.slides[0]
    slot_windows = {n: T.shape_window_cm(T.find_shape(slide, n))
                    for n in cfg.ppt.slot_names}

    for slot in cfg.ppt.slot_names:
        W.place_photo_in_slot(slide, slot, img_path, photo_wh,
                              letterbox_color=cfg.geometry.letterbox_color)

    names = [sh.name for sh in slide.shapes]

    # 1) 슬롯마다 배경 도형 + 기하 일치
    for slot in cfg.ppt.slot_names:
        bname = W.backdrop_shape_name(slot)
        assert bname in names, f"{bname} 없음"
        bd = T.find_shape(slide, bname)
        win = slot_windows[slot]
        assert approx(emu_to_cm(bd.left), win.x) and approx(emu_to_cm(bd.top), win.y), \
            f"{bname} 위치 불일치"
        assert approx(emu_to_cm(bd.width), win.w) and approx(emu_to_cm(bd.height), win.h), \
            f"{bname} 크기 불일치"
        # 2) 색·테두리
        assert str(bd.fill.fore_color.rgb) == cfg.geometry.letterbox_color.upper(), \
            f"{bname} 채움색 불일치: {bd.fill.fore_color.rgb}"
        assert bd.line.fill.type is not None, f"{bname} 선 채움이 설정되지 않음"
        print(f"  {bname:22s} {emu_to_cm(bd.width):.2f}x{emu_to_cm(bd.height):.2f}cm "
              f"#{bd.fill.fore_color.rgb} OK")

    # 3) z-order: 배경 < 사진 (spTree 순서 = 그리는 순서, 앞쪽이 아래)
    for slot in cfg.ppt.slot_names:
        bi = names.index(W.backdrop_shape_name(slot))
        pi = names.index(W.photo_shape_name(slot))
        assert bi < pi, f"{slot}: 배경({bi})이 사진({pi})보다 앞에 있어야 함"
    # 모든 마스크는 모든 사진보다 뒤(위)에 그려져야 함
    last_photo = max(names.index(W.photo_shape_name(s)) for s in cfg.ppt.slot_names)
    first_mask = min((i for i, n in enumerate(names)
                      if n.startswith(cfg.ppt.mask_prefix)), default=None)
    assert first_mask is not None and first_mask > last_photo, \
        f"마스크({first_mask})가 사진({last_photo})보다 위에 있어야 함"
    print(f"z-order OK: 배경 < 사진(최대 {last_photo}) < 마스크(최소 {first_mask})")

    # 4) ppt_reader 간섭 없음
    import ppt_reader as R  # noqa: E402
    R.set_slot_windows(slot_windows)
    vs = R.read_visit_slide(slide, 0, cfg, cfg.geometry.render_px_per_cm)
    assert set(vs.slots) == set(cfg.ppt.slot_names), \
        f"ppt_reader가 인식한 슬롯이 다름: {sorted(vs.slots)}"
    print(f"ppt_reader OK: 슬롯 {len(vs.slots)}개 인식, BACKDROP_ 간섭 없음")

    out = OUT_DIR / "letterbox.pptx"
    prs.save(str(out))
    print(f"저장: {out}")

    # 5) allow_letterbox 플래그가 clamp를 실제로 켜고 끔
    win = slot_windows["SLOT_FRONT"]
    bw, bh = cover_base_ext_cm(photo_wh[0], photo_wh[1], win)
    st = EditorState(dx_px=0, dy_px=0, scale=0.7, angle_deg=8.0)  # 축소+회전 → 귀퉁이 빔
    clamped = apply_cover_clamp(st, win, bw, bh)
    assert clamped.scale > st.scale, "clamp가 배율을 올려야 함(테스트 전제)"

    import main as M  # noqa: E402
    M.cfg.geometry.allow_letterbox = True
    assert M._clamp(st, win, bw, bh).scale == st.scale, \
        "allow_letterbox=true면 배율을 건드리지 않아야 함"
    M.cfg.geometry.allow_letterbox = False
    assert M._clamp(st, win, bw, bh).scale == clamped.scale, \
        "allow_letterbox=false면 cover clamp가 걸려야 함"
    M.cfg.geometry.allow_letterbox = cfg.geometry.allow_letterbox  # 원복
    print(f"clamp 게이팅 OK: scale {st.scale} → (허용) {st.scale} / (금지) {clamped.scale:.4f}")

    print("\n모든 검증 통과")
    print("주의: 이 환경엔 LibreOffice가 없어 실제 렌더 색은 육안 확인 못 함. "
          "pptx를 파워포인트로 열어 빈 공간이 검정인지 확인 필요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
