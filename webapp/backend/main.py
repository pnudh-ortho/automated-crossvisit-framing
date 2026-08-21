"""
FastAPI 로컬 서버 — CRoCs Fastest Lap

완전 오프라인. 브라우저 단일 페이지 UI(localhost)와 통신.
세션 상태는 서버 메모리에 보관하고 업로드 사진은 세션 임시폴더에 저장한다.
확정(commit) 전에는 저장 폴더에 아무것도 쓰지 않는다.

본편(CRoCs)과의 차이 — 환자 관리도, PPT 도, 라벨·노트도 없다:

    업로드(기준/현재 두 풀) → 자동분류 → (재진이면 짝맞춤·정합) → 검수 → 이미지 저장

  · 초진/재진은 사용자가 선언하지 않는다 — 기준 사진 풀이 비었으면 초진이다.
  · 정합 기준은 PPT 가 아니라 **사용자가 올린 기준 사진**이다. 기준 사진을
    프레이밍 모델로 창에 구워 낸 것이 본편의 'PPT 복원 기준영상'과 같은 형태라,
    정합·겹쳐보기 수학은 본편 그대로 산다.
  · 반전(flip)은 슬롯이 아니라 **사진**에 달렸다. 기본값은 설정의
    카테고리×(기준/현재) 그리드에서 오고, 화면에서 사진별로 뒤집을 수 있다.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime
from pathlib import Path

# 슬롯별 정합을 스레드 풀로 병렬화하므로, ONNX 세션 하나가 코어를 전부 쥐면
# 서로 밟는다. 세션 생성 전에(=onnxruntime 이 임포트되기 전에) intra-op 스레드
# 상한을 환경으로 내려 둔다. 사용자가 직접 정해 뒀으면 그 값을 존중한다.
_CPU = os.cpu_count() or 4
_WORKERS_GUESS = max(1, min(3, _CPU // 2))
os.environ.setdefault("OMP_NUM_THREADS", str(max(1, _CPU // _WORKERS_GUESS)))

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config as C
import crop as Cr
import fl_names as FN
import framing as Fr
import registration_teeth as Reg   # 차수 간 정합 = 치아 중심점
import uninstaller as Un
import updater as Up
import storage as S
from classify import load_classifier
from coords import (EditorState, WindowCm, apply_cover_clamp, cover_base_ext_cm,
                    flip_editor_v, placement_from_photo_affine, placement_to_editor)

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

cfg = C.load_config()
classifier = load_classifier(cfg)
# 자동 프레이밍 모델. 없으면 None 이고 초기 배치는 cover-fit 으로 놓인다.
framer = Fr.load_framer(cfg)
# 십자뷰 슬롯 창 — 본편은 템플릿 PPT 도형에서 읽었지만 여기서는 config 값이다.
SLOT_WINDOWS: dict[str, WindowCm] = {
    k: WindowCm(x=v.x, y=v.y, w=v.w, h=v.h) for k, v in cfg.slot_windows.items()}
FACE_WINDOW = WindowCm(x=cfg.face_window.x, y=cfg.face_window.y,
                       w=cfg.face_window.w, h=cfg.face_window.h)
SLOT_NAMES = list(cfg.slot_names)

# 정합 모델을 기동 때 미리 올린다 — 첫 정합 요청만 수 초 느려지는 걸 막는다.
# 실패해도 서버는 뜬다: 정합을 건너뛰고 프레이밍 모델이 받는다.
_REG_READY = Reg.warmup()

PPC = cfg.geometry.render_px_per_cm


def _imread(path) -> np.ndarray | None:
    """한글·공백·특수문자 경로에 안전한 imread.

    Windows 의 `cv2.imread` 는 경로를 ANSI 로 열어서 비ASCII 경로에서 조용히
    None 을 돌려준다. 바이트를 파이썬이 읽고 cv2 는 디코드만 하게 하면
    경로 인코딩과 무관해진다.
    """
    try:
        return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    except Exception:                                             # noqa: BLE001
        return None


# ── 정합 결과 → 배치 환산 ─────────────────────────────────────────────────────
def _win_px_to_cm_affine(win: WindowCm) -> np.ndarray:
    return np.array([[1.0 / PPC, 0.0, win.x], [0.0, 1.0 / PPC, win.y]], np.float32)


def _clamp(st: EditorState, win: WindowCm, bw: float, bh: float) -> EditorState:
    """
    geometry.allow_letterbox가 false일 때만 배율 하한(cover clamp)을 건다.
    true면 사람이 손으로 자른 것과 같이 빈 공간(레터박스)이 생기도록 허용하고,
    그 자리는 저장 이미지에서 geometry.letterbox_color로 칠해진다.
    """
    if cfg.geometry.allow_letterbox:
        return st
    return apply_cover_clamp(st, win, bw, bh)


def registration_to_editor(M_new_to_winpx, win: WindowCm, pw, ph) -> EditorState:
    """정합 유사변환(new_px→창_px) → 편집기 상태(EditorState)."""
    Twin = _win_px_to_cm_affine(win)
    M3 = np.vstack([np.array(M_new_to_winpx, np.float32), [0, 0, 1]])
    T3 = np.vstack([Twin, [0, 0, 1]])
    A = (T3 @ M3)[:2, :]                       # new_px → cm
    pl = placement_from_photo_affine(A.tolist(), pw, ph)
    bw, bh = cover_base_ext_cm(pw, ph, win)
    st = placement_to_editor(pl, win, bw, bh, PPC)
    return _clamp(st, win, bw, bh)


def framing_to_editor(res: "Fr.FramingResult", win: WindowCm, pw, ph) -> EditorState:
    """프레이밍 예측(raw→canonical) → 편집기 상태.

    모델이 주는 T 는 "raw 픽셀 → 잘라낸 결과물" 이다. 결과물이 슬롯을 꽉 채우도록
    canonical→창픽셀 배율 하나만 앞에 붙이면 정합과 똑같은 모양이 되어, 아래는
    `registration_to_editor` 를 그대로 재사용한다.
    """
    cw, ch = res.canon_wh
    Wpx, Hpx = win.w * PPC, win.h * PPC
    k = min(Wpx / cw, Hpx / ch)
    C_can_to_winpx = np.array([[k, 0.0, (Wpx - k * cw) / 2.0],
                               [0.0, k, (Hpx - k * ch) / 2.0]], np.float64)
    T3 = np.vstack([np.array(res.matrix, np.float64), [0, 0, 1]])
    M = (np.vstack([C_can_to_winpx, [0, 0, 1]]) @ T3)[:2, :]   # raw px → 창 px
    return registration_to_editor(M, win, pw, ph)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """청소 스레드는 서버 수명과 함께 산다. TestClient를 컨텍스트 없이 쓰면 뜨지 않는다."""
    _log_framer()
    # 아이콘 이름이 바뀌었으면 바로가기를 수리한다. powershell 을 띄우는 일이라
    # 기동을 붙잡지 않게 옆으로 보낸다 — 서버가 뜨는 것과 아무 상관이 없다.
    # **테스트에서는 띄우지 않는다**: 설정 파일에 쓰는 스레드라, 테스트가 경로를
    # 바꿔치기했다 되돌리는 사이에 돌면 진짜 설정을 건드린다. 느리기도 하다.
    if "pytest" not in sys.modules:
        threading.Thread(target=_repair_shortcut_icon, daemon=True).start()
    stop = threading.Event()
    threading.Thread(target=_sweeper_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


def _log_framer() -> None:
    """자동 프레이밍 모델 상태를 기동 로그에 한 번 찍는다."""
    if framer is None:
        print("[프레이밍] 모델 없음 — 초기 배치는 cover-fit(회전 0, 중심)으로 놓입니다.")
        return
    n = {len(v) for v in framer.files.values()}
    kind = "배포본" if n == {1} else f"fold {max(n)}개 앙상블"
    print(f"[프레이밍] {framer.meta.get('tag')} / {kind} / "
          f"{framer.iw}x{framer.ih} / 클래스 {len(framer.files)}개")
    ph = framer.placeholder
    if ph:
        print(f"  ⚠ 임시 대역 모델입니다 — {ph.get('reason')}")
        print(f"    {ph.get('risk')}")
        print(f"    교체: {ph.get('replace_with')}")


app = FastAPI(title="CRoCs Fastest Lap", lifespan=_lifespan)

SESSIONS: dict[str, "Session"] = {}
# 저장 루트는 실행 중에 바뀔 수 있다(설정의 '저장 위치').
# config.yaml은 기본값, settings.json이 있으면 그쪽이 이긴다 — 주석 달린 YAML을
# 프로그램이 다시 쓰면 형식이 망가지므로 사용자 선택은 별도 파일에 둔다.
PROGRAM_DIR = BACKEND_DIR.parents[1]
SETTINGS_FILE = PROGRAM_DIR / "settings.json"


def _settings(path=None) -> dict:
    """설정 전체. 고쳐 쓸 것이라면 **경로를 붙잡아 넘겨라**.

    `SETTINGS_FILE` 은 모듈 전역이라 읽을 때와 쓸 때 각각 다시 참조된다. 그
    사이에 값이 바뀌면 *A 에서 읽어 B 에 쓰는* 일이 생긴다 — 본편에서 실제로,
    테스트가 이 전역을 임시 경로로 바꿔치기하는 동안 기동 스레드가 저장을 돌아
    빈 dict 를 진짜 설정 파일에 덮어썼다(저장 위치·이름 양식이 통째로 날아갔다).
    """
    try:
        d = json.loads((path or SETTINGS_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                                   # noqa: BLE001
        return {}


def _setting(key: str, default=None):
    """settings.json 의 값 하나. 파일이 없거나 깨졌으면 기본값."""
    return _settings().get(key, default)


def _save_setting(key: str, value) -> None:
    """값 하나만 얹는다 — **있던 설정을 지우지 않는다.**"""
    path = SETTINGS_FILE                       # 읽은 그 파일에만 쓴다
    d = _settings(path)
    d[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass


# ── 사용자 설정 (settings.json) — fastest_lap 의 개인화 전부 ──────────────────
# 기본값: 현재 사진의 교합면(거울 촬영)만 상하반전. 기준 사진은 대개 이미 처리된
# 결과물(본편이 구워 낸 크롭)이라 반전이 필요 없다.
FLIP_DEFAULTS = {"cur": {"IO_UPPER": True, "IO_LOWER": True}, "ref": {}}
FLIP_CLASSES = [c for c in cfg.classes if c != "OTHERS"]


def _flip_defaults() -> dict:
    saved = _setting("flip_defaults") or {}
    out = {}
    for pool in ("ref", "cur"):
        base = dict(FLIP_DEFAULTS.get(pool, {}))
        got = saved.get(pool)
        if isinstance(got, dict):
            base = {k: bool(v) for k, v in got.items() if k in FLIP_CLASSES}
        out[pool] = base
    return out


def _naming_prefs() -> dict:
    d = _setting("naming") or {}
    aliases = dict(FN.DEFAULT_ALIASES)
    got = d.get("aliases")
    if isinstance(got, dict):
        for k, v in got.items():
            if k in aliases and FN.sanitize(str(v)):
                aliases[k] = FN.sanitize(str(v))
    mode = d.get("number_mode")
    sep = d.get("separator")
    start = d.get("start")
    return {"number_mode": mode if mode in ("multi_only", "always") else "multi_only",
            "start": int(start) if start in (0, 1, "0", "1") else 1,
            "separator": sep if isinstance(sep, str) and 0 < len(sep) <= 3
                             and not (set(sep) & set('\\/:*?"<>|')) else "_",
            "aliases": aliases}


def _output_prefs() -> dict:
    d = _setting("output") or {}
    ppcm = d.get("px_per_cm")
    q = d.get("jpeg_quality")
    fmt = d.get("format")
    return {"px_per_cm": float(ppcm) if isinstance(ppcm, (int, float)) and 50 <= ppcm <= 400
                          else cfg.geometry.export_px_per_cm,
            "format": fmt if fmt in ("jpg", "png") else "jpg",
            "jpeg_quality": int(q) if isinstance(q, (int, float)) and 60 <= q <= 100 else 95,
            "save_extras": bool(d.get("save_extras")),
            # 끄면 검수 화면에서만 뒤집어 보고, 저장은 촬영된 방향(거울상) 그대로.
            # 크롭 영역은 같다 — 반전만 빼고 굽는다.
            "flip_save": bool(d.get("flip_save", True)),
            # 저장(=검수 창) 비율. 세션을 열 때 굳는다 — 작업 중간에 바뀌면
            # 이미 잡은 구도·기준영상이 다른 창 기준이 되기 때문이다.
            "io_ratio": d.get("io_ratio") if d.get("io_ratio") in IO_RATIOS else "4:3",
            "face_ratio": d.get("face_ratio") if d.get("face_ratio") in FACE_RATIOS else "3:4"}


# 저장 비율 후보. 폭(8.4cm)은 고정하고 높이를 비율로 푼다 — 해상도(px/cm)와
# 조합하면 출력 픽셀 크기가 그대로 정해진다.
IO_RATIOS = {"4:3": 4 / 3, "3:2": 3 / 2, "1:1": 1.0}
FACE_RATIOS = {"3:4": 3 / 4, "2:3": 2 / 3, "1:1": 1.0}


def _session_windows() -> tuple[dict[str, WindowCm], WindowCm]:
    """지금 설정 기준의 검수/저장 창. 세션이 열릴 때 한 번 찍어 세션에 넣는다."""
    outp = _output_prefs()
    r = IO_RATIOS[outp["io_ratio"]]
    wins = {k: WindowCm(x=v.x, y=v.y, w=v.w, h=round(v.w / r, 3))
            for k, v in SLOT_WINDOWS.items()}
    fr = FACE_RATIOS[outp["face_ratio"]]
    face = WindowCm(x=FACE_WINDOW.x, y=FACE_WINDOW.y, w=FACE_WINDOW.w,
                    h=round(FACE_WINDOW.w / fr, 3))
    return wins, face


def _after_save() -> dict:
    d = _setting("after_save") or {}
    return {"open_folder": bool(d.get("open_folder", True)),
            "auto_next": bool(d.get("auto_next", False))}


def _save_raw() -> bool:
    """원본을 함께 남길까. 기본은 **아니오**.

    켜면 원본 사본이 `raw/` 하위에 함께 남는다. 끄면 원본은 어디에도 남지
    않는다 — 확정과 함께 업로드 임시본이 지워지고, 잘라낸 영역은 되돌릴 수 없다.
    (개원의는 원본을 카메라/PC 에 따로 갖고 있는 것이 보통이다.)
    """
    return bool(_setting("save_raw"))


def _letterbox_color() -> str:
    """회전·축소로 드러나는 빈 자리를 채울 색 (RGB hex, '#' 없이).

    화면 캔버스와 저장되는 이미지가 **같은 값**을 봐야 한다. 갈리면 검수 화면에서
    본 모습과 결과물이 달라진다. 설정이 없으면 config 기본값.
    """
    v = _setting("letterbox_color")
    if isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", v):
        return v.upper()
    return cfg.geometry.letterbox_color


# ── 저장 위치(루트) ───────────────────────────────────────────────────────────
def _saved_roots() -> list[str]:
    """등록해 둔 저장 위치들. 현재 위치가 목록에 없으면 앞에 끼워 돌려준다."""
    d = _settings()
    out: list[str] = []
    for p in [*(d.get("roots") or []), d.get("root") or ""]:
        if isinstance(p, str) and p and p not in out:
            out.append(p)
    return out


def _write_roots(paths: list[str], current: str) -> None:
    """목록과 현재 위치를 저장한다. 다른 설정은 건드리지 않는다."""
    path = SETTINGS_FILE                       # 읽은 그 파일에만 쓴다
    d = _settings(path)
    d["roots"] = [p for p in paths if p]
    d["root"] = current
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _switch_root(p: Path) -> None:
    """현재 저장 위치를 바꾼다 — **함께 움직여야 하는 것들을 한자리에서**.

    감사 로그가 특히 그렇다. 저장 위치가 여럿이면 A 자료의 기록이 B 에 쌓이면
    안 된다. 열려 있던 세션은 옛 경로를 가리키므로 임시 업로드까지 함께 버린다.
    """
    global ROOT, SESS_ROOT, LOG_FILE
    for s in list(SESSIONS.values()):
        discard_session(s)
    ROOT = p.resolve()
    SESS_ROOT = ROOT / "_sessions_tmp"
    SESS_ROOT.mkdir(parents=True, exist_ok=True)
    if not cfg.paths.log_file:              # config 가 정한 자리가 있으면 그대로 둔다
        LOG_FILE = ROOT / "_audit_log.jsonl"


def _default_root() -> Path:
    r"""첫 실행에서 제시할 저장 위치.

    **프로그램 폴더 옆**이 기본이다 — 눈에 띄고, 프로그램을 어디에 깔든 말이 된다.
    다만 `Program Files` 처럼 쓸 수 없는 자리면 홈 폴더로 물러난다.
    """
    sib = PROGRAM_DIR.parent / (PROGRAM_DIR.name + "_data")
    try:
        sib.mkdir(parents=True, exist_ok=True)
        probe = sib / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return sib
    except OSError:
        return Path.home() / "crocs-fastest-lap" / "data"


def _saved_root_str() -> str:
    """설정에 적힌 저장 위치 — **있는지는 보지 않는다.**

    외장 드라이브에 자료를 둔 설치본이 있다. 드라이브를 빼면 그 경로는 잠깐
    사라질 뿐 잘못된 설정이 아니다. "아직 안 골랐다" 와 "골라 뒀는데 지금
    닿지 않는다" 를 가르려면 존재 여부를 빼고 읽는 길이 하나 필요하다.
    """
    v = _setting("root")
    return v if isinstance(v, str) else ""


def _saved_root() -> Path | None:
    """사용자가 고른 저장 위치. 없으면 config.yaml 기본값이 쓰인다."""
    try:
        p = _setting("root")
        return Path(p).expanduser().resolve() if p and Path(p).expanduser().is_dir() else None
    except Exception:                                   # noqa: BLE001
        return None


# 사용자가 고른 값 > config.yaml 이 비었으면 계산된 기본값 > config.yaml
ROOT = _saved_root() or (_default_root() if not cfg.paths.root
                         else Path(cfg.resolve(cfg.paths.root)).resolve())
# 이력 로그는 저장 루트와 함께 둔다 — 기록물이라 같이 백업돼야 한다
LOG_FILE = (Path(cfg.resolve(cfg.paths.log_file)) if cfg.paths.log_file
            else ROOT / "_audit_log.jsonl")
SESS_ROOT = ROOT / "_sessions_tmp"
SESS_ROOT.mkdir(parents=True, exist_ok=True)

_AUDIT_LOCK = threading.Lock()


def _audit(record: dict) -> None:
    """감사 로그 한 줄 — 병렬 정합 스레드에서도 부르므로 줄이 섞이지 않게 잠근다."""
    try:
        with _AUDIT_LOCK:
            S.append_audit(LOG_FILE, record)
    except Exception:                                   # noqa: BLE001
        pass          # 로그 실패가 작업을 막으면 안 된다


# ── 임시 업로드 보관 기한 ─────────────────────────────────────────────────────
# 확정 전 업로드 원본은 _sessions_tmp/<세션id>/ 에만 있다. 브라우저를 그냥 닫거나
# 서버가 재시작되면 이 폴더는 주인 없이 남는다. 정책은 하나로 둔다:
# "확정되지 않은 임시 업로드는 최대 48시간 보관한다."
SESSION_TTL = 48 * 3600      # 마지막 요청 이후 이만큼 지나면 세션을 버린다
SWEEP_INTERVAL = 600         # 청소 주기(초)


# ── 세션 모델 ─────────────────────────────────────────────────────────────────
class Photo:
    def __init__(self, pid, path, w, h, pool: str):
        self.id = pid
        self.path = Path(path)
        self.w, self.h = w, h
        self.pool = pool           # 'ref'(정합용 기준) | 'cur'(현재)
        self.orig_name = None
        self.label = None
        self.confidence = 0.0
        self.probs = {}
        self.slot = None           # 배정된 슬롯(SLOT_*) 또는 'FACE' 또는 None
        # 상하반전. 슬롯이 아니라 **사진**에 달렸다 — 기본값은 설정의 그리드에서
        # 오고(분류 시 적용), 사람이 화면에서 사진별로 뒤집을 수 있다. 원본 파일은
        # 그대로 두고 표시·정합·저장이 이 값을 따른다. editor 값은 반전 프레임
        # 기준으로 들고 있다 (본편의 flip_v 규약 그대로).
        self.flip = False
        self.flip_user = False     # 사람이 직접 토글했으면 기본값이 덮지 않는다
        self.editor = EditorState()
        # **initial-fit** — 자동으로 잡아 준 첫 구도. 짝이 있으면 정합 결과,
        # 없으면 프레이밍 모델의 예측이고, 둘 다 못 쓰면 cover-fit 이다.
        # 사람이 손으로 만진 뒤 '되돌리기'가 돌아갈 자리라, editor 와 달리
        # **조작으로 바뀌지 않는다** — _frame_slot 이 다시 계산할 때만 갱신된다.
        self.editor0 = EditorState()
        self.badge = "ok"          # ok | low | manual | missing
        self.taken_at = None       # EXIF 촬영시각, 서브초 있으면 microsecond까지
        self.exif_seq = None       # EXIF ImageNumber (대개 없다)
        # 이 배치가 어디서 왔는가: model=프레이밍 예측, registration=정합,
        # cover=예측을 기각하고 cover-fit, None=모델 없음
        self.framing = None
        self.framing_note = None   # 기각 사유 등 (검수화면 툴팁용)


class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.tmp = SESS_ROOT / self.id
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.touched = time.time()   # 마지막 요청 시각 — 청소 기준

        self.folder = ""             # 저장될 폴더 이름 (ROOT 바로 아래)
        self.prefix = ""             # 파일명 접두어. 비우면 폴더 이름을 따른다
        # 이 세션의 검수/저장 창 — 여는 시점의 비율 설정으로 굳는다
        self.slot_windows, self.face_window = _session_windows()
        self.photos: list[Photo] = []
        # 상자 = 순서 있는 목록. 0번이 대표. 기준/현재 풀이 각자 한 벌씩 갖는다.
        self.ref_bins: dict[str, list[str]] = {}   # 'SLOT_*'|'FACE' -> [photo_id]
        self.cur_bins: dict[str, list[str]] = {}
        # 어느 슬롯을 어떤 (현재 사진, 반전, 기준 열쇠)로 계산했나 — 재진입 시
        # 그대로면 건너뛴다. 좌·우를 고치고 돌아오면 그 두 칸만 다시 돈다.
        self.framed: dict[str, tuple] = {}
        # 얼굴은 사진 단위 — pid -> 계산 당시 반전값
        self.face_framed: dict[str, bool] = {}
        # 정합 기준영상: slot -> 창에 구워 낸 기준 사진 (PPC 해상도).
        # 본편의 'PPT 복원 기준영상'과 같은 형태다.
        self.references: dict[str, np.ndarray] = {}
        self.ref_src: dict[str, tuple] = {}   # slot -> (ref_pid, flip) 베이크 근거
        # 정합 진행 상태 — 병렬로 도는 동안 화면이 이걸 폴링해 라벨별로 보여준다.
        # 값: wait | refs | run | reg | frame | fallback | error
        self.progress: dict[str, str] = {}
        # 병렬 정합·백그라운드 작업이 세션 상태를 같이 만지므로 잠근다.
        self.lock = threading.RLock()

    @property
    def mode(self) -> str:
        """기준 사진이 하나라도 있으면 재진 — 사용자가 선언하지 않는다."""
        return "revisit" if any(p.pool == "ref" for p in self.photos) else "first"

    @property
    def slots(self) -> dict[str, str]:
        """슬롯별 현재 사진 대표. 읽기 전용 — 쓰기는 cur_bins로 한다."""
        return {k: v[0] for k, v in self.cur_bins.items() if k != "FACE" and v}

    @property
    def ref_slots(self) -> dict[str, str]:
        return {k: v[0] for k, v in self.ref_bins.items() if k != "FACE" and v}

    @property
    def face(self) -> list[str]:
        return self.cur_bins.get("FACE", [])


def get_session(sid) -> Session:
    # 410은 "있었는데 사라졌다" — 프론트가 경로/사진 없음(404)과 구분해 안내한다.
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(410, "세션이 만료되었거나 존재하지 않습니다")
    s.touched = time.time()
    return s


# ── 임시 폴더 청소 ────────────────────────────────────────────────────────────
def discard_session(s: "Session") -> None:
    """세션을 버리고 임시 업로드도 함께 지운다."""
    SESSIONS.pop(s.id, None)
    shutil.rmtree(s.tmp, ignore_errors=True)


def sweep_sessions(now: float | None = None) -> int:
    """기한 지난 세션과 고아 폴더를 지운다. 반환값은 지운 폴더 수."""
    now = time.time() if now is None else now
    n = 0
    for s in list(SESSIONS.values()):
        if now - s.touched > SESSION_TTL:
            discard_session(s)
            n += 1
    live = {s.id for s in SESSIONS.values()}
    if not SESS_ROOT.is_dir():
        return n
    for d in SESS_ROOT.iterdir():
        if not d.is_dir() or d.name in live:
            continue
        try:
            if now - d.stat().st_mtime > SESSION_TTL:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


def _sweeper_loop(stop: threading.Event) -> None:
    while not stop.wait(SWEEP_INTERVAL):
        try:
            sweep_sessions()
        except Exception:                               # noqa: BLE001
            pass          # 청소가 실패해도 서버는 계속 돌아야 한다


# ── 병렬 정합 풀 ─────────────────────────────────────────────────────────────
def _pair_workers() -> int:
    n = cfg.perf.pair_workers
    if n and n > 0:
        return min(int(n), 8)
    return max(1, min(3, (os.cpu_count() or 2) // 2))


EXEC = ThreadPoolExecutor(max_workers=_pair_workers(),
                          thread_name_prefix="flap-reg")


# ── 스키마 ────────────────────────────────────────────────────────────────────
class SessionReq(BaseModel):
    folder: str = ""
    prefix: str = ""


class NamesReq(BaseModel):
    folder: str
    prefix: str = ""


class AssignReq(BaseModel):
    session_id: str
    photo_id: str
    slot: str | None          # 'SLOT_*' | 'FACE' | None(=OTHERS로 빼기)
    at: int | None = None     # 0이면 대표 자리로


class AdjustReq(BaseModel):
    session_id: str
    slot: str                 # 'SLOT_*' 또는 'FACE:<photo_id>'
    dx: float
    dy: float
    scale: float
    angle: float


class FlipReq(BaseModel):
    session_id: str
    photo_id: str
    on: bool


class SortReq(BaseModel):
    session_id: str
    slot: str                 # 'FACE' | 'SLOT_*'
    pool: str = "cur"


class RegisterReq(BaseModel):
    slots: list[str] | None = None    # None 이면 배정된 슬롯 전부
    force: bool = False               # 이미 계산한 자리도 다시


class CommitReq(BaseModel):
    # 저장 검토에서 "덮어쓰기"로 고른 파일 이름들 (폴더 기준 상대경로).
    # 목록에 없는 충돌 파일은 자동으로 뒤에 번호가 붙는다.
    overwrite: list[str] = []


# ── 정적/루트 ─────────────────────────────────────────────────────────────────
# 화면 파일은 캐시하지 않는다(no-cache = 쓰기 전에 반드시 확인). 이 앱은
# `git pull` 로 자기를 갱신하므로 옛 화면 캐시는 상시 위험이다.
NO_CACHE = {"Cache-Control": "no-cache"}


@app.middleware("http")
async def _no_store_api(request, call_next):
    """API 응답은 저장하지 않는다 — 조회할 때마다 답이 달라진다."""
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html", headers=NO_CACHE)


class _NoCacheStatic(StaticFiles):
    """`/static/*` 도 매번 확인시킨다. 바뀐 게 없으면 304 로 끝난다."""

    def file_response(self, *args, **kwargs):
        r = super().file_response(*args, **kwargs)
        r.headers.setdefault("Cache-Control", "no-cache")
        return r


@app.get("/api/health")
def health():
    fr = {"loaded": False}
    if framer is not None:
        fr = {"loaded": True, "tag": framer.meta.get("tag"),
              "input": [framer.iw, framer.ih],
              "classes": sorted(framer.files),
              "models_per_class": {c: len(v) for c, v in framer.files.items()},
              "placeholder": framer.placeholder}
    return {"ok": True, "classifier": type(classifier).__name__,
            "app": "fastest_lap",
            "needs_setup": not _saved_root() and not _saved_root_str(),
            "root_missing": ("" if _saved_root() else _saved_root_str()),
            "root": str(ROOT), "program_dir": str(PROGRAM_DIR),
            "framing": fr,
            "classes": cfg.classes, "slots": SLOT_NAMES,
            "px_per_cm": PPC,
            "rotation_range_deg": cfg.geometry.rotation_range_deg,
            "windows": {k: {"x": v.x, "y": v.y, "w": v.w, "h": v.h}
                        for k, v in SLOT_WINDOWS.items()},
            "face_window": {"w": FACE_WINDOW.w, "h": FACE_WINDOW.h}}


# ── 폴더 선택 창 (저장 위치 고르기) ───────────────────────────────────────────
_PS_PICK = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$o = New-Object System.Windows.Forms.Form; "
    "$o.TopMost = $true; $o.ShowInTaskbar = $false; "
    "$o.StartPosition = 'CenterScreen'; $o.Size = '1,1'; $o.Show(); "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$d.Description = '사진을 저장할 폴더'; "
    "$d.ShowNewFolderButton = $true; "
    "if ($env:ACF_START) { $d.SelectedPath = $env:ACF_START }; "
    "$r = $d.ShowDialog($o); $o.Close(); "
    "if ($r -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
)

_TK_PICK = r"""
import sys, tkinter as tk
from tkinter import filedialog
r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)
print(filedialog.askdirectory(title='사진을 저장할 폴더',
                              initialdir=sys.argv[1] or None) or '')
