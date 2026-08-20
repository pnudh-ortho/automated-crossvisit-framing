"""
설정 로더 (pydantic v2) — Fastest Lap

config.yaml을 읽어 타입 검증된 객체로 노출한다. 상대 경로는 config.yaml
위치를 기준으로 절대경로화한다. 본편과 달리 PPT·명명 패턴·노트 섹션이 없다 —
사용자 취향은 settings.json 이 담당하고, 여기는 코드와 짝을 이루는 값뿐이다.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Paths(BaseModel):
    root: str
    models_dir: str
    log_file: str


class DigitRule(BaseModel):
    digits: int


class SlotIndex(BaseModel):
    slot: str
    index: int


class FaceCfg(BaseModel):
    classes: list[str]
    start_index: int
    order: list[str]


class RegistrationThresholds(BaseModel):
    # 치아 중심점 경로의 문턱. 대응점이 치아 개수(보통 8~20)뿐이라 문턱도 그 규모다.
    teeth_min_inliers: int = 4
    teeth_min_inlier_ratio: float = 0.45
    # 잔차 / 치아 간격. 무차원이라 해상도·치아 개수와 무관하다.
    teeth_max_resid_spacing: float = 0.25


class FramingThresholds(BaseModel):
    # 기본값은 backend/framing.py:DEFAULT_THRESH 와 같게 유지한다.
    max_spread_pct: float = 3.0
    min_crop_frac: float = 0.15
    max_crop_frac: float = 1.60
    max_angle_deg: float = 20.0


class Thresholds(BaseModel):
    classify_confidence: float
    registration: RegistrationThresholds
    framing: FramingThresholds = FramingThresholds()


class Geometry(BaseModel):
    emu_per_cm: int
    emu_per_inch: int
    render_px_per_cm: float
    # 저장 이미지를 구울 해상도(px/cm) 기본값. settings.json 의 output 이 이긴다.
    export_px_per_cm: float = 200
    rotation_range_deg: float
    rotation_step_deg: float
    # 사진이 창을 다 덮지 못할 때(회전·축소·이동) 드러나는 빈 공간 = 레터박스
    letterbox_color: str = "000000"   # 배경색 (RGB hex, '#' 없이)
    allow_letterbox: bool = True      # False면 빈 공간이 안 생기도록 배율 하한을 강제


class WindowDef(BaseModel):
    x: float
    y: float
    w: float
    h: float


class PerfCfg(BaseModel):
    # 슬롯별 정합/프레이밍 동시 실행 수. 0 = 자동(min(3, cpu//2)).
    pair_workers: int = 0


class Config(BaseModel):
    paths: Paths
    classes: list[str]
    intraoral_slots: dict[str, SlotIndex]
    slot_names: list[str]
    slot_windows: dict[str, WindowDef]
    face_window: WindowDef
    face: FaceCfg
    thresholds: Thresholds
    geometry: Geometry
    perf: PerfCfg = PerfCfg()

    # config.yaml의 디렉토리 (상대경로 해석 기준). 직렬화 제외.
    base_dir: Path = Field(default=Path("."), exclude=True)

    def resolve(self, rel: str) -> Path:
        """config 기준 상대경로를 절대경로로. `~` 는 사용자 홈으로 편다.

        **사용자 데이터는 저장소 밖에 둔다.** 안에 두면 `git pull` 이 환자 폴더와
        같은 트리를 건드리고, `.gitignore` 실수 하나로 환자 정보가 저장소에 들어간다.
        `~` 확장은 Windows 에서도 `%USERPROFILE%` 로 풀린다.
        """
        p = Path(rel).expanduser()
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    # 편의 접근자
    @property
    def slot_by_class(self) -> dict[str, str]:
        return {cls: si.slot for cls, si in self.intraoral_slots.items()}

    @property
    def index_by_class(self) -> dict[str, int]:
        return {cls: si.index for cls, si in self.intraoral_slots.items()}

    @property
    def class_by_slot(self) -> dict[str, str]:
        return {si.slot: cls for cls, si in self.intraoral_slots.items()}


def load_config(path: str | os.PathLike = None) -> Config:
    if path is None:
        # 기본: webapp/config.yaml
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    path = Path(path).resolve()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cfg = Config(**data)
    cfg.base_dir = path.parent
    return cfg
