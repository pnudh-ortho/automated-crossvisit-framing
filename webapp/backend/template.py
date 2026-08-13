"""
템플릿 파싱 (Stage 1)

PPT 좌표를 하드코딩하지 않고 도형 '이름'으로 슬롯 위치를 읽는다.
레이아웃이 수정되어도(창 좌표가 바뀌어도) 코드는 무변경.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

from coords import WindowCm, emu_to_cm


@dataclass(frozen=True)
class SlideGeom:
    width_cm: float
    height_cm: float


def load_presentation(path: str | Path) -> Presentation:
    return Presentation(str(path))


def slide_geom(prs: Presentation) -> SlideGeom:
    return SlideGeom(emu_to_cm(prs.slide_width), emu_to_cm(prs.slide_height))


def find_shape(slide, name: str):
    """이름으로 도형 하나 찾기. 없으면 None."""
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    return None


def require_shape(slide, name: str):
    sh = find_shape(slide, name)
    if sh is None:
        raise KeyError(f"템플릿에 도형 '{name}'가 없습니다")
    return sh


def shape_window_cm(shape) -> WindowCm:
    """도형의 위치·크기를 cm 창으로."""
    return WindowCm(
        x=emu_to_cm(shape.left),
        y=emu_to_cm(shape.top),
        w=emu_to_cm(shape.width),
        h=emu_to_cm(shape.height),
    )


def slot_windows(slide, slot_names: list[str]) -> dict[str, WindowCm]:
    """슬롯 앵커 이름 → 창(cm). 삽입 위치 기준."""
    out = {}
    for name in slot_names:
        out[name] = shape_window_cm(require_shape(slide, name))
    return out


def mask_shapes(slide, mask_prefix: str) -> list:
    return [sh for sh in slide.shapes if sh.name.startswith(mask_prefix)]


def validate_template(prs: Presentation, cfg) -> dict:
    """
    템플릿이 사양을 만족하는지 점검하고 요약을 반환.
    첫 슬라이드에 슬롯 5개 + INFO_BOX + 마스크가 있어야 한다.
    """
    slide = prs.slides[0]
    geom = slide_geom(prs)
    wins = slot_windows(slide, cfg.ppt.slot_names)
    masks = mask_shapes(slide, cfg.ppt.mask_prefix)
    info = require_shape(slide, cfg.ppt.info_box_name)
    return {
        "slide_cm": (round(geom.width_cm, 3), round(geom.height_cm, 3)),
        "slots": {k: (round(v.x, 3), round(v.y, 3), round(v.w, 3), round(v.h, 3)) for k, v in wins.items()},
        "mask_count": len(masks),
        "info_box": info.name,
    }