"""


def _powershell() -> str | None:
    """Windows 의 powershell. WSL 에서도 interop 으로 그대로 부를 수 있다."""
    if os.name == "nt":
        return "powershell"
    return shutil.which("powershell.exe")


def _to_local(path: str) -> str:
    """Windows 경로 → 이 프로세스가 쓸 수 있는 경로 (WSL 이면 /mnt/c/...)."""
    if os.name == "nt" or not path:
        return path
    try:
        r = subprocess.run(["wslpath", "-u", path], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return path


@app.get("/api/pick-folder")
def pick_folder(start: str = ""):
    """운영체제의 **폴더 선택 창**을 띄우고 고른 경로를 돌려준다."""
    ps = _powershell()
    try:
        if ps:
            env = {**os.environ}
            if start:
                w = subprocess.run(["wslpath", "-w", start], capture_output=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=10) if os.name != "nt" else None
                env["ACF_START"] = (w.stdout.strip() if w and w.returncode == 0
                                    else start)
            r = subprocess.run([ps, "-NoProfile", "-STA", "-Command", _PS_PICK],
                               capture_output=True, encoding="utf-8",
                               errors="replace", timeout=600, env=env)
            path = (r.stdout or "").strip().replace("\r", "").strip()
            if r.returncode != 0 and not path:
                return {"ok": False, "detail": (r.stderr or "").strip()[-200:]
                        or "창을 띄우지 못했습니다"}
            if not path:
                return {"ok": False, "cancelled": True}
            return {"ok": True, "path": _to_local(path), "shown": path}
        r = subprocess.run([sys.executable, "-c", _TK_PICK, start],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=600,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if r.returncode != 0:
            return {"ok": False, "detail": (r.stderr or "").strip()[-200:]
                    or "창을 띄우지 못했습니다"}
        path = (r.stdout or "").strip()
        return ({"ok": True, "path": path, "shown": path} if path
                else {"ok": False, "cancelled": True})
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}"}


# ── 저장 위치(루트) 고르기 ────────────────────────────────────────────────────
def _fs_roots() -> list[str]:
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return ["/"]


@app.get("/api/fs")
def fs(path: str = ""):
    base = Path(path).expanduser() if path else ROOT
    if not base.is_dir():
        raise HTTPException(404, f"폴더가 없습니다: {base}")
    base = base.resolve()
    try:
        dirs = [{"name": p.name, "path": str(p)}
                for p in sorted(base.iterdir(), key=lambda x: x.name.lower())
                if p.is_dir() and not p.name.startswith(".")]
    except PermissionError:
        raise HTTPException(403, f"접근 권한이 없습니다: {base}")
    return {"path": str(base),
            "parent": str(base.parent) if base.parent != base else None,
            "dirs": dirs, "drives": _fs_roots(), "current_root": str(ROOT)}


class MkdirReq(BaseModel):
    path: str
    name: str


@app.post("/api/fs/mkdir")
def fs_mkdir(req: MkdirReq):
    """저장 위치 픽커에서 새 폴더 만들기 — 탐색기로 나갔다 올 필요가 없게."""
    base = Path(req.path).expanduser()
    if not base.is_dir():
        raise HTTPException(404, f"폴더가 없습니다: {base}")
    name = req.name.strip().rstrip(".")
    if not name or set(chr(92) + '/:*?"<>|') & set(name):
        raise HTTPException(400, '폴더 이름이 비었거나 \\ / : * ? " < > | 가 들어 있습니다')
    p = (base / name).resolve()
    if p.exists():
        if p.is_dir():
            return {"path": str(p), "existed": True}
        raise HTTPException(400, f"같은 이름의 파일이 있습니다: {name}")
    try:
        p.mkdir()
    except OSError as e:
        raise HTTPException(400, f"만들지 못했습니다: {e}")
    return {"path": str(p), "existed": False}


@app.post("/api/root/recheck")
def root_recheck():
    """사라졌던 저장 위치가 돌아왔는지 다시 본다 — 드라이브를 꽂고 누른다."""
    saved = _saved_root()
    if saved is None:
        return {"ok": False, "path": _saved_root_str(), "root": str(ROOT)}
    _switch_root(saved)
    return {"ok": True, "root": str(ROOT)}


class RootReq(BaseModel):
    path: str


@app.post("/api/root")
def set_root(req: RootReq):
    """저장 위치 변경. 열려 있던 세션은 옛 경로를 가리키므로 함께 버린다."""
    p = Path(req.path).expanduser()
    if not p.is_dir():
        if not p.parent.is_dir():
            raise HTTPException(400, f"상위 폴더가 없습니다: {p.parent}")
        try:
            p.mkdir(parents=False)
        except OSError as e:
            raise HTTPException(400, f"폴더를 만들 수 없습니다: {p} ({e.strerror})")
    _switch_root(p)
    roots = _saved_roots()
    if str(ROOT) not in roots:
        roots.append(str(ROOT))
    _write_roots(roots, str(ROOT))
    return {"root": str(ROOT), "roots": _roots_json()}


def _roots_json() -> list[dict]:
    """목록 한 줄씩 — 지금 보고 있는 곳인가, 지금 닿는 곳인가."""
    cur = str(ROOT)
    return [{"path": p, "current": p == cur,
             "exists": Path(p).expanduser().is_dir()}
            for p in _saved_roots()]


@app.get("/api/roots")
def roots_list():
    return {"roots": _roots_json(), "current": str(ROOT)}


class RootSelReq(BaseModel):
    path: str


@app.post("/api/root/select")
def root_select(req: RootSelReq):
    """등록해 둔 위치로 갈아탄다. 목록에 없는 곳은 받지 않는다."""
    if req.path not in _saved_roots():
        raise HTTPException(400, "목록에 없는 저장 위치입니다")
    p = Path(req.path).expanduser()
    if not p.is_dir():
        raise HTTPException(404, f"지금은 닿지 않는 위치입니다: {p}")
    _switch_root(p)
    _write_roots(_saved_roots(), str(ROOT))
    return {"root": str(ROOT), "roots": _roots_json()}


@app.post("/api/root/forget")
def root_forget(req: RootSelReq):
    """목록에서만 뺀다 — 폴더와 그 안의 자료는 그대로 둔다."""
    if req.path == str(ROOT):
        raise HTTPException(400, "지금 쓰는 위치는 뺄 수 없습니다")
    _write_roots([p for p in _saved_roots() if p != req.path], str(ROOT))
    return {"roots": _roots_json()}


# ── 세션 ─────────────────────────────────────────────────────────────────────
def _check_folder_name(name: str) -> str:
    got = FN.sanitize(name)
    if not got:
        raise HTTPException(400, '폴더 이름이 비었거나 \\ / : * ? " < > | 가 들어 있습니다')
    return got


@app.post("/api/session")
def session_open(req: SessionReq = Body(default=SessionReq())):
    """세션 시작. 폴더·접두어는 나중에(`/api/session/{sid}/names`) 채워도 된다 —
    사진부터 끌어다 놓는 흐름을 막지 않는다. 저장 검토 전까지만 정해지면 된다."""
    s = Session()
    if req.folder:
        s.folder = _check_folder_name(req.folder)
    if req.prefix:
        s.prefix = FN.sanitize(req.prefix) or ""
    SESSIONS[s.id] = s
    return {"session_id": s.id, "mode": s.mode,
            "windows": {k: {"x": v.x, "y": v.y, "w": v.w, "h": v.h}
                        for k, v in s.slot_windows.items()},
            "face_window": {"w": s.face_window.w, "h": s.face_window.h}}


@app.post("/api/session/{sid}/names")
def session_names(sid: str, req: NamesReq):
    s = get_session(sid)
    s.folder = _check_folder_name(req.folder)
    s.prefix = FN.sanitize(req.prefix) or ""
    return {"folder": s.folder, "prefix": s.prefix or s.folder,
            "folder_exists": (ROOT / s.folder).is_dir()}


@app.delete("/api/session/{sid}")
def session_close(sid: str):
    s = SESSIONS.get(sid)
    if s:
        discard_session(s)
    return {"ok": True}


# ── 업로드 (EXIF 처리는 본편 그대로) ─────────────────────────────────────────
EXIF_ORIENT, EXIF_SUB_IFD = 274, 0x8769
EXIF_DT_TAGS = (36867, 36868)      # DateTimeOriginal, DateTimeDigitized
EXIF_SUBSEC = {36867: 37521, 36868: 37522, 306: 37520}
EXIF_IMAGE_NUMBER = 37393          # ImageNumber — 기록하지 않는 기종이 더 많다
LOSSLESS_EXT = {".jpg", ".jpeg"}
_DIGITS = re.compile(r"\d+")


def _subsec_us(raw) -> int:
    """SubSecTime('83', '0421' …) → microsecond. 소수점 이하 자릿수를 그대로 해석."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())[:6]
    return int(digits.ljust(6, "0")) if digits else 0


