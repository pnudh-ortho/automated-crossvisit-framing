"""
설정 로더 (pydantic v2)

config.yaml을 읽어 타입 검증된 객체로 노출한다. 상대 경로는 config.yaml
위치를 기준으로 절대경로화한다. 파일명/폴더명/임계값은 전부 여기서 온다.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Paths(BaseModel):
    root: str
    template_pptx: str
    models_dir: str
    log_file: str
    # 케이스 프레젠테이션 양식. 없으면 초진도 십자뷰 한 장짜리로 만든다.
    case_template_pptx: str | None = None


class NamingCfg(BaseModel):
    folder_pattern: str
    ppt_pattern: str
    ppt_patterns_legacy: list[str] = []   # 옛 기본형 — 빈 목록일 때 인식 폴백
    photo_pattern: str
    photo_extra_pattern: str = "{ortho_id}_{visit} ({index})-{n}.jpg"
    visit_regex: str


class DigitRule(BaseModel):
    digits: int


class NameRule(BaseModel):
    allow_regex: str


class Identifiers(BaseModel):
    hospital_id: DigitRule
    ortho_id: DigitRule
    name: NameRule


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
    # 모델을 아직 안 넣은 설치본의 config.yaml 에는 없을 수 있다 → 기본값으로 산다.
    framing: FramingThresholds = FramingThresholds()


class Geometry(BaseModel):
    emu_per_cm: int
    emu_per_inch: int
    render_px_per_cm: float
    # PPT 에 넣을 그림을 구울 해상도(px/cm). 0이면 굽지 않고 종전처럼
    # 원본을 통째로 넣는다(삐져나온 부분은 양식 마스크에 의존).
    export_px_per_cm: float = 0
    rotation_range_deg: float
    rotation_step_deg: float
    # 사진이 창을 다 덮지 못할 때(회전·축소·이동) 드러나는 빈 공간 = 레터박스
    letterbox_color: str = "000000"   # PPT 슬롯 배경색 (RGB hex, '#' 없이)
    allow_letterbox: bool = True      # False면 빈 공간이 안 생기도록 배율 하한을 강제


class PptCfg(BaseModel):
    slot_names: list[str]
    info_box_name: str
    mask_prefix: str
    info_first_visit: str
    info_revisit: str
    info_date_format: str


class NoteField(BaseModel):
    key: str
    label: str
    hint: str = ""
    lines: int = 1        # 1이면 한 줄 입력, 그 이상이면 여러 줄
    # 화면에서 칸을 묶어 보여줄 이름. 'patient'는 첫 슬라이드(환자정보)로 가는
    # 칸이라 차수마다 바뀌는 값이 아니다 — 초진에서만 보인다.
    group: str = ""
    # 자동 계산값을 끼운 기본값 서식. {first_date} {today} {months} {visit}
    # {visit_label} 을 쓸 수 있다. 사람이 고치면 그쪽이 이긴다.
    default: str = ""


class NotesCfg(BaseModel):
    """차수 노트: 사용자가 채우는 칸과, 그 칸을 합쳐 박스에 적는 서식."""
    fields: list[NoteField] = []
    boxes: dict[str, str] = {}
    # 줄 끝 괄호(예: "Rx. Period: 23 month (24.08.12)" 의 날짜)에 쓸 글자 크기(pt).
    # 양식이 본문 15pt / 날짜 9pt 로 돼 있다. 0이면 줄이지 않는다.
    date_pt: float = 0
    # 줄 끝 괄호를 **줄이지 않을** 박스. 날짜 칸의 "(초진 A)" 는 날짜가 아니라
    # 차수 표시라 본문과 같은 크기여야 한다.
    date_pt_except: list[str] = []


class PatientInfoCfg(BaseModel):
    """양식 첫 슬라이드(환자정보)를 환자 정보로 채우는 규칙.

    줄 전체를 갈아끼우지 않고 **머리말 뒤만** 채운다 — 양식의 글꼴·줄간격·
    문단 서식을 그대로 살리기 위해서다. 머리말은 양식에 적힌 글자 그대로 적는다.
    """
    enabled: bool = False
    slide_no: int = 1
    # 머리말 → 그 뒤에 붙일 서식. {name} {hospital_id} {ortho_id} 와
    # notes.fields 의 칸(예: {sex} {age})을 쓸 수 있다.
    # 쓰이는 칸이 모두 비면 그 줄은 양식 그대로 둔다(안내 문구를 지우지 않는다).
    lines: dict[str, str] = {}


class CaseDeckCfg(BaseModel):
    """초진 덱 구성. 좌표는 담지 않는다 — 양식의 도형 기하에서 읽는다."""
    enabled: bool = False
    keep_slides: int = 16
    note_slide_no: int = 30
    face_slides: list[int] = []
    big_slides: list[int] = []
    slide_labels: dict[int, str] = {}   # 화면 안내용 (PPT에는 안 쓰인다)
    # 촬영순으로 세운 얼굴 사진이 들어갈 자리 순서 ("4L", "7C" …).
    # 비우면 자동 배치를 하지 않고 종전처럼 사람이 전부 고른다.
    face_auto_order: list[str] = []
    # 검수 판에 양식의 검은 띠를 그려 줄 슬라이드 (사진이 실제로 가리는 곳만)
    mask_slides: list[int] = []
    intraoral_slides: dict[str, int] = {}


class PerfCfg(BaseModel):
    """성능 손잡이. 없는 config.yaml 이면 전부 자동으로 산다."""
    # 슬롯별 정합/프레이밍 동시 실행 수. 0 = 자동(min(3, cpu//2)).
    # ONNX 세션 자체가 코어를 여럿 쓰므로 너무 키우면 서로 밟는다.
    pair_workers: int = 0


class Config(BaseModel):
    paths: Paths
    naming: NamingCfg
    identifiers: Identifiers
    classes: list[str]
    intraoral_slots: dict[str, SlotIndex]
    # 상하반전해서 보여줄 슬롯(교합면). 원본 파일은 건드리지 않고 화면에서만
    # 뒤집으며, PPT 에는 a:xfrm/@flipV 로 남긴다. 없는 설치본은 종전 동작.
    flip_v_slots: list[str] = []
    face: FaceCfg
    thresholds: Thresholds
    geometry: Geometry
    ppt: PptCfg
    perf: PerfCfg = PerfCfg()
    case_deck: CaseDeckCfg = CaseDeckCfg()
    notes: NotesCfg = NotesCfg()
    patient_info: PatientInfoCfg = PatientInfoCfg()

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
