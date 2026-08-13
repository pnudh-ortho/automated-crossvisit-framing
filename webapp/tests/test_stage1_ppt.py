"""
Stage 1 통합 검증: 템플릿 파싱 + 사진 삽입 + z-order + 앵커제거 + INFO_BOX.

실사진이 없으므로 합성 4:3 이미지로 검증한다. 생성된 pptx는 파워포인트로
열어 육안 확인도 가능(OUT_DIR).

실행: cd webapp && python tests/test_stage1_ppt.py
"""
import os
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from PIL import Image, ImageDraw  # noqa: E402

import config as C  # noqa: E402
import template as T  # noqa: E402
import ppt_writer as W  # noqa: E402
from coords import emu_to_cm  # noqa: E402

# 산출물 확인용 폴더. 기본은 임시 폴더 — 특정 PC 의 경로를 박으면 다른 데서 못 돈다.
OUT_DIR = Path(os.environ.get("STAGE1_OUT",
                              Path(tempfile.gettempdir()) / "stage1_out"))


def make_test_image(path: Path, label: str, color: tuple, size=(1600, 1200)):
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    # 중앙 라벨 + 방향 표식(회전/좌우 확인용 화살표)
    d.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=(255, 255, 255), width=8)
    d.text((size[0] // 2 - 60, size[1] // 2 - 20), label, fill=(255, 255, 255))
    d.polygon([(size[0] // 2, 80), (size[0] // 2 - 40, 160), (size[0] // 2 + 40, 160)],
              fill=(255, 255, 0))  # 위쪽 화살표
    img.save(path)
    return size


def main():
    cfg = C.load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 템플릿 검증
    tmpl_path = cfg.resolve(cfg.paths.template_pptx)
    prs = T.load_presentation(tmpl_path)
    summary = T.validate_template(prs, cfg)
    print("템플릿 요약:", summary)
    assert summary["slide_cm"] == (25.4, 19.05), summary["slide_cm"]
    assert set(summary["slots"]) == set(cfg.ppt.slot_names)
    assert summary["mask_count"] >= 12
    print(f"  슬롯 {len(summary['slots'])}개, 마스크 {summary['mask_count']}개 확인")

    # 2) 합성 이미지 생성 (구내 5슬롯)
    colors = {
        "SLOT_UPPER": (120, 40, 40), "SLOT_LEFT": (40, 90, 120),
        "SLOT_FRONT": (40, 120, 60), "SLOT_RIGHT": (120, 90, 40),
        "SLOT_LOWER": (90, 40, 120),
    }
    img_dir = OUT_DIR / "imgs"
    img_dir.mkdir(exist_ok=True)
    photo_wh = {}
    for slot, col in colors.items():
        p = img_dir / f"{slot}.jpg"
        photo_wh[slot] = make_test_image(p, slot.replace("SLOT_", ""), col)

    # 3) 초진: 템플릿 복사 → 삽입
    dest = OUT_DIR / "환자_123456789_12345.pptx"
    out_prs = W.new_ppt_from_template(tmpl_path, dest)
    slide = out_prs.slides[0]

    placements = {}
    for slot, col in colors.items():
        pic, pl = W.place_photo_in_slot(slide, slot, img_dir / f"{slot}.jpg", photo_wh[slot])
        placements[slot] = pl
    W.set_info_box(slide, cfg.ppt.info_box_name, "26.07.10. (초진)")
    out_prs.save(str(dest))
    print(f"  저장: {dest}")

    # 4) 재파싱 검증
    v = T.load_presentation(dest)
    vslide = v.slides[0]
    names = [sh.name for sh in vslide.shapes]

    # 4a) 사진 5개 존재 + 앵커 제거됨
    photos = [n for n in names if n.startswith(W.PHOTO_NAME_PREFIX)]
    assert len(photos) == 5, photos
    for slot in cfg.ppt.slot_names:
        assert slot not in names, f"앵커 {slot} 가 제거되지 않음"
    print(f"  사진 {len(photos)}개 삽입, 슬롯 앵커 5개 제거 확인")

    # 4b) 위치·크기 = 슬롯 창(cover-fit, 4:3→4:3 이면 창과 동일)
    wins = T.slot_windows(prs.slides[0], cfg.ppt.slot_names)  # 원본 템플릿 기준
    for slot, win in wins.items():
        pic = next(sh for sh in vslide.shapes if sh.name == W.photo_shape_name(slot))
        assert abs(emu_to_cm(pic.left) - win.x) < 1e-3
        assert abs(emu_to_cm(pic.top) - win.y) < 1e-3
        assert abs(emu_to_cm(pic.width) - win.w) < 1e-3
        assert abs(emu_to_cm(pic.height) - win.h) < 1e-3
    print("  cover-fit 위치·크기 = 슬롯 창 일치 확인")

    # 4c) z-order: 모든 사진이 모든 마스크보다 뒤(먼저 그려짐)
    order = {sh.name: i for i, sh in enumerate(vslide.shapes)}
    max_photo_idx = max(order[n] for n in photos)
    min_mask_idx = min(order[n] for n in names if n.startswith(cfg.ppt.mask_prefix))
    assert max_photo_idx < min_mask_idx, (max_photo_idx, min_mask_idx)
    print(f"  z-order: 사진(<= {max_photo_idx}) < 마스크(>= {min_mask_idx}) 확인")

    # 4d) INFO_BOX
    info = T.find_shape(vslide, cfg.ppt.info_box_name)
    assert "초진" in info.text_frame.text
    print(f"  INFO_BOX = '{info.text_frame.text}' 확인")

    # 5) 재진: 템플릿 슬라이드 임포트 (구내 시퀀스 뒤, 다른 슬라이드 앞 위치)
    n_before = W.count_slides(v)
    new_slide = W.import_template_slide(v, prs, insert_index=1)
    assert W.count_slides(v) == n_before + 1
    # 임포트된 슬라이드에도 슬롯 앵커가 존재해야(깨끗한 템플릿)
    new_names = [sh.name for sh in v.slides[1].shapes]
    assert all(s in new_names for s in cfg.ppt.slot_names), new_names
    dest2 = OUT_DIR / "환자_123456789_12345_재진.pptx"
    v.save(str(dest2))
    print(f"  재진 슬라이드 임포트 OK (슬라이드 {n_before}→{W.count_slides(v)}), 저장: {dest2}")

    print("\n✅ Stage 1 전체 검증 통과")


if __name__ == "__main__":
    main()