def _exif_facts(im) -> tuple[int, datetime | None, int | None]:
    """(회전 플래그, 촬영시각, 일련번호). 읽을 수 없으면 (1, None, None)."""
    try:
        ex = im.getexif()
    except Exception:                                   # noqa: BLE001
        return 1, None, None
    orient = ex.get(EXIF_ORIENT) or 1
    sub = {}
    try:
        sub = ex.get_ifd(EXIF_SUB_IFD) or {}
    except Exception:                                   # noqa: BLE001
        pass
    raw_seq = sub.get(EXIF_IMAGE_NUMBER, ex.get(EXIF_IMAGE_NUMBER))
    try:
        seq = int(raw_seq) if raw_seq is not None else None
    except (TypeError, ValueError):
        seq = None
    for tag, src in ((EXIF_DT_TAGS[0], sub), (EXIF_DT_TAGS[1], sub), (306, ex)):
        raw = src.get(tag)
        if not raw:
            continue
        try:
            dt = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        us = _subsec_us(src.get(EXIF_SUBSEC[tag], sub.get(EXIF_SUBSEC[tag])))
        return orient, dt.replace(microsecond=us), seq
    return orient, None, seq


def _name_seq(name: str | None) -> int:
    """파일명 안의 마지막 숫자 뭉치 (IMG_1234.JPG → 1234). 없으면 −1."""
    nums = _DIGITS.findall(Path(name or "").stem)
    return int(nums[-1]) if nums else -1


def _shot_order_key(p: "Photo", idx: int) -> tuple:
    """촬영 순서 정렬 키.

    EXIF 가 부실한 기종을 위해 단계적으로 물러난다:
      1) 촬영시각(서브초까지) 2) EXIF 일련번호 3) 파일명 끝 숫자 4) 업로드 순서
    """
    return (0 if p.taken_at else 1,          # 시각을 아는 사진이 앞
            p.taken_at or datetime.min,
            p.exif_seq if p.exif_seq is not None else -1,
            _name_seq(p.orig_name),
            idx)


async def _stage_photos(s: "Session", files: list[UploadFile],
                        pool: str) -> list[Photo]:
    """세션 임시폴더에 저장만 한다. 분류는 하지 않는다."""
    from PIL import Image as _Im, ImageOps as _Ops              # noqa: PLC0415
    staged = []
    for uf in files:
        data = await uf.read()
        try:
            with _Im.open(io.BytesIO(data)) as im:
                im.load()
                orient, taken, seq = _exif_facts(im)
                pid = uuid.uuid4().hex[:10]
                dst = s.tmp / f"{pid}.jpg"
                ext = Path(uf.filename or "").suffix.lower()
                # 확장자가 아니라 **실제 포맷**을 본다. .jpg 로 저장된 MPO 를
                # 그대로 두면 나중에 터진다.
                if (ext in LOSSLESS_EXT and im.format == "JPEG"
                        and orient in (0, 1)):
                    dst.write_bytes(data)          # 원본 그대로 — EXIF·화질 보존
                    pw, ph = im.size
                else:
                    fixed = _Ops.exif_transpose(im)
                    fixed.convert("RGB").save(dst, "JPEG", quality=95, subsampling=0,
                                              exif=fixed.getexif().tobytes())
                    pw, ph = fixed.size
        except Exception:                               # noqa: BLE001
            continue                                # 이미지가 아니거나 깨진 파일
        photo = Photo(pid, dst, pw, ph, pool)
        photo.orig_name = uf.filename or dst.name
        photo.taken_at = taken
        photo.exif_seq = seq
        # 촬영시각을 파일 시각으로 새겨 둔다. 확정 때 그대로 따라가서 저장 폴더의
        # 사진이 "찍은 날"을 갖게 된다.
        if taken:
            ts = taken.timestamp()
            os.utime(dst, (ts, ts))
        with s.lock:
            s.photos.append(photo)
        staged.append(photo)
    return staged


def _prewarm_ref(s: "Session", photo: Photo) -> None:
    """기준 사진 한 장을 **미리** 데운다 — 분류·세그멘테이션 캐시.

    사용자가 나머지 사진을 올리고 분류를 검수하는 동안 뒤에서 돌아, 정합 버튼을
    눌렀을 때 기준 쪽 무거운 계산(291MB 분할 모델)이 이미 끝나 있게 한다.
    결과를 세션에 확정하지는 않는다 — 대표 선정은 분류·검수가 정하는 일이고,
    여기서는 내용 주소 캐시(Reg.centers 의 이미지 해시)만 채운다. 같은 사진이면
    나중의 베이크가 같은 픽셀을 만들므로 캐시가 맞는다.
    """
    try:
        if photo.label is None:
            from PIL import Image as _Im                          # noqa: PLC0415
            with _Im.open(photo.path) as im:
                pred = classifier.predict(im.copy(), filename=photo.orig_name)
            with s.lock:
                if photo.label is None:
                    photo.label = pred.label
                    photo.confidence, photo.probs = pred.confidence, pred.probs
        slot = cfg.slot_by_class.get(photo.label or "")
        if slot is None:
            return                       # FACE·OTHERS 기준은 정합에 안 쓰인다
        arr = _imread(photo.path)
        if arr is None:
            return
        with s.lock:
            _apply_default_flip(s, photo)
        # _ref_bake 와 **똑같이** 굽는다 — 픽셀이 한 톨이라도 다르면 이미지
        # 해시가 달라져 캐시가 안 맞고, 데운 보람이 없어진다.
        win = s.slot_windows[slot]
        img = Cr.render_window(arr, win, _contain_state(photo.w, photo.h, win),
                               photo.flip, PPC, PPC, Cr.hex_to_bgr(_letterbox_color()))
        Reg.centers(img, use_gate=True)  # 해시 키 캐시 — 정합 때 그대로 적중
    except Exception as e:                                        # noqa: BLE001
        _audit({"event": "prewarm_error", "pid": photo.id,
                "error": f"{type(e).__name__}: {e}"[:200]})


@app.post("/api/photos/{sid}")
async def add_photos(sid: str, pool: str = "cur",
                     files: list[UploadFile] = File(...)):
    if pool not in ("ref", "cur"):
        raise HTTPException(400, "pool 은 ref 또는 cur 이어야 합니다")
    s = get_session(sid)
    staged = await _stage_photos(s, files, pool)
    # 기준 사진은 스테이징 즉시 뒤에서 데운다 — 정합 대기 시간이 그만큼 준다.
    if pool == "ref":
        for p in staged:
            EXEC.submit(_prewarm_ref, s, p)
    return {"added": len(staged), "mode": s.mode,
            "photos": [_photo_json(s, p) for p in s.photos]}


@app.delete("/api/photos/{sid}/{pid}")
def drop_photo(sid: str, pid: str):
    s = get_session(sid)
    photo = _photo(s, pid)
    with s.lock:
        _detach(s, photo)
        s.photos = [p for p in s.photos if p.id != pid]
        _invalidate(s, photo)
    photo.path.unlink(missing_ok=True)
    return {"mode": s.mode, "photos": [_photo_json(s, p) for p in s.photos]}


# ── 분류 ─────────────────────────────────────────────────────────────────────
@app.post("/api/classify/{sid}")
def classify_session(sid: str):
    """투입된 사진(양쪽 풀)을 분류하고 상자를 자동 배정한다.

    **정합·프레이밍은 안 한다** — `/api/register` 가 한다. 기준영상이 자리마다
    다르므로 배정이 확정되기 전에 계산하면 값이 틀리기 때문이다(본편과 동일).
    """
    s = get_session(sid)
    _classify(s, s.photos)
    return {"photos": [_photo_json(s, p) for p in s.photos], "review": _review_json(s)}


# 교합면 두 클래스. 이 둘만 위아래 방향에 좌우된다 — 나머지 넷은 뒤집어도
# 라벨이 그대로였다(실측 18/18장).
OCCLUSAL = ("IO_UPPER", "IO_LOWER")


def _occlusal_mirrored(pool: str) -> bool:
    """이 풀의 교합면이 **거울 원본**인가 — 설정의 상하반전 값이 그 선언이다.

    켜져 있으면 "이 사진은 뒤집어야 한다" = 거울로 찍은 원본 = 분류기가 배운
    방향이다. 꺼져 있으면 이미 뒤집어 저장한 사진이라 분류기에게는 낯선 방향이다.
    상·하악은 늘 함께 찍으므로 두 값은 짝처럼 움직인다 — 하나라도 켜져 있으면
    거울 원본으로 본다.
    """
    grid = _flip_defaults().get(pool, {})
    return any(bool(grid.get(c)) for c in OCCLUSAL)


def _classify(s: "Session", targets: list[Photo]) -> None:
    """라벨을 붙이고 상자에 넣는다. **여기까지가 가볍다.**"""
    thr = cfg.thresholds
    from PIL import Image as _Im                        # noqa: PLC0415
    for photo in targets:
        if photo.label is not None:
            continue                     # prewarm 이 이미 분류했다 — 픽셀은 안 변한다
        with _Im.open(photo.path) as _im:
            pred = classifier.predict(_im.copy(), filename=photo.orig_name)
        photo.label, photo.confidence, photo.probs = pred.label, pred.confidence, pred.probs

    # 교합면만 방향을 탄다. 분류기는 **거울 원본**으로 배웠는데, 이미 뒤집어
    # 저장한 사진(설정에서 상하반전을 끈 풀)은 그 반대 방향이라 상·하악이
    # 뒤바뀐다 — 실측에서 6장 중 4장이 뒤집혔고 둘은 0.98 로 자신 있게 틀렸다.
    # 그런 풀에서만 **뒤집어서 다시 읽는다**. 화면·저장 방향은 설정 그대로다.
    for photo in targets:
        if photo.label not in OCCLUSAL or _occlusal_mirrored(photo.pool):
            continue
        with _Im.open(photo.path) as _im:
            pred = classifier.predict(_im.copy().transpose(_Im.FLIP_TOP_BOTTOM),
                                      filename=photo.orig_name)
        photo.label, photo.confidence, photo.probs = pred.label, pred.confidence, pred.probs

    flips = _flip_defaults()
    slot_by_class = cfg.slot_by_class
    with s.lock:
        for photo in targets:
            if photo.label in slot_by_class:
                _put(s, photo, slot_by_class[photo.label])
            elif photo.label in cfg.face.classes:
                _put(s, photo, "FACE")
            # 그 외(OTHERS 등)는 미배정으로 남긴다 — 화면에서 수동 배정/제외 가능
            if photo.confidence < thr.classify_confidence:
                photo.badge = "low"
        # 같은 클래스가 여러 장이면 경쟁시키지 않고 한 상자에 쌓는다.
        # 상자 안은 신뢰도 내림차순 — 맨 위가 대표가 된다.
        for bins in (s.ref_bins, s.cur_bins):
            for key, lst in bins.items():
                if key != "FACE":
                    lst.sort(key=lambda pid: -_photo(s, pid).confidence)
            # 얼굴은 촬영 순서 — 저장 번호가 곧 촬영 흐름이 되게.
            lst = bins.get("FACE", [])
            seen = {pid: i for i, pid in enumerate(lst)}
            lst.sort(key=lambda pid: _shot_order_key(_photo(s, pid), seen[pid]))
    # 기본 반전 적용 — 사람이 손대지 않은 사진만. (_put 이후: 상자가 정해져야
    # 카테고리도 정해진다.)
    for photo in targets:
        _apply_default_flip(s, photo, flips)


def _category_of(photo: Photo) -> str | None:
    """상자 열쇠 → 분류 카테고리. 미배정이면 라벨을 그대로 쓴다."""
    if photo.slot == "FACE":
        return "FACE"
    if photo.slot in cfg.class_by_slot:
        return cfg.class_by_slot[photo.slot]
    return photo.label


def _apply_default_flip(s: "Session", photo: Photo, flips: dict | None = None) -> None:
    """설정 그리드의 기본 반전을 적용한다. 사람이 토글한 사진은 건드리지 않는다."""
    if photo.flip_user:
        return
    flips = flips or _flip_defaults()
    want = bool(flips.get(photo.pool, {}).get(_category_of(photo) or "", False))
    if want != photo.flip:
        photo.flip = want
        photo.editor = flip_editor_v(photo.editor)
        photo.editor0 = flip_editor_v(photo.editor0)
        _drop_eff_cache(s, photo)


# ── 배정 조작 ─────────────────────────────────────────────────────────────────
def _photo(s, pid) -> Photo:
    for p in s.photos:
        if p.id == pid:
            return p
    raise HTTPException(404, "사진 없음")


def _bins_of(s: "Session", photo: Photo) -> dict[str, list[str]]:
    return s.ref_bins if photo.pool == "ref" else s.cur_bins


def _detach(s, photo: Photo) -> None:
    """어느 상자에 있든 빼낸다."""
    for lst in _bins_of(s, photo).values():
        if photo.id in lst:
            lst.remove(photo.id)
    photo.slot = None


def _put(s, photo: Photo, key, at=None) -> None:
    """상자에 넣는다. at=0이면 대표 자리, None이면 맨 뒤."""
    bins = _bins_of(s, photo)
    old = next(((k, v.index(photo.id)) for k, v in bins.items() if photo.id in v), None)
    _detach(s, photo)
    lst = bins.setdefault(key, [])
    idx = len(lst) if at is None else max(0, min(int(at), len(lst) + 1))
    if old and old[0] == key and old[1] < idx:
        idx -= 1          # 같은 상자 안에서 아래로 옮길 때 인덱스가 한 칸 당겨진다
    lst.insert(min(idx, len(lst)), photo.id)
    photo.slot = key
    # 카테고리가 바뀌면 기본 반전도 그 카테고리를 따른다(교합면 ↔ 정면 이동 등).
    _apply_default_flip(s, photo)


def _invalidate(s: "Session", photo: Photo) -> None:
    """사진의 상태(반전·배정·삭제)가 바뀌었을 때 그와 얽힌 계산 결과를 버린다."""
    if photo.pool == "ref":
        for slot, (pid, _fl) in list(s.ref_src.items()):
            if pid == photo.id:
                s.references.pop(slot, None)
                s.ref_src.pop(slot, None)
                s.framed.pop(slot, None)
    else:
        for slot, key in list(s.framed.items()):
            if key and key[0] == photo.id:
                s.framed.pop(slot, None)
        s.face_framed.pop(photo.id, None)


@app.post("/api/assign")
def assign(req: AssignReq):
    """상자 사이 이동. at=0이면 대표(검수에 들어갈 사진)로 올린다."""
    s = get_session(req.session_id)
    photo = _photo(s, req.photo_id)
    with s.lock:
        if req.slot:
            if req.slot != "FACE" and req.slot not in SLOT_WINDOWS:
                raise HTTPException(400, f"모르는 상자입니다: {req.slot}")
            _put(s, photo, req.slot, at=req.at)
        else:
            _detach(s, photo)
        _invalidate(s, photo)
    return {"review": _review_json(s), "photos": [_photo_json(s, p) for p in s.photos]}


@app.post("/api/sort")
def sort_bin(req: SortReq):
    """상자 안을 촬영 순서로 세운다. 정렬 근거는 _shot_order_key 참고."""
    s = get_session(req.session_id)
    bins = s.ref_bins if req.pool == "ref" else s.cur_bins
    lst = bins.get(req.slot)
    if lst is None:
        raise HTTPException(400, f"상자 '{req.slot}' 가 없습니다")
    before = list(lst)
    seen = {pid: i for i, pid in enumerate(lst)}
    lst.sort(key=lambda pid: _shot_order_key(_photo(s, pid), seen[pid]))
    known = sum(1 for pid in lst if _photo(s, pid).taken_at)
    return {"changed": lst != before, "n": len(lst), "with_time": known,
            "review": _review_json(s), "photos": [_photo_json(s, p) for p in s.photos]}


@app.post("/api/flip")
def flip_photo(req: FlipReq):
    """사진 하나를 상하반전한다 — 설정 기본값과 다르게 온 사진 대응.

    editor 값은 반전 프레임 기준이므로 함께 옮긴다(잡아 둔 구도가 가리키는
    사진 영역은 그대로다). 그 사진이 정합의 입력이었다면 결과를 버린다 —
    반전이 틀린 채로 돈 정합은 대개 실패했거나 엉뚱한 자세다.
    """
    s = get_session(req.session_id)
    photo = _photo(s, req.photo_id)
    with s.lock:
        if photo.flip != bool(req.on):
            photo.flip = bool(req.on)
            photo.editor = flip_editor_v(photo.editor)
            photo.editor0 = flip_editor_v(photo.editor0)
            _drop_eff_cache(s, photo)
            _invalidate(s, photo)
        photo.flip_user = True
    return {"photo": _photo_json(s, photo), "review": _review_json(s)}


def _drop_eff_cache(s: "Session", photo: Photo) -> None:
    """반전이 바뀐 사진의 표시용 캐시(반전본·카드 축소본)를 지운다."""
    for f in s.tmp.glob(f"eff_{photo.id}*.jpg"):
        f.unlink(missing_ok=True)
    for f in s.tmp.glob(f"card_{photo.id}_*.jpg"):
        f.unlink(missing_ok=True)


# ── 정합 기준영상 — 기준 사진을 창에 구워 낸다 ────────────────────────────────
def _contain_state(pw: int, ph: int, win: WindowCm) -> EditorState:
    """사진 **전체**가 창에 들어가는 배율 (contain). 남는 자리는 여백이 된다.

    `render_window` 는 cover(창을 덮는 배율)를 기준으로 삼으므로, 그 위에 얹을
    비율을 돌려준다. 창과 종횡비가 같으면 1.0 이라 아무것도 바뀌지 않는다.
    """
    bw, bh = cover_base_ext_cm(pw, ph, win)
    return EditorState(scale=min(win.w / bw, win.h / bh))


def _ref_bake(s: "Session", slot: str) -> np.ndarray | None:
    """슬롯의 기준 사진 대표를 **있는 그대로** 창(PPC 해상도)에 앉힌다.

    이 결과가 본편의 'PPT 복원 기준영상'과 같은 형태다: 창 좌표계, 교합면이면
    이미 반전된 그림. 정합 대상이자 겹쳐보기 이미지로 함께 쓰인다.

    **다시 자르지 않는다.** 기준 사진은 지난 차수의 완성본이고, 정합이 맞춰야
    하는 목표는 바로 그 프레임 자체다. 예전에는 프레이밍 모델을 한 번 더 걸었는데,
    그 모델은 raw 사진에서 자를 자리를 예측하도록 배운 것이라 이미 잘린 사진을
    주면 원본 경계 밖까지 잡았다 — 실측에서 교합면이 창의 7%를 검은 여백으로
    채웠고(630px 중 48행), 그만큼 기준 프레임이 저장본과 어긋나 이번 차수가 지난
    차수와 다른 구도로 저장됐다.

    **사진 전체를 넣는다**(contain). 창과 종횡비가 같은 우리 저장본은 여백 없이
    정확히 들어맞는다. 저장 비율을 바꾼 뒤라 종횡비가 다르면 짧은 쪽에 여백이
    남는데, 그편이 잘라내는 것보다 낫다: 구내 사진에서 잘려 나가는 것은 대개
    후방 치아이고, 기준이 잘리면 그에 맞춰 이번 차수 저장본까지 좁아져 기록이
    차수를 거듭할수록 줄어든다. 대응점도 줄어 정합이 약해진다.
    """
    pid = s.ref_slots.get(slot)
    if pid is None:
        with s.lock:
            s.references.pop(slot, None)
            s.ref_src.pop(slot, None)
        return None
    photo = _photo(s, pid)
    with s.lock:
        if s.ref_src.get(slot) == (pid, photo.flip) and slot in s.references:
            return s.references[slot]
    arr = _imread(photo.path)
    if arr is None:
        return None
    win = s.slot_windows[slot]
    img = Cr.render_window(arr, win, _contain_state(photo.w, photo.h, win),
                           photo.flip, PPC, PPC, Cr.hex_to_bgr(_letterbox_color()))
    with s.lock:
        s.references[slot] = img
        s.ref_src[slot] = (pid, photo.flip)
    return img


# ── 프레이밍 / 정합 ───────────────────────────────────────────────────────────
def cover_fit_editor() -> EditorState:
    return EditorState()  # scale=1, 중심, 무회전 = cover-fit


def _auto_frame(s: "Session", photo: Photo, win: WindowCm,
                fallback_badge: str | None = None, bgr=None):
    """프레이밍 모델로 초기 배치를 잡는다. 못 쓰면 cover-fit 으로 물러난다.

    프레이밍 모델은 **반전 없는 원본**으로 학습됐다. 교합면은 상하 비대칭이
    커서 뒤집어 넣으면 정확도만 조용히 떨어지므로, 추론은 항상 원본으로 하고
    결과 좌표만 반전 프레임으로 옮긴다.

    배지 규약: **모델이 있는데 예측을 기각했을 때만** '수동'을 붙인다.
    """
    label = _category_of(photo) or photo.label
    if framer is None or not framer.has(label or ""):
        photo.editor = cover_fit_editor()
        photo.framing = None
        if fallback_badge:
            photo.badge = fallback_badge
        return
    arr = bgr if bgr is not None else _imread(photo.path)
    res = framer.predict(arr, label)
    photo.framing_note = res.method
    if res.ok:
        st = framing_to_editor(res, win, photo.w, photo.h)
        photo.editor = flip_editor_v(st) if photo.flip else st
        photo.framing = "model"
        if fallback_badge:
            photo.badge = fallback_badge
    else:
        photo.editor = cover_fit_editor()
        photo.framing = "cover"
        photo.badge = fallback_badge or "manual"


def _frame_slot(s: "Session", slot: str) -> None:
    """구내 슬롯 하나의 초기 구도 — 재진이고 짝이 있으면 정합, 아니면 프레이밍."""
    pid = s.slots.get(slot)
    if pid is None:
        s.progress.pop(slot, None)
        return
    s.progress[slot] = "run"
    photo = _photo(s, pid)
    win = s.slot_windows[slot]
    ref_img = s.references.get(slot)
    t0 = time.perf_counter()

    if ref_img is None:
        # 초진이거나, 재진인데 이 카테고리엔 기준 사진이 없다(짝 없음).
        badge = "manual" if s.mode == "revisit" else None
        if s.mode == "revisit":
            _audit({"event": "register_skipped", "reason": "no_reference",
                    "folder": s.folder, "slot": slot})
        _auto_frame(s, photo, win, fallback_badge=badge)
        photo.editor0 = photo.editor   # 방금 잡은 구도가 이 자리의 initial-fit
        s.progress[slot] = "fallback" if s.mode == "revisit" else "frame"
        return

    arr = _imread(photo.path)
    # 기준영상은 반전이 이미 픽셀에 들어간 그림이다. 특징 매칭도 유사변환(det>0)도
    # 거울상은 다루지 못하므로 신규 사진을 같은 방향으로 맞춰서 넣는다. 그러면
    # 결과 변환이 곧 반전 프레임 기준이라 photo.editor 로 그대로 들어간다.
    arr_reg = cv2.flip(arr, 0) if photo.flip else arr
    try:
        pw = Reg.pseudo_frame(arr, framer, photo.label, flip_v=photo.flip)
        best, res, _ = Reg.register_best(
            arr_reg, {"기준": ref_img},
            thresholds=cfg.thresholds.registration.model_dump(), prewarp=pw)
    except Exception as e:                              # noqa: BLE001
        # 정합 오류가 검수 진입을 막으면 안 된다 — 남기고 프레이밍으로.
        _audit({"event": "register_error", "folder": s.folder, "slot": slot,
                "error": f"{type(e).__name__}: {e}"[:300]})
        _auto_frame(s, photo, win, fallback_badge="manual", bgr=arr)
        photo.editor0 = photo.editor
        s.progress[slot] = "fallback"
        return
    if res.ok:
        photo.editor = registration_to_editor(res.matrix, win, photo.w, photo.h)
        photo.badge = "ok"
        photo.framing = "registration"
        s.progress[slot] = "reg"
    else:
        _audit({"event": "register_rejected", "folder": s.folder, "slot": slot,
                "n_matches": res.n_matches, "n_inliers": res.n_inliers,
                "reproj_error_px": round(res.reproj_error_px, 2),
                "score": round(res.score, 4)})
        _auto_frame(s, photo, win, fallback_badge="manual", bgr=arr)
        s.progress[slot] = "fallback"
    photo.editor0 = photo.editor
    _audit({"event": "frame_timing", "slot": slot,
            "ms": round((time.perf_counter() - t0) * 1000)})


@app.post("/api/register/{sid}")
def register_session(sid: str, req: RegisterReq = Body(default=RegisterReq())):
    """배정이 확정된 뒤 도는 무거운 단계 — 슬롯별로 **병렬**이다.

    재진이면 먼저 기준 사진들을 창에 구워 기준영상을 만들고(그 자체도 병렬),
    이어 슬롯별 정합과 얼굴 프레이밍을 스레드 풀에 뿌린다. 슬롯끼리는 완전히
    독립이고 ONNX 세션은 동시 실행이 안전하다.
    """
    s = get_session(sid)
    force = req.force
    s.progress = {}

    # 1) 기준영상 베이크 (재진) — 짝이 있는 슬롯만
    if s.mode == "revisit":
        for slot in SLOT_NAMES:
            if s.ref_slots.get(slot) and s.slots.get(slot):
                s.progress[slot] = "refs"
        jobs = [EXEC.submit(_ref_bake, s, slot) for slot in SLOT_NAMES]
        _futures_wait(jobs, timeout=300)

    # 2) 구내 슬롯 — framed 열쇠가 그대로면 건너뛴다
    def _key_of(slot):
        pid = s.slots.get(slot)
        if pid is None:
            return None
        p = _photo(s, pid)
        return (pid, p.flip, s.ref_src.get(slot))

    want = req.slots if req.slots is not None else SLOT_NAMES
    done, jobs = [], []

    def _face_job(photo):
        key = f"FACE:{photo.id}"
        s.progress[key] = "run"
        _auto_frame(s, photo, s.face_window)
        photo.editor0 = photo.editor
        s.progress[key] = "frame"

    with s.lock:
        for slot in want:
            key = _key_of(slot)
            if key is None:
                s.framed.pop(slot, None)      # 비워진 자리 — 기록도 지운다
                s.progress.pop(slot, None)
                continue
            if not force and s.framed.get(slot) == key:
                continue
            s.framed[slot] = key
            s.progress.setdefault(slot, "wait")
            done.append(slot)
            jobs.append(EXEC.submit(_frame_slot, s, slot))

        # 3) 얼굴 — 정합 없이 프레이밍만, 사진마다 한 장짜리 검수 창
        for pid in s.face:
            photo = _photo(s, pid)
            if not force and s.face_framed.get(pid) == photo.flip:
                continue
            s.face_framed[pid] = photo.flip
            s.progress[f"FACE:{pid}"] = "wait"
            done.append(f"FACE:{pid}")
            jobs.append(EXEC.submit(_face_job, photo))

    if jobs:
        _futures_wait(jobs, timeout=600)
        for j in jobs:
            exc = j.exception()
            if exc is not None:
                _audit({"event": "frame_error",
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"done": done, "photos": [_photo_json(s, p) for p in s.photos],
            "review": _review_json(s)}


@app.get("/api/register/{sid}/status")
def register_status(sid: str):
    """정합 진행 상태 — 병렬로 도는 동안 화면이 라벨별 진행을 폴링한다."""
    s = get_session(sid)
    return {"progress": dict(s.progress), "mode": s.mode}


# 투입·분류·정합을 한 번에 하는 엔드포인트 — API 테스트와 외부 자동화용.
@app.post("/api/upload/{sid}")
async def upload(sid: str, pool: str = "cur", files: list[UploadFile] = File(...)):
    if pool not in ("ref", "cur"):
        raise HTTPException(400, "pool 은 ref 또는 cur 이어야 합니다")
    s = get_session(sid)
    staged = await _stage_photos(s, files, pool)
    _classify(s, staged)
    register_session(s.id, RegisterReq())
    return {"photos": [_photo_json(s, p) for p in s.photos], "review": _review_json(s)}


# ── 검수 조정 ─────────────────────────────────────────────────────────────────
def _adjust_target(s: "Session", slot: str) -> tuple[Photo, WindowCm]:
    if slot.startswith("FACE:"):
        photo = _photo(s, slot.split(":", 1)[1])
        if photo.slot != "FACE":
            raise HTTPException(400, "얼굴 상자에 없는 사진입니다")
        return photo, s.face_window
    pid = s.slots.get(slot)
    if not pid:
        raise HTTPException(400, "슬롯이 비어있음")
    return _photo(s, pid), s.slot_windows[slot]


@app.post("/api/adjust")
def adjust(req: AdjustReq):
    s = get_session(req.session_id)
    photo, win = _adjust_target(s, req.slot)
    bw, bh = cover_base_ext_cm(photo.w, photo.h, win)
    st = _clamp(EditorState(req.dx, req.dy, req.scale, req.angle), win, bw, bh)
    photo.editor = st
    return {"clamped_scale": st.scale}


# ── 이미지 서빙 ───────────────────────────────────────────────────────────────
def _eff_path(s: "Session", p: Photo) -> Path:
    """표시용 파일 — 반전이 켜진 사진은 뒤집은 사본을 캐시해서 준다.

    화면(캔버스·카드)은 언제나 이 파일을 그리므로 flip 계산이 프론트에 없다.
    저장 베이크는 원본 + editor + flip 으로 따로 돈다(crop.render_window).
    """
    if not p.flip:
        return p.path
    dst = s.tmp / f"eff_{p.id}.jpg"
    if not dst.exists():
        arr = _imread(p.path)
        if arr is None:
            return p.path
        ok, buf = cv2.imencode(".jpg", cv2.flip(arr, 0),
                               [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            return p.path
        dst.write_bytes(buf.tobytes())
    return dst


@app.get("/api/thumb/{sid}/{pid}")
def thumb(sid: str, pid: str, w: int = 0, v: int = 0):
    """사진 한 장(반전 반영). `w` 를 주면 그 폭으로 줄여서 준다.

    `v` 는 캐시 무효화용 반전 토큰 — 값 자체는 쓰지 않는다(반전은 서버 상태가
    진실이다). URL 을 가르는 것이 목적의 전부다.
    """
    s = get_session(sid)
    p = _photo(s, pid)
    src = _eff_path(s, p)
    if w <= 0:
        return FileResponse(src)
    w = max(64, min(int(w), 1024))
    dst = s.tmp / f"card_{p.id}_{w}.jpg"
    if not dst.exists():
        from PIL import Image as _Im, ImageOps as _Ops      # noqa: PLC0415
        try:
            with _Im.open(src) as im:
                im.draft("RGB", (w, w))     # 1/2·1/4·1/8 로 바로 디코드
                im = _Ops.exif_transpose(im).convert("RGB")
                im.thumbnail((w, w))
                im.save(dst, "JPEG", quality=80)
        except Exception:                                   # noqa: BLE001
            return FileResponse(src)
    return FileResponse(dst)


@app.get("/api/reference/{sid}/{slot}")
def reference(sid: str, slot: str):
    """기준영상 한 장 — 기준 사진을 창에 구워 낸 그림 그대로.

    창 기준으로 이미 맞춰져 있고 교합면이면 이미 뒤집혀 있다 — 그래서 화면에서
    창에 그대로 깔면 지금 편집 중인 구도와 바로 겹쳐 볼 수 있다(겹쳐보기).
    """
    s = get_session(sid)
    img = s.references.get(slot)
    if img is None:
        img = _ref_bake(s, slot)
    if img is None:
        raise HTTPException(404, "기준영상 없음")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise HTTPException(500, "기준영상을 만들지 못했습니다")
    return StreamingResponse(io.BytesIO(buf.tobytes()), media_type="image/png")


@app.get("/api/references/{sid}")
def reference_list(sid: str):
    """슬롯마다 겹쳐볼 기준이 있는가 — 화면의 겹쳐보기 토글이 이걸 쓴다."""
    s = get_session(sid)
    return {slot: True for slot in SLOT_NAMES
            if slot in s.references or s.ref_slots.get(slot)}


# ── 확정 (원자적 저장) ────────────────────────────────────────────────────────
def _plan_entries(s: "Session") -> list[dict]:
    """저장될 사진들 — 순서대로 (categoy, pid, kind). 대표 먼저, 여분은 설정."""
    save_extras = _output_prefs()["save_extras"]
    out: list[dict] = []
    # 구내: 파일명 순번(1~5) 순서 = FRONT, RIGHT, LEFT, UPPER, LOWER
    by_index = sorted(cfg.intraoral_slots.items(), key=lambda kv: kv[1].index)
    for cls, si in by_index:
        members = s.cur_bins.get(si.slot) or []
        for i, pid in enumerate(members):
            if i > 0 and not save_extras:
                break
            out.append({"category": cls, "pid": pid, "slot": si.slot,
                        "extra": i > 0})
    for i, pid in enumerate(s.face):
        out.append({"category": "FACE", "pid": pid, "slot": "FACE",
                    "extra": False})
    return out


def _build_plan(s: "Session", overwrite: set[str] | None = None) -> dict:
    """확정하면 **무엇이 어떤 이름으로 어디에 생기는지** 계산한다. 부수효과 없음.

    commit()이 이 결과를 그대로 쓴다. 미리보기와 실제 저장이 각자 이름을
    계산하면 언젠가 반드시 갈라지고, 그건 기록물에서 나면 안 되는 버그다.
    """
    if not s.folder:
        raise HTTPException(400, "저장될 폴더 이름을 먼저 입력해 주세요")
    overwrite = overwrite or set()
    naming = _naming_prefs()
    outp = _output_prefs()
    ext = "." + outp["format"]
    prefix = s.prefix or s.folder
    dest = ROOT / s.folder

    entries = _plan_entries(s)
    stems = FN.plan_stems(prefix, [e["category"] for e in entries],
                          aliases=naming["aliases"],
                          number_mode=naming["number_mode"],
                          start=naming["start"], separator=naming["separator"])
    # 충돌 판정: 폴더에 이미 있는 파일 + 이 계획 안에서 이미 배정한 이름
    taken = {p.name.lower() for p in dest.iterdir() if p.is_file()} if dest.is_dir() else set()
    raw = _save_raw()
    files = []
    for e, stem in zip(entries, stems):
        want = stem + ext
        exists = want.lower() in taken
        if exists and want not in overwrite:
            # 덮어쓰지 않는다 — 다음 빈 번호를 붙인다
            stem_taken = {t[:-len(ext)] if t.endswith(ext) else t for t in taken}
            final_stem = FN.bump(stem.lower(), stem_taken, naming["separator"])
            # bump 는 소문자 비교용 — 원 표기를 살려 재조립
            final = (stem + final_stem[len(stem.lower()):]) + ext
            action = "number"
        else:
            final = want
            action = "overwrite" if exists else "new"
        taken.add(final.lower())
        photo = _photo(s, e["pid"])
        files.append({
            "pid": e["pid"], "category": e["category"], "slot": e["slot"],
            "extra": e["extra"], "label": photo.label,
            # base = 충돌 전 원래 이름. 화면의 [자동 번호|덮어쓰기] 선택과
            # commit 의 overwrite 목록이 이 이름을 쓴다.
            "base": want,
            "file": final, "exists": exists, "action": action,
            "raw": (f"raw/{FN.raw_name(Path(final).stem, photo.orig_name)}"
                    if raw else None),
        })
    missing = [cls for cls, si in cfg.intraoral_slots.items()
               if not s.cur_bins.get(si.slot)]
    return {"dir": str(dest), "folder": s.folder, "prefix": prefix,
            "mode": s.mode, "format": outp["format"],
            "px_per_cm": outp["px_per_cm"],
            "files": files, "missing": missing,
            "save_raw": raw, "save_extras": outp["save_extras"]}


@app.get("/api/plan/{sid}")
def plan(sid: str):
    """저장 직전 검토용 드라이런. 아무것도 쓰지 않는다."""
    return _build_plan(get_session(sid))


def _bake_out(photo: Photo, win: WindowCm, dst: Path, outp: dict) -> Path | None:
    """창에 보이는 그림만 잘라 dst 에 굽는다. 실패하면 None (원본 복사 폴백)."""
    arr = _imread(photo.path)
    if arr is None:
        return None
    if outp["flip_save"] or not photo.flip:
        st, fl = photo.editor, photo.flip
    else:
        # '반전 적용해 저장' 이 꺼져 있다 — 같은 크롭 영역을 반전 없이 굽는다.
        # editor 값은 반전 프레임 기준이라 원본 프레임으로 옮겨야 영역이 같다.
        st, fl = flip_editor_v(photo.editor), False
    out = Cr.render_window(arr, win, st, fl,
                           outp["px_per_cm"], PPC,
                           Cr.hex_to_bgr(_letterbox_color()))
    try:
        from PIL import Image as _Im                              # noqa: PLC0415
        im = _Im.fromarray(out[:, :, ::-1])
        if outp["format"] == "png":
            im.save(dst, "PNG")
        else:
            # EXIF 를 원본에서 옮겨 심는다 — cv2.imwrite 는 EXIF 를 쓸 줄 모른다.
            with _Im.open(photo.path) as _src:
                exif = _src.info.get("exif")
            im.save(dst, "JPEG", quality=outp["jpeg_quality"], subsampling=0,
                    **({"exif": exif} if exif else {}))
    except Exception:                                             # noqa: BLE001
        return None
    # 촬영시각을 옮겨 심는다 — mtime 이 '구운 시각'이면 찍은 날이 사라진다.
    try:
        st = photo.path.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return dst


def _open_folder(path: Path) -> None:
    """저장이 끝난 폴더를 탐색기로 연다 — 실패해도 조용히 지나간다."""
    try:
        if os.name == "nt":
            os.startfile(str(path))                    # noqa: S606
            return
        exe = shutil.which("explorer.exe")             # WSL → Windows 탐색기
        if exe:
            w = subprocess.run(["wslpath", "-w", str(path)], capture_output=True,
                               encoding="utf-8", timeout=10)
            target = w.stdout.strip() if w.returncode == 0 else str(path)
            subprocess.Popen([exe, target])
            return
        opener = shutil.which("open") or shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, str(path)])
    except Exception:                                             # noqa: BLE001
        pass


class OpenFolderReq(BaseModel):
    path: str


@app.post("/api/open")
def open_folder_api(req: OpenFolderReq):
    """저장이 끝난 폴더를 탐색기로 연다 — 저장 루트 안만 허용한다."""
    p = Path(req.path).resolve()
    if not p.is_dir() or not str(p).startswith(str(ROOT.resolve())):
        raise HTTPException(400, "저장 루트 안의 폴더만 열 수 있습니다")
    _open_folder(p)
    return {"ok": True}


@app.post("/api/commit/{sid}")
def commit(sid: str, req: CommitReq = Body(default=CommitReq()),
           allow_missing: bool = False):
    s = get_session(sid)
    pl = _build_plan(s, overwrite=set(req.overwrite))
    if pl["missing"] and not allow_missing:
        return JSONResponse(status_code=409, content={
            "error": "missing_slots", "missing": pl["missing"]})
    if not pl["files"]:
        raise HTTPException(400, "저장할 사진이 없습니다")

    outp = _output_prefs()
    dest = ROOT / s.folder
    try:
        with S.Transaction(dest) as tx:
            for i, fe in enumerate(pl["files"]):
                photo = _photo(s, fe["pid"])
                win = s.face_window if fe["slot"] == "FACE" else s.slot_windows[fe["slot"]]
                baked = _bake_out(photo, win, s.tmp / f"bake_{i}{Path(fe['file']).suffix}",
                                  outp)
                # 굽지 못하면(깨진 파일 등) 원본이라도 남긴다 — 조용한 유실 방지
                tx.stage_file(baked or photo.path, fe["file"])
                if fe.get("raw"):
                    tx.stage_file(photo.path, fe["raw"])
            moved = tx.commit()
        _audit({"event": "commit", "mode": s.mode, "folder": s.folder,
                "files": [p.relative_to(dest).as_posix() for p in moved]})
    except Exception as e:                                        # noqa: BLE001
        _audit({"event": "commit_failed", "folder": s.folder, "error": str(e)})
        raise HTTPException(500, f"확정 실패(롤백됨): {e}")

    after = _after_save()
    if after["open_folder"]:
        _open_folder(dest)
    result = {"ok": True, "dir": str(dest), "folder": s.folder,
              "files": [p.relative_to(dest).as_posix() for p in moved],
              "after": after}
    discard_session(s)   # 업로드 원본은 저장 폴더로 복사됐다 — 임시본을 남기지 않는다
    return result


# ── JSON 직렬화 ───────────────────────────────────────────────────────────────
def _photo_json(s, p: Photo):
    return {"id": p.id, "pool": p.pool, "label": p.label,
            "confidence": round(p.confidence, 3),
            "slot": p.slot, "badge": p.badge,
            "framing": p.framing, "framing_note": p.framing_note,
            "flip": p.flip,
            "taken_at": p.taken_at.isoformat(sep=" ", timespec="milliseconds") if p.taken_at else None,
            # v = 반전 상태 토큰. 반전을 켜고 끌 때 URL 이 달라져야 브라우저와
            # 화면 캐시가 옛 방향의 그림을 재사용하지 않는다 — 즉시 뒤집혀 보인다.
            "thumb": f"/api/thumb/{s.id}/{p.id}?v={int(p.flip)}",
            "card": f"/api/thumb/{s.id}/{p.id}?w=320&v={int(p.flip)}",
            "editor": {"dx": round(p.editor.dx_px, 2), "dy": round(p.editor.dy_px, 2),
                       "scale": round(p.editor.scale, 4), "angle": round(p.editor.angle_deg, 3)},
            # 'initial-fit 으로 초기화' 가 돌아갈 자리
            "editor0": {"dx": round(p.editor0.dx_px, 2), "dy": round(p.editor0.dy_px, 2),
                        "scale": round(p.editor0.scale, 4),
                        "angle": round(p.editor0.angle_deg, 3)}}


def _review_json(s):
    slots = {}
    for slot in SLOT_NAMES:
        pid = s.slots.get(slot)
        slots[slot] = _photo_json(s, _photo(s, pid)) if pid else None
    face = [_photo_json(s, _photo(s, pid)) for pid in s.face]
    keys = list(SLOT_NAMES) + ["FACE"]
    bins = {k: [_photo_json(s, _photo(s, pid)) for pid in s.cur_bins.get(k, [])]
            for k in keys}
    ref_bins = {k: [_photo_json(s, _photo(s, pid)) for pid in s.ref_bins.get(k, [])]
                for k in keys}
    # 짝맞춤 상태: 카테고리(슬롯)별로 기준·현재가 다 있는가
    pairs = {k: {"ref": bool(s.ref_bins.get(k)), "cur": bool(s.cur_bins.get(k)),
                 # FACE 는 정합 대상이 아니다 — 화면이 '정합 제외'로 표시한다
                 "registrable": k != "FACE" and bool(s.ref_bins.get(k))
                                and bool(s.cur_bins.get(k))}
             for k in keys}
    return {"mode": s.mode, "folder": s.folder, "prefix": s.prefix or s.folder,
            "slots": slots, "face": face, "bins": bins, "ref_bins": ref_bins,
            "pairs": pairs,
            "missing": [sl for sl in SLOT_NAMES if sl not in s.slots]}


# ── 업데이트 · 유지관리 ──────────────────────────────────────────────────────
def _busy() -> bool:
    """확정하지 않은 작업이 있나. 있으면 재시작이 그 작업을 날린다."""
    return any(getattr(s, "photos", None) for s in SESSIONS.values())


def _safe_check() -> Up.UpdateStatus:
    """확인이 어떻게 터지든 **500 을 내지 않는다.** 실패는 사유를 달고 화면까지."""
    try:
        return Up.check(busy=_busy())
    except Exception as e:                                        # noqa: BLE001
        st = Up.UpdateStatus()
        st.reason = f"확인 중 오류: {type(e).__name__}: {e}"[:300]
        return st


@app.get("/api/update/check")
def update_check():
    """새 버전이 있나. **네트워크를 쓴다** — 화면은 배너로만 쓰고 기다리게 하지 말 것."""
    return _safe_check().to_json()


class UpdateApplyReq(BaseModel):
    force: bool = False      # 직접 수정한 파일을 백업하고 강제 진행


@app.post("/api/update/apply")
def update_apply(req: UpdateApplyReq = Body(default=UpdateApplyReq())):
    st = _safe_check()
    if not st.has_update:
        return {"ok": False, "detail": st.reason or "이미 최신입니다"}
    if st.blocked and not (req.force and "직접 수정" in st.blocked):
        return {"ok": False, "detail": st.blocked}
    try:
        return Up.apply_update(force=req.force)
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/update/rollback")
def update_rollback():
    if _busy():
        return {"ok": False, "detail": "확정하지 않은 작업이 있습니다"}
    try:
        return Up.rollback()
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/update/restart")
def update_restart():
    """재시작 종료코드로 죽는다. `run.bat`/`run.command` 의 루프가 다시 띄운다."""
    if _busy():
        return {"ok": False, "detail": "확정하지 않은 작업이 있습니다"}
    threading.Timer(0.5, Up.restart_now).start()      # 응답을 먼저 보내고 죽는다
    return {"ok": True}


# 바탕화면 바로가기가 가리킬 아이콘. **이름에 번호가 붙어 있는 것이 핵심**이다 —
# 윈도우는 바로가기 아이콘을 (경로, 인덱스)로 캐시하므로 경로가 달라져야 갱신된다.
SHORTCUT_ICON = "crocs-2.ico"


def _win_repo_path() -> tuple[str, str] | None:
    """(powershell 실행 파일, 윈도우식 프로그램 경로). 윈도우가 아니면 None."""
    exe = _powershell()
    if exe is None:
        return None
    repo = str(PROGRAM_DIR)
    if os.name != "nt":
        try:
            r = subprocess.run(["wslpath", "-w", repo], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=10)
            if r.returncode != 0 or not r.stdout.strip():
                return None
            repo = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return exe, repo


def _repair_shortcut_icon() -> None:
    """아이콘 파일 이름이 바뀌었으면 바탕화면 바로가기의 **아이콘 경로만** 고친다."""
    try:
        if _setting("shortcut_icon") == SHORTCUT_ICON:
            return
        got = _win_repo_path()
        if got is None:                        # 윈도우가 아니다 — 할 일이 없다
            _save_setting("shortcut_icon", SHORTCUT_ICON)
            return
        exe, repo = got
        icon = f"{repo}\\assets\\{SHORTCUT_ICON},0"
        ps = (
            "$sh=New-Object -ComObject WScript.Shell;"
            "$p=@();"
            "try{$d=$sh.SpecialFolders.Item('Desktop'); if($d){$p+=(Join-Path $d 'CRoCs Fastest Lap.lnk')}}catch{};"
            "$p+=(Join-Path $env:USERPROFILE 'Desktop\\CRoCs Fastest Lap.lnk');"
            "$p+=(Join-Path $env:USERPROFILE 'OneDrive\\Desktop\\CRoCs Fastest Lap.lnk');"
            "$n=0;"
            "foreach($f in ($p | Select-Object -Unique)){"
            "  if(Test-Path $f){"
            "    $s=$sh.CreateShortcut($f);"
            f"    if($s.TargetPath -like '{repo}\\*'){{"
            f"      if($s.IconLocation -ne '{icon}'){{ $s.IconLocation='{icon}'; $s.Save(); }}"
            "      $n++;"
            "    }"
            "  }"
            "};"
            "Write-Output $n"
        )
        r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", ps],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=30)
        fixed = (r.stdout or "").strip().splitlines()
        print(f"[바로가기] 아이콘 경로 갱신 — {fixed[-1] if fixed else '?'}개")
        _save_setting("shortcut_icon", SHORTCUT_ICON)
    except Exception as e:                                        # noqa: BLE001
        print(f"[바로가기] 아이콘 갱신 건너뜀: {type(e).__name__}: {e}")


@app.post("/api/shortcut")
def make_shortcut():
    """바탕화면에 바로가기 — 설치 이후에도 설정에서 만들 수 있게. Windows 전용."""
    got = _win_repo_path()
    if got is None:
        return {"ok": False, "detail": "Windows에서만 만들 수 있습니다"}
    exe, repo = got
    ps = (
        "$sh=New-Object -ComObject WScript.Shell;"
        "$d=$sh.SpecialFolders.Item('Desktop');"
        "if(-not $d){$d=Join-Path $env:USERPROFILE 'Desktop'};"
        "$s=$sh.CreateShortcut((Join-Path $d 'CRoCs Fastest Lap.lnk'));"
        f"$s.TargetPath='{repo}\\run.bat';"
        f"$s.WorkingDirectory='{repo}';"
        f"$s.IconLocation='{repo}\\assets\\{SHORTCUT_ICON},0';"
        "$s.Save(); Write-Output $d"
    )
    try:
        r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", ps],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:200]}
    if r.returncode != 0:
        return {"ok": False,
                "detail": ((r.stderr or r.stdout or "").strip()[:300]
                           or "만들지 못했습니다")}
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    return {"ok": True, "desktop": lines[-1] if lines else ""}


# ── 개인화 설정 ───────────────────────────────────────────────────────────────
class PrefsReq(BaseModel):
    save_raw: bool | None = None
    letterbox_color: str | None = None           # 회전·축소로 드러나는 빈 자리 색
    flip_defaults: dict | None = None            # {"ref": {...}, "cur": {...}}
    naming: dict | None = None                   # number_mode·start·separator·aliases
    output: dict | None = None                   # px_per_cm·format·jpeg_quality·save_extras
    after_save: dict | None = None               # open_folder·auto_next


def _prefs_json() -> dict:
    return {"save_raw": _save_raw(),
            "letterbox_color": _letterbox_color(),
            "flip_defaults": _flip_defaults(),
            "flip_defaults_default": FLIP_DEFAULTS,
            "naming": _naming_prefs(),
            "aliases_default": dict(FN.DEFAULT_ALIASES),
            "output": _output_prefs(),
            "after_save": _after_save(),
            "classes": FLIP_CLASSES}


@app.get("/api/prefs")
def prefs_get():
    return _prefs_json()


@app.post("/api/prefs")
def prefs_set(req: PrefsReq):
    """개인화 설정. **`settings.json`(설치본 공용)** 에 둔다. 보낸 항목만 바꾼다."""
    path = SETTINGS_FILE                       # 읽은 그 파일에만 쓴다
    d = _settings(path)
    if req.save_raw is not None:
        d["save_raw"] = bool(req.save_raw)
    if req.letterbox_color is not None:
        v = req.letterbox_color.strip().lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", v):
            raise HTTPException(400, "색은 RGB 6자리(예: 000000)여야 합니다")
        d["letterbox_color"] = v.upper()
    if req.flip_defaults is not None:
        clean = {}
        for pool in ("ref", "cur"):
            got = req.flip_defaults.get(pool)
            if not isinstance(got, dict):
                raise HTTPException(400, "flip_defaults 는 ref/cur 두 그리드여야 합니다")
            bad = set(got) - set(FLIP_CLASSES)
            if bad:
                raise HTTPException(400, f"모르는 카테고리: {', '.join(sorted(bad))}")
            clean[pool] = {k: bool(v) for k, v in got.items()}
        d["flip_defaults"] = clean
    if req.naming is not None:
        cur = d.get("naming") or {}
        if "number_mode" in req.naming:
            if req.naming["number_mode"] not in ("multi_only", "always"):
                raise HTTPException(400, "number_mode 는 multi_only 또는 always")
            cur["number_mode"] = req.naming["number_mode"]
        if "start" in req.naming:
            if req.naming["start"] not in (0, 1):
                raise HTTPException(400, "시작 번호는 0 또는 1")
            cur["start"] = int(req.naming["start"])
        if "separator" in req.naming:
            sep = str(req.naming["separator"])
            if not (0 < len(sep) <= 3) or (set(sep) & set('\\/:*?"<>|')):
                raise HTTPException(400, "구분자는 1~3글자, 금지 문자 불가")
            cur["separator"] = sep
        if "aliases" in req.naming:
            got = req.naming["aliases"]
            if not isinstance(got, dict):
                raise HTTPException(400, "aliases 는 카테고리→이름 표여야 합니다")
            bad = set(got) - set(FN.DEFAULT_ALIASES)
            if bad:
                raise HTTPException(400, f"모르는 카테고리: {', '.join(sorted(bad))}")
            clean = {}
            for k, v in got.items():
                sv = FN.sanitize(str(v))
                if not sv:
                    raise HTTPException(400, f"'{v}' 는 파일명으로 쓸 수 없습니다")
                clean[k] = sv
            cur["aliases"] = {**(cur.get("aliases") or {}), **clean}
        d["naming"] = cur
    if req.output is not None:
        cur = d.get("output") or {}
        if "px_per_cm" in req.output:
            try:
                v = float(req.output["px_per_cm"])
            except (TypeError, ValueError):
                raise HTTPException(400, "해상도는 숫자여야 합니다")
            if not 50 <= v <= 400:
                raise HTTPException(400, "해상도는 50~400 px/cm 사이여야 합니다")
            cur["px_per_cm"] = v
        if "format" in req.output:
            if req.output["format"] not in ("jpg", "png"):
                raise HTTPException(400, "형식은 jpg 또는 png")
            cur["format"] = req.output["format"]
        if "jpeg_quality" in req.output:
            try:
                q = int(req.output["jpeg_quality"])
            except (TypeError, ValueError):
                raise HTTPException(400, "품질은 숫자여야 합니다")
            if not 60 <= q <= 100:
                raise HTTPException(400, "품질은 60~100 사이여야 합니다")
            cur["jpeg_quality"] = q
        if "save_extras" in req.output:
            cur["save_extras"] = bool(req.output["save_extras"])
        if "flip_save" in req.output:
            cur["flip_save"] = bool(req.output["flip_save"])
        if "io_ratio" in req.output:
            if req.output["io_ratio"] not in IO_RATIOS:
                raise HTTPException(400, f"구내 비율은 {' · '.join(IO_RATIOS)} 중 하나")
            cur["io_ratio"] = req.output["io_ratio"]
        if "face_ratio" in req.output:
            if req.output["face_ratio"] not in FACE_RATIOS:
                raise HTTPException(400, f"얼굴 비율은 {' · '.join(FACE_RATIOS)} 중 하나")
            cur["face_ratio"] = req.output["face_ratio"]
        d["output"] = cur
    if req.after_save is not None:
        cur = d.get("after_save") or {}
        for k in ("open_folder", "auto_next"):
            if k in req.after_save:
                cur[k] = bool(req.after_save[k])
        d["after_save"] = cur
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return _prefs_json()


@app.get("/api/uninstall/inventory")
def uninstall_inventory():
    """지워질 것과 남을 것. **사진 자료는 기본으로 남는다.**"""
    return Un.inventory(BACKEND_DIR.parents[1], ROOT).to_json()


@app.post("/api/uninstall/prepare")
def uninstall_prepare(body: dict = Body(default={})):
    """삭제 스크립트를 만들고 앱을 끝낸다."""
    if _busy():
        return {"ok": False, "detail": "확정하지 않은 작업이 있습니다"}
    # 사진 자료는 받지 않는다 — body 에 drop_data 가 와도 무시한다.
    tools = [str(t) for t in (body.get("drop_tools") or [])]
    r = Un.prepare(BACKEND_DIR.parents[1], ROOT, drop_tools=tools)
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return r


@app.get("/api/weights")
def weights_status():
    """가중치 준비 상태. 없으면 화면이 무엇을 어디서 받는지 안내한다."""
    try:
        import sys as _sys                                          # noqa: PLC0415
        _sys.path.insert(0, str(BACKEND_DIR.parents[1]))
        import weightstore                                          # noqa: PLC0415
        st = weightstore.scan(verify=False)
        return {"ready": st.ready,
                "items": [{"key": i.key, "state": i.state, "file": i.file,
                           "detail": i.detail, "url": i.drive_url} for i in st.items],
                "strays": [{"name": p.name, "why": w} for p, w in st.strays],
                "drop_dir": str(weightstore.DROP_DIR)}
    except Exception as e:                                          # noqa: BLE001
        return {"ready": False, "items": [], "strays": [],
                "error": f"{type(e).__name__}: {e}"}


app.mount("/static", _NoCacheStatic(directory=str(FRONTEND_DIR)), name="static")
# 로고·아이콘·안내 그림. 화면 파일과 같은 캐시 규칙(no-cache)을 쓴다.
app.mount("/assets", _NoCacheStatic(directory=str(PROGRAM_DIR / "assets")), name="assets")


def _port_free(port: int) -> bool:
    """그 포트에 바인딩할 수 있나 — 서버가 실제로 뜰 수 있는지와 같은 조건."""
    import socket                                                   # noqa: PLC0415
    with socket.socket() as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _ours_at(port: int) -> bool:
    """그 포트에 떠 있는 것이 **살아 있는 이 앱**인가."""
    import urllib.request                                           # noqa: PLC0415
    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=4.0) as r:
                return bool(json.loads(r.read()).get("program_dir"))
        except Exception:                                           # noqa: BLE001
            if attempt == 0:
                time.sleep(0.6)
    return False


# 실행 중인 서버가 자기 pid 와 포트를 적어 두는 자리. 새로 뜨는 쪽이 "포트를 쥔
# 것이 우리 것인가" 를 확인하는 데 쓴다 — 이게 없으면 남의 프로그램을 죽일 수 있다.
LOCK_FILE = PROGRAM_DIR / ".server.fastest.json"


def _write_lock(port: int) -> None:
    try:
        LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "port": port}),
                             encoding="utf-8")
    except OSError:
        pass


def _kill_stale(port: int) -> bool:
    """응답하지 않으면서 포트만 쥐고 있는 **우리 프로세스**를 정리한다."""
    import signal                                                   # noqa: PLC0415
    try:
        d = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:                                               # noqa: BLE001
        return False
    pid = d.get("pid")
    if not isinstance(pid, int) or d.get("port") != port or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False                    # 이미 없는 pid — 포트는 남이 쥐고 있다
    for _ in range(20):                 # 최대 2초 기다린다
        time.sleep(0.1)
        if _port_free(port):
            return True
    try:                                # 안 죽으면 강제로
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass
    for _ in range(20):
        time.sleep(0.1)
        if _port_free(port):
            return True
    return False


def run():
    import threading
    import webbrowser
    import uvicorn
    # 본편 CRoCs(8000)와 같은 PC 에서 공존해야 하므로 기본 포트를 8001 로 둔다.
    port = int(os.environ.get("PORT", "8001"))
    if not _port_free(port):
        if _ours_at(port):
            print(f"[알림] 이미 실행 중입니다 (포트 {port}). 브라우저만 엽니다.")
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        if _kill_stale(port):
            print(f"[알림] 응답하지 않는 이전 실행을 정리하고 시작합니다 (포트 {port}).")
        else:
            print(f"[오류] 포트 {port} 을 다른 프로그램이 쓰고 있습니다.")
            print("       그 프로그램을 닫고 다시 실행해 주세요.")
            print(f"       확인:  netstat -ano | findstr :{port}")
            print(f"       이번만 다른 포트로 띄우려면:  set PORT={port + 1} && run.bat")
            try:
                input("       Enter 를 누르면 닫습니다... ")
            except EOFError:
                pass
            return
    _write_lock(port)
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    run()
