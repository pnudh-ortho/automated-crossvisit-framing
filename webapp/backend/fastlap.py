"""
Fastest Lap — 환자 폴더에 PPT 없이 사진만 남기는 진행 방식.

### 본편과 무엇이 다른가

  · 정합 기준영상이 **사람이 떨어뜨린 사진**이다 (본편은 이전 차수 슬라이드 복원)
  · 슬롯별 정합이 **병렬**이고, 기준 사진은 들어오는 즉시 데워 둔다
  · 확정하면 덱을 조립하지 않고 사진만 쓴다 — 차수는 사람이 PowerPoint 에서 넣는다

### 무엇이 같은가

나머지 전부다: 업로드·EXIF 촬영순·분류·프레이밍 모델·검수 편집기·창(레이아웃)·
저장 루트·설정·차수 글자. `main` 의 것을 그대로 부른다. **차수의 진실은 여전히
PPT 하나**이고 이 파일은 그것을 읽기만 한다.

### 왜 별도 파일인가

`fastest_lap` 브랜치(환자 관리가 아예 없는 개원의용 판본)가 계속 유지되고,
코어 수정은 cherry-pick 으로 그쪽에 흘러간다. `main.py` 를 크게 흔들면 그
흐름이 끊긴다 — 그래서 새 로직은 **본편에 없는 새 파일**로 들어온다.

`import main as M` 로 모듈째 참조하는 것도 일부러다. from-import 로 이름을
끌어오면 이 파일이 main 의 정의 순서에 묶이는데, main 은 제 맨 끝에서 이
모듈을 부른다. 속성으로 접근하면 그 순서를 신경 쓸 일이 없다.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait as _wait
from pathlib import Path

import cv2
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from pydantic import BaseModel

import fl_names as FN
import main as M
from coords import EditorState, WindowCm

router = APIRouter(prefix="/api/fl", tags=["fastest-lap"])

# 기준영상 열쇠. 본편은 차수 글자('A','B'…)를 열쇠로 쓰지만 여기 기준은 사람이
# 떨어뜨린 사진 한 장뿐이라 셀 차수가 없다. `main._ref_order` 가 글자가 아닌
# 이 열쇠를 받아 주므로 겹쳐보기 엔드포인트는 두 모드에서 그대로 돈다.
REF_KEY = "기준"

POOLS = ("ref", "cur")

# 얼굴 검수 창. 케이스 덱의 얼굴 자리(CASE_ANCHORS)는 덱을 만들 때만 쓰는
# 것이라 여기서는 못 쓴다. 프레이밍 FACE 모델의 crop 비율과 같은 3:4 세로를
# 슬롯 폭에 맞춰 세운다 — 폭이 슬롯과 같으면 저장 해상도 계산이 한 규칙으로 선다.
_SLOT_W = next(iter(M.SLOT_WINDOWS.values())).w
FACE_WINDOW = WindowCm(x=0.0, y=0.0, w=_SLOT_W, h=round(_SLOT_W / 0.75, 3))


def _pair_workers() -> int:
    """슬롯별 정합을 몇 개까지 동시에 돌릴까. 0 = 자동.

    ONNX 세션 자체가 코어를 여럿 쓰므로 너무 키우면 서로 밟는다.
    """
    n = getattr(M.cfg, "perf", None)
    n = getattr(n, "pair_workers", 0) if n is not None else 0
    if n and n > 0:
        return min(int(n), 8)
    return max(1, min(3, (os.cpu_count() or 2) // 2))


EXEC = ThreadPoolExecutor(max_workers=_pair_workers(), thread_name_prefix="flap")


def _audit(record: dict) -> None:
    M.S.append_audit(M.LOG_FILE, record)


def _fast(sid: str):
    """Fastest Lap 세션을 집는다. 본편 세션이면 거절한다 — 저장 경로가 다르다."""
    s = M.get_session(sid)
    if not s.fast:
        raise HTTPException(400, "Fastest Lap 세션이 아닙니다")
    return s


# ── 두 풀의 상자 ──────────────────────────────────────────────────────────────
# 'cur'(오늘 사진)은 본편의 `s.bins` 를 **그대로** 쓴다. 검수·저장·JSON 이 전부
# 그 자리를 보고 있으므로, 오늘 사진이 거기 있으면 그 코드가 한 줄도 안 바뀐다.
# 새로 생기는 것은 기준 풀(`s.ref_bins`) 하나뿐이다.
def _bins_of(s, pool: str) -> dict:
    return s.ref_bins if pool == "ref" else s.bins


# 상자는 **여러 스레드가 함께 만진다.** 기준 사진의 prewarm 이 백그라운드에서
# 분류·배정을 하는 동안 사람이 `자동 분류로` 를 누르거나 카드를 끌면, 같은 목록을
# 두 스레드가 동시에 고친다 — 그러면 사진이 두 번 들어가거나 사라진다.
# 굽는 일(느린 쪽)은 잠금 밖에 두고, 목록을 고치는 순간만 잠근다.
def _detach(s, photo) -> None:
    with s.lock:
        for lst in _bins_of(s, photo.pool).values():
            if photo.id in lst:
                lst.remove(photo.id)
        photo.slot = None


def _put(s, photo, key: str, at: int | None = None, flips: dict | None = None) -> None:
    """상자에 넣는다. 풀은 사진이 들고 있다 — 두 풀이 섞이지 않는다.

    `flips` 는 반전 기본값 표다. 안 주면 `_sync_flip` 이 설정 파일을 읽는데,
    한 번에 여러 장을 넣는 분류에서는 장마다 읽게 되므로 그쪽이 넘겨 준다.
    """
    with s.lock:
        bins = _bins_of(s, photo.pool)
        old = next(((k, v.index(photo.id)) for k, v in bins.items() if photo.id in v), None)
        _detach(s, photo)
        lst = bins.setdefault(key, [])
        idx = len(lst) if at is None else max(0, min(int(at), len(lst) + 1))
        # 같은 상자 안에서 아래로 옮길 때, 빼내면서 뒤쪽 인덱스가 한 칸 당겨진다
        if old and old[0] == key and old[1] < idx:
            idx -= 1
        lst.insert(min(idx, len(lst)), photo.id)
        photo.slot = key
        # 반전 기본값은 카테고리에서 오고, 카테고리는 자리가 정해져야 나온다.
        M._sync_flip(s, photo, flips)


def _ref_slots(s) -> dict[str, str]:
    """기준 풀의 슬롯별 대표. `s.slots`(오늘 사진)와 짝을 이룬다."""
    return {k: v[0] for k, v in s.ref_bins.items() if k != "FACE" and v}


def _pool_photos(s, pool: str) -> list:
    return [p for p in s.photos if p.pool == pool]


# ── 분류 ──────────────────────────────────────────────────────────────────────
# 분류 모델은 **반전 없는 원본**(거울로 찍은 그대로)으로 학습됐다. 그런데 이 모드의
# 기준 풀에는 지난 차수의 **저장본**이 들어오는 일이 흔하고, 저장본은 반전이 이미
# 픽셀에 구워져 있다 — 학습 분포와 상하가 뒤집힌 채 들어가는 것이다.
#
# 실측(저장본 12장): 상악은 47.8% 로 문턱(70%)을 못 넘었고, **하악은 98.4% 확신으로
# 상악이라고** 나왔다. 두 장이 같은 상자로 몰리면 하악 기준이 비고, 하악 정합이
# 조용히 프레이밍으로 물러난다 — 사람에게는 '수동' 배지만 보인다.
#
# 그래서 **교합면으로 보이거나 확신이 낮으면 뒤집어서 한 번 더 묻고 더 확신하는
# 쪽을 쓴다.** 나머지 카테고리(얼굴·정면·측방)는 거울로 찍지 않아 뒤집어도 답이
# 같으므로 두 번 묻지 않는다 — 값을 치르는 자리를 좁혀 둔다.
def _mirror_classes() -> set[str]:
    """거울로 찍어 상하가 뒤집히는 카테고리 — 설정의 `flip_v_slots` 에서 뽑는다."""
    by_slot = {si.slot: cls for cls, si in M.cfg.intraoral_slots.items()}
    return {by_slot[sl] for sl in M.cfg.flip_v_slots if sl in by_slot}


def _predict(im, filename: str | None):
    """분류 한 번. 교합면이거나 미덥지 않으면 뒤집어서 한 번 더 묻는다."""
    from PIL import Image as _Im                                  # noqa: PLC0415

    got = M.classifier.predict(im.copy(), filename=filename)
    if got.label not in _mirror_classes() \
            and got.confidence >= M.cfg.thresholds.classify_confidence:
        return got
    flipped = M.classifier.predict(im.copy().transpose(_Im.FLIP_TOP_BOTTOM),
                                   filename=filename)
    return flipped if flipped.confidence > got.confidence else got


# ── (분류 본체) ──────────────────────────────────────────────────────────────────────
def _classify(s, targets: list) -> None:
    """라벨을 붙이고 제 풀의 상자에 넣는다.

    본편의 `_classify` 와 다른 점은 두 가지뿐이다: 상자가 풀마다 따로고, 케이스
    덱의 얼굴 자리 배정을 하지 않는다 (덱을 안 만드니 배정할 자리가 없다).
    """
    if not targets:
        return
    from PIL import Image as _Im

    thr = M.cfg.thresholds
    # 추론은 잠그지 않는다 — 느리고, 사진마다 독립이다. 잠그면 백그라운드
    # prewarm 이 도는 동안 화면의 요청이 통째로 기다린다.
    preds = {}
    for photo in targets:
        with _Im.open(photo.path) as im:
            preds[photo.id] = _predict(im, photo.orig_name)

    slot_by_class = M.cfg.slot_by_class
    flips = M._flip_defaults()          # 사진마다 파일을 다시 읽지 않게 한 번만
    with s.lock:
        _assign_all(s, targets, preds, slot_by_class, flips, thr)


def _assign_all(s, targets, preds, slot_by_class, flips, thr) -> None:
    """라벨을 붙이고 상자에 넣는다 — **잠금 안에서** 도는 부분."""
    # 추론이 도는 동안(느리다) 사람이 이 사진을 뺐을 수 있다. 그대로 넣으면
    # 상자에는 있는데 사진 목록에는 없는 열쇠가 남고, 그 뒤 **모든 응답이 404 로
    # 넘어진다**(`_review_json` 이 열쇠마다 사진을 찾는다) — 세션을 못 쓰게 된다.
    live = {p.id for p in s.photos}
    for photo in (p for p in targets if p.id in live):
        pred = preds[photo.id]
        photo.label, photo.confidence, photo.probs = (
            pred.label, pred.confidence, pred.probs)
        if photo.label in slot_by_class:
            _put(s, photo, slot_by_class[photo.label], flips=flips)
        elif photo.label in M.cfg.face.classes:
            _put(s, photo, "FACE", flips=flips)
        else:
            # OTHERS 등은 미배정으로 남긴다 — 화면에서 사람이 끌어 넣는다.
            M._sync_flip(s, photo, flips)
        if photo.confidence < thr.classify_confidence:
            photo.badge = "low"

    # 상자 안은 신뢰도 내림차순 — 맨 위가 대표다. 얼굴은 촬영순.
    for pool in POOLS:
        bins = _bins_of(s, pool)
        for key, lst in bins.items():
            if key == "FACE":
                seen = {pid: i for i, pid in enumerate(lst)}
                lst.sort(key=lambda pid: M._shot_order_key(M._photo(s, pid), seen[pid]))
            else:
                lst.sort(key=lambda pid: -M._photo(s, pid).confidence)


# ── 기준영상 ──────────────────────────────────────────────────────────────────
def _contain_state(pw: int, ph: int, win) -> EditorState:
    """사진 **전체**가 창에 들어가는 배율(contain). 남는 자리는 여백이 된다."""
    bw, bh = M.cover_base_ext_cm(pw, ph, win)
    return EditorState(scale=min(win.w / bw, win.h / bh))


def _ref_bake(s, slot: str):
    """기준 사진 대표를 **있는 그대로** 창에 앉힌다 (PPC 해상도).

    결과는 본편이 이전 차수 슬라이드에서 복원해 오는 기준영상과 같은 형태다:
    창 좌표계이고, 교합면이면 이미 뒤집힌 그림. 정합 대상이자 겹쳐보기 이미지로
    함께 쓰인다.

    **다시 자르지 않는다.** 기준 사진은 지난 차수의 완성본이고 정합이 맞춰야 할
    목표가 바로 그 프레임이다. 프레이밍 모델을 한 번 더 걸면 — 그 모델은 raw
    사진에서 자를 자리를 예측하도록 배웠으므로 — 이미 잘린 사진에서는 원본 경계
    밖까지 잡는다.

    **사진 전체를 넣는다**(contain). 창과 종횡비가 같은 저장본은 여백 없이 딱
    들어맞고, 다르면 짧은 쪽에 여백이 남는다. 그편이 잘라내는 것보다 낫다:
    구내 사진에서 잘려 나가는 것은 대개 후방 치아라, 기준이 잘리면 그에 맞춰
    이번 차수까지 좁아져 기록이 차수를 거듭할수록 줄어든다.
    """
    pid = _ref_slots(s).get(slot)
    if pid is None:
        with s.lock:
            s.references.pop(slot, None)
            s.ref_src.pop(slot, None)
        return None
    photo = M._photo(s, pid)
    with s.lock:
        if s.ref_src.get(slot) == (pid, photo.flip_v) and slot in s.references:
            return s.references[slot].get(REF_KEY)
    arr = M._imread(photo.path)
    if arr is None:
        return None
    win = s.slot_windows[slot]
    img = M.Cr.render_window(arr, win, _contain_state(photo.w, photo.h, win),
                             photo.flip_v, M.PPC, M.PPC,
                             M.Cr.hex_to_bgr(M._letterbox_color()))
    with s.lock:
        s.references[slot] = {REF_KEY: img}
        s.ref_src[slot] = (pid, photo.flip_v)
    return img


def _prewarm(s, photo) -> None:
    """기준 사진이 들어오는 즉시 분류하고 세그 캐시를 데운다.

    정합 때 기준영상에서 치아 중심점을 뽑는 일이 통째로 앞당겨진다. 사람이
    오늘 사진을 떨어뜨리고 화면을 넘기는 동안 백그라운드에서 끝나 있으므로,
    검수 진입까지의 기다림에서 그만큼이 사라진다.
    """
    try:
        _classify(s, [photo])
        slot = photo.slot
        if not slot or slot == "FACE":
            return
        arr = M._imread(photo.path)
        if arr is None:
            return
        # `_ref_bake` 와 **똑같이** 굽는다 — 픽셀이 한 톨이라도 다르면 이미지
        # 해시가 달라져 캐시가 안 맞고, 데운 보람이 없어진다.
        win = s.slot_windows[slot]
        img = M.Cr.render_window(arr, win, _contain_state(photo.w, photo.h, win),
                                 photo.flip_v, M.PPC, M.PPC,
                                 M.Cr.hex_to_bgr(M._letterbox_color()))
        M.Reg.centers(img, use_gate=True)
    except Exception as e:                                        # noqa: BLE001
        _audit({"event": "prewarm_error", "pid": photo.id,
                "error": f"{type(e).__name__}: {e}"[:200]})


# ── 정합 / 프레이밍 ───────────────────────────────────────────────────────────
def _frame_slot(s, slot: str) -> None:
    """슬롯 하나의 초기 구도 — 짝이 있으면 정합, 없으면 프레이밍 모델."""
    pid = s.slots.get(slot)
    if pid is None:
        with s.lock:
            s.progress.pop(slot, None)
        return
    s.progress[slot] = "run"
    photo = M._photo(s, pid)
    win = s.slot_windows[slot]
    ref_img = _ref_bake(s, slot)
    t0 = time.perf_counter()
    base = {"patient": s.patient_dir.name if s.patient_dir else "", "slot": slot}

    if ref_img is None:
        # 기준 풀이 비었거나 이 카테고리엔 짝이 없다 — 초진과 같은 처지다.
        if _ref_slots(s):
            _audit({"event": "register_skipped", "reason": "no_reference", **base})
        M._auto_frame(s, photo, win,
                      fallback_badge="manual" if _ref_slots(s) else None)
        photo.editor0 = photo.editor
        s.progress[slot] = "fallback" if _ref_slots(s) else "frame"
        return

    arr = M._imread(photo.path)
    # 기준영상은 반전이 이미 픽셀에 들어간 그림이다. 특징 매칭도 유사변환(det>0)도
    # 거울상은 다루지 못하므로 신규 사진을 같은 방향으로 맞춰서 넣는다. 그러면
    # 결과 변환이 곧 반전 프레임 기준이라 photo.editor 로 그대로 들어간다.
    arr_reg = cv2.flip(arr, 0) if photo.flip_v else arr
    try:
        pw = M.Reg.pseudo_frame(arr, M.framer, photo.label, flip_v=photo.flip_v)
        best, res, _ = M.Reg.register_best(
            arr_reg, {REF_KEY: ref_img},
            thresholds=M.cfg.thresholds.registration.model_dump(), prewarp=pw)
    except Exception as e:                                        # noqa: BLE001
        # 정합 오류가 검수 진입을 막으면 안 된다 — 남기고 프레이밍으로.
        _audit({"event": "register_error", **base,
                "error": f"{type(e).__name__}: {e}"[:300]})
        M._auto_frame(s, photo, win, fallback_badge="manual", bgr=arr)
        photo.editor0 = photo.editor
        s.progress[slot] = "fallback"
        return

    if res.ok:
        photo.editor = M.registration_to_editor(res.matrix, win, photo.w, photo.h)
        photo.ref_visit = REF_KEY
        photo.badge = "ok"
        photo.framing = "registration"
        s.progress[slot] = "reg"
    else:
        _audit({"event": "register_rejected", **base, "ref": best,
                "n_matches": res.n_matches, "n_inliers": res.n_inliers,
                "reproj_error_px": round(res.reproj_error_px, 2),
                "score": round(res.score, 4)})
        photo.ref_visit = REF_KEY
        M._auto_frame(s, photo, win, fallback_badge="manual", bgr=arr)
        s.progress[slot] = "fallback"
    photo.editor0 = photo.editor
    _audit({"event": "frame_timing", **base,
            "ms": round((time.perf_counter() - t0) * 1000)})


def _frame_face(s, pid: str) -> None:
    """얼굴 한 장. 짝이 없으므로 언제나 프레이밍 모델이다."""
    photo = M._photo(s, pid)
    M._auto_frame(s, photo, FACE_WINDOW)
    photo.editor0 = photo.editor


def _register(s, slots: list[str] | None = None, force: bool = False) -> list[str]:
    """배정이 확정된 뒤 도는 무거운 단계 — 슬롯별로 **병렬**이다.

    슬롯끼리는 완전히 독립이고 ONNX 세션은 동시 실행이 안전하다. 본편의 `_frame`
    이 순차인 것은 슬롯마다 기준 차수 후보가 여럿이라 작업 단위가 다르기
    때문이고, 여기는 슬롯당 기준이 하나라 그대로 나눌 수 있다.
    """
    want = list(slots) if slots is not None else list(s.slots) + [
        k for k in s.framed if k not in s.slots]
    jobs, done = [], []
    for slot in want:
        pid = s.slots.get(slot)
        if pid is None:
            s.framed.pop(slot, None)          # 비워진 자리 — 기록도 지운다
            s.progress.pop(slot, None)
            continue
        if not force and s.framed.get(slot) == (pid, M._photo(s, pid).flip_v):
            continue
        s.progress[slot] = "wait"
        jobs.append(slot)

    face_jobs = [pid for pid in s.face if force or pid not in s.face_framed]

    futs = [EXEC.submit(_frame_slot, s, slot) for slot in jobs]
    futs += [EXEC.submit(_frame_face, s, pid) for pid in face_jobs]
    # **끝까지 기다린 뒤에** 예외를 본다. 첫 예외에서 바로 던지면 남은 일꾼이 아직
    # 도는 채로 요청이 끝나고, 사람이 다시 누르면 같은 사진을 두 스레드가 함께
    # 고친다. 진행표도 wait/run 에 멈춘 채로 남는다.
    _wait(futs)
    for f in futs:
        if f.exception() is not None:
            raise f.exception()

    for slot in jobs:
        s.framed[slot] = (s.slots[slot], M._photo(s, s.slots[slot]).flip_v)
        done.append(slot)
    for pid in face_jobs:
        s.face_framed[pid] = M._photo(s, pid).flip_v
    return done


def _invalidate(s, photo) -> None:
    """이 사진이 걸린 계산 기록을 지운다 — 다음 정합에서 다시 돈다."""
    for slot, mark in list(s.framed.items()):
        if isinstance(mark, tuple) and mark[0] == photo.id:
            del s.framed[slot]
    s.face_framed.pop(photo.id, None)
    if photo.pool == "ref":
        for slot, src in list(s.ref_src.items()):
            if src[0] == photo.id:
                del s.ref_src[slot]
                s.references.pop(slot, None)


# ── JSON ──────────────────────────────────────────────────────────────────────
def _progress_json(s) -> dict:
    """정합 진행표. 병렬 작업이 고치는 중이라 잠그고 베낀다."""
    with s.lock:
        return dict(s.progress)


def _photo_json(s, p) -> dict:
    d = M._photo_json(s, p)
    d["pool"] = p.pool
    d["flip_user"] = p.flip_user
    return d


def _review_json(s) -> dict:
    names = list(M.cfg.ppt.slot_names)
    cur = {k: [_photo_json(s, M._photo(s, pid)) for pid in s.bins.get(k, [])]
           for k in names + ["FACE"]}
    ref = {k: [_photo_json(s, M._photo(s, pid)) for pid in s.ref_bins.get(k, [])]
           for k in names + ["FACE"]}
    others = {pool: [_photo_json(s, p) for p in _pool_photos(s, pool) if not p.slot]
              for pool in POOLS}
    slots = {slot: (_photo_json(s, M._photo(s, s.slots[slot])) if slot in s.slots else None)
             for slot in names}
    return {"fast": True, "visit": s.visit, "mode": s.mode,
            # 얼굴은 자리가 아니라 **사진** 단위라 창이 하나뿐이다. 검수 편집기가
            # 이 창으로 캔버스를 세운다(구내 4:3 가로, 얼굴 3:4 세로).
            "face_window": face_window_json(),
            "bins": cur, "ref_bins": ref, "others": others,
            "slots": slots,
            "face": [_photo_json(s, M._photo(s, pid)) for pid in s.face],
            # **사진이 있는가**로 본다(자리에 들어갔는가가 아니라). 분류가 어긋나
            # OTHERS 로 빠진 기준 사진도 화면에 나와야 사람이 끌어 넣을 수 있다.
            "has_ref": any(p.pool == "ref" for p in s.photos),
            "progress": _progress_json(s),
            "missing": [sl for sl in names if sl not in s.slots]}


def face_window_json() -> dict:
    """얼굴 검수 창 (cm). main 의 세션 응답도 이걸 쓴다."""
    w = FACE_WINDOW
    return {"x": w.x, "y": w.y, "w": w.w, "h": w.h}


def _state(s) -> dict:
    return {"photos": [_photo_json(s, p) for p in s.photos], "review": _review_json(s)}


# ── 엔드포인트 ────────────────────────────────────────────────────────────────
@router.post("/photos/{sid}")
async def add_photos(sid: str, pool: str = "cur", files: list[UploadFile] = File(...)):
    """한 드롭존이 받은 사진들. `pool` 이 왼쪽(ref)·오른쪽(cur)을 가른다."""
    if pool not in POOLS:
        raise HTTPException(400, "pool 은 ref 또는 cur")
    s = _fast(sid)
    staged = await M._stage_photos(s, files, pool=pool)
    if pool == "ref":
        # 기준은 들어오는 즉시 분류·베이크·캐시까지 백그라운드로 밀어 둔다.
        for photo in staged:
            EXEC.submit(_prewarm, s, photo)
    return {"added": len(staged), **_state(s)}


@router.delete("/photos/{sid}/{pid}")
def drop_photo(sid: str, pid: str):
    s = _fast(sid)
    # 배정과 같은 잠금 안에서 뺀다 — 백그라운드 분류가 막 넣으려는 참일 수 있다.
    with s.lock:
        photo = M._photo(s, pid)
        _invalidate(s, photo)
        _detach(s, photo)
        s.photos = [p for p in s.photos if p.id != pid]
    photo.path.unlink(missing_ok=True)
    return _state(s)


@router.post("/classify/{sid}")
def classify(sid: str):
    """아직 분류되지 않은 사진만 돌린다 — 기준 풀은 대개 prewarm 이 끝내 뒀다."""
    s = _fast(sid)
    _classify(s, [p for p in s.photos if p.label is None])
    return _state(s)


class RegisterReq(BaseModel):
    slots: list[str] | None = None
    force: bool = False


@router.post("/register/{sid}")
def register(sid: str, req: RegisterReq = Body(default=RegisterReq())):
    s = _fast(sid)
    done = _register(s, req.slots, force=req.force)
    return {"done": done, **_state(s)}


@router.get("/register/{sid}/status")
def register_status(sid: str):
    """정합이 도는 동안 화면이 폴링한다 — 슬롯별로 어디까지 왔는지."""
    s = _fast(sid)
    # 도는 중에 읽는다 — 베끼는 동안 크기가 바뀌면 파이썬이 예외를 던진다.
    with s.lock:
        prog = dict(s.progress)
    return {"progress": prog,
            "busy": any(v in ("wait", "run") for v in prog.values())}


class FlipReq(BaseModel):
    session_id: str
    photo_id: str
    on: bool


@router.post("/flip")
def flip(req: FlipReq):
    """사진 한 장의 상하반전. 사람이 고른 값은 분류가 바뀌어도 덮이지 않는다."""
    s = _fast(req.session_id)
    photo = M._photo(s, req.photo_id)
    photo.flip_user = True
    want = bool(req.on)
    if want != photo.flip_v:
        photo.flip_v = want
        photo.editor = M.flip_editor_v(photo.editor)
        photo.editor0 = M.flip_editor_v(photo.editor0)
        _invalidate(s, photo)
    return _state(s)


@router.get("/review/{sid}")
def review(sid: str):
    return _state(_fast(sid))


# ── 환자 없이 진행하기 ────────────────────────────────────────────────────────
# 환자를 고르면 차수·레이아웃·이름 규칙이 전부 그 환자에게서 온다. 고르지 않으면
# 그럴 것이 없으므로 사람이 **저장 폴더 이름과 파일 접두어**를 직접 적는다.
# 등록되지 않은 사람의 사진을 한 번 자르고 넘기는 자리다 — 장부를 만들지 않는다.
_BAD_CHARS = set('\\/:*?"<>|')


def _check_folder_name(name: str) -> str:
    """저장할 자리. **이름 하나**이거나 **절대 경로**다.

    이름만 적으면 저장 루트 아래로 간다. 탐색기로 고른 경로가 오면 그대로 쓴다 —
    외장 드라이브나 바탕화면처럼 루트 밖에 한 번 떨어뜨리고 마는 자리가 있고,
    이 모드는 애초에 장부를 만들지 않으므로 루트 안에 가둘 이유가 없다.
    """
    got = (name or "").strip().strip('"')
    if not got:
        raise HTTPException(400, "저장할 폴더 이름을 적어 주세요")
    if Path(got).is_absolute():
        parent = Path(got).parent
        if not parent.is_dir():
            raise HTTPException(400, f"상위 폴더가 없습니다: {parent}")
        return str(Path(got))
    got = got.strip(".")
    if not got:
        raise HTTPException(400, "저장할 폴더 이름을 적어 주세요")
    if _BAD_CHARS & set(got):
        raise HTTPException(400, '폴더 이름에 \\ / : * ? " < > | 는 쓸 수 없습니다')
    return got


def _folder_mode(s) -> bool:
    """환자 없이 여는 세션인가. 환자를 골랐으면 `ids` 가 있다."""
    return s.ids is None


def _dest(s) -> Path:
    if not _folder_mode(s):
        return s.patient_dir
    got = Path(s.folder)
    return got if got.is_absolute() else M.ROOT / s.folder


def _label(s) -> str:
    """화면·접두어에 쓸 짧은 이름. 절대 경로면 마지막 칸만 쓴다."""
    got = Path(s.folder)
    return got.name if got.is_absolute() else s.folder


# 별칭이 `(숫자)` 꼴이면 본편의 번호 규칙을 그대로 쓴다 — 아래 `_folder_names` 참고.
_NUM_ALIAS = re.compile(r"^\((\d+)\)$")


def _default_aliases() -> dict[str, str]:
    """별칭 기본값 — **본편이 파일에 붙이는 번호 그대로**다.

    본편은 `12345_A (1).jpg` … `(5)` 로 구내를 세고 얼굴은 `(6)` 부터 이어 센다.
    환자 없이 저장할 때도 같은 번호를 쓰면, 나중에 두 경로로 저장한 폴더를 나란히
    놓아도 사람이 같은 규칙으로 읽는다. 번호는 설정(config.yaml)에서 뽑는다 —
    여기 베껴 두면 설정을 고친 날 조용히 갈라진다.
    """
    out = {cls: f"({si.index})" for cls, si in M.cfg.intraoral_slots.items()}
    out["FACE"] = f"({M.cfg.face.start_index})"
    return out


def _naming_prefs() -> dict:
    """파일 이름 취향 — 번호 규칙·시작 번호·구분자·카테고리 별칭.

    환자 폴더에 저장할 때는 쓰이지 않는다. 그쪽은 본편의 `photo_pattern` 이
    진실이고, 한 폴더에 이름 체계가 둘이면 사람이 못 읽는다.
    """
    d = M._setting("naming") or {}
    aliases = _default_aliases()
    got = d.get("aliases")
    if isinstance(got, dict):
        for k, v in got.items():
            if k not in aliases:
                continue
            sv = FN.sanitize(str(v))
            # 옛 판본은 별칭 기본값이 **카테고리 이름 그대로**였다(IO_FRONT → "IO_FRONT").
            # 그 값이 남아 있으면 사람이 고른 것이 아니라 옛 기본값이 굳은 것이므로,
            # 새 기본값(본편 번호)에 자리를 내준다. 그러지 않으면 새 설치본과 옛
            # 설치본이 같은 설정을 두고 서로 다른 이름을 만든다.
            if sv and sv != k:
                aliases[k] = sv
    mode, sep, start = d.get("number_mode"), d.get("separator"), d.get("start")
    return {"number_mode": mode if mode in ("multi_only", "always") else "multi_only",
            "start": int(start) if start in (0, 1, "0", "1") else 1,
            # 기본은 **공백 1칸**이다. 본편의 사진 이름이
            #   {교정번호}_{차수}[공백]({순번}).jpg   →  12345_A (1).jpg
            # 이라서, `_` 는 신원 조각(번호·차수)을 잇고 **공백이 신원과 번호를
            # 가른다**. 환자 없이 저장할 때의 접두어가 곧 그 신원 자리이므로,
            # 같은 자리에 오는 구분자도 공백이라야 두 경로의 이름이 한 모양이 된다.
            "separator": sep if isinstance(sep, str) and 0 < len(sep) <= 3
                             and not (_BAD_CHARS & set(sep)) else " ",
            "aliases": aliases}


class FolderReq(BaseModel):
    folder: str
    prefix: str = ""          # 비우면 폴더 이름을 그대로 쓴다


@router.post("/session")
def session_open(req: FolderReq):
    """환자 없이 세션을 연다. 차수도 덱도 없다 — 폴더 하나와 접두어뿐이다."""
    name = _check_folder_name(req.folder)
    prefix = (req.prefix or "").strip()
    if prefix and (_BAD_CHARS & set(prefix)):
        raise HTTPException(400, '접두어에 \\ / : * ? " < > | 는 쓸 수 없습니다')
    s = M.Session("first", None, "")
    s.fast = True
    s.folder = name
    s.prefix = prefix
    M.SESSIONS[s.id] = s
    # 화면(startSession)이 읽는 모양을 본편과 맞춰 둔다 — 이름표 자리에는
    # 환자 이름 대신 폴더 이름이 뜬다.
    return {"session_id": s.id, "fast": True, "folder": name,
            "face_window": face_window_json(),
            "prefix": prefix or _label(s), "dir": str(_dest(s)),
            "mode": "first", "visit": "",
            "ids": {"name": _label(s), "hospital_id": "", "ortho_id": ""},
            "prev_visits": [], "ppt_exists": False, "folder_exists": _dest(s).is_dir(),
            "windows": M._windows_json(s.slot_windows)}


# ── 설정 ──────────────────────────────────────────────────────────────────────
# 이 모드에만 쓰이는 취향 두 가지. 본편의 `/api/prefs` 와 **같은 `settings.json`**
# 에 쓰되 엔드포인트를 갈라 둔다 — main.py 를 흔들지 않기 위해서다(파일 머리말 참고).
def _prefs_json() -> dict:
    return {"flip_defaults": M._flip_defaults(),
            "flip_defaults_default": M.FLIP_DEFAULTS,
            "classes": M.FLIP_CLASSES,
            "naming": _naming_prefs(),
            "aliases_default": _default_aliases(),
            "example": _name_example()}


class PrefsReq(BaseModel):
    flip_defaults: dict | None = None     # {"ref": {카테고리: bool}, "cur": {...}}
    naming: dict | None = None            # number_mode · start · separator · aliases


@router.get("/prefs")
def prefs_get():
    return _prefs_json()


@router.post("/prefs")
def prefs_set(req: PrefsReq):
    """보낸 항목만 바꾼다. 화면이 늘 전부를 보내야 한다면 한 항목을 고칠 때마다
    나머지를 실어 나르다가 언젠가 하나를 빠뜨린다."""
    path = M.SETTINGS_FILE                     # 읽은 그 파일에만 쓴다
    try:
        d = M.json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        d = {}

    if req.flip_defaults is not None:
        clean = {}
        for pool in POOLS:
            got = req.flip_defaults.get(pool)
            if not isinstance(got, dict):
                raise HTTPException(400, "flip_defaults 는 ref/cur 두 그리드여야 합니다")
            bad = set(got) - set(M.FLIP_CLASSES)
            if bad:
                raise HTTPException(400, f"모르는 카테고리: {', '.join(sorted(bad))}")
            clean[pool] = {k: bool(v) for k, v in got.items()}
        d["flip_defaults"] = clean

    if req.naming is not None:
        cur = d.get("naming") or {}
        got = req.naming
        if "number_mode" in got:
            if got["number_mode"] not in ("multi_only", "always"):
                raise HTTPException(400, "number_mode 는 multi_only 또는 always")
            cur["number_mode"] = got["number_mode"]
        if "start" in got:
            if got["start"] not in (0, 1, "0", "1"):
                raise HTTPException(400, "시작 번호는 0 또는 1")
            cur["start"] = int(got["start"])
        if "separator" in got:
            sep = str(got["separator"])
            if not (0 < len(sep) <= 3) or (_BAD_CHARS & set(sep)):
                raise HTTPException(400, "구분자는 1~3글자, 파일명 금지 문자는 못 씁니다")
            cur["separator"] = sep
        if "aliases" in got:
            al = got["aliases"]
            if not isinstance(al, dict):
                raise HTTPException(400, "aliases 는 카테고리→이름 표여야 합니다")
            bad = set(al) - set(FN.DEFAULT_ALIASES)
            if bad:
                raise HTTPException(400, f"모르는 카테고리: {', '.join(sorted(bad))}")
            # 기본값과 같은 값은 **저장하지 않는다.** 굳혀 두면 나중에 설정의
            # 번호를 바꿔도 저장된 옛 값이 이겨서 조용히 갈라진다.
            base = _default_aliases()
            merged = dict(cur.get("aliases") or {})
            for k, v in al.items():
                sv = FN.sanitize(str(v))
                if not sv:
                    raise HTTPException(400, f"'{v}' 는 파일 이름으로 쓸 수 없습니다")
                # `sv == k` 는 옛 판본의 기본값이라 읽을 때 무시된다(_naming_prefs).
                # 저장해 두면 설정에는 남는데 화면에는 `(1)` 로 되돌아와, 사람은
                # 값이 씹혔다고 읽는다 — 아예 남기지 않는다.
                if sv == base.get(k) or sv == k:
                    merged.pop(k, None)
                else:
                    merged[k] = sv
            cur["aliases"] = merged
        d["naming"] = cur

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(M.json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return _prefs_json()


@router.get("/naming")
def naming_prefs(prefix: str = ""):
    """파일 이름 규칙. 화면이 '이렇게 저장됩니다' 예시를 만들 때 쓴다.

    예시를 화면에서 지어내지 않고 **저장이 실제로 쓰는 값**을 내려보낸다 —
    둘이 갈라지면 사람은 보지도 못한 이름을 보고 확정하게 된다.
    """
    return {**_naming_prefs(), "aliases_default": _default_aliases(),
            # 이 접두어로 저장하면 실제로 나올 이름들 (구내 5 + 얼굴 2)
            "example": _name_example(prefix), "ext": "jpg"}


class NamesReq(BaseModel):
    folder: str
    prefix: str = ""


@router.post("/session/{sid}/names")
def session_names(sid: str, req: NamesReq):
    """열려 있는 폴더 세션의 저장 폴더·접두어를 고친다.

    사진을 넣은 뒤에야 오타를 알아채는 일이 흔하다. 이름은 저장할 때 비로소
    쓰이므로, 세션을 버리지 않고 여기서 바꿔 준다.
    """
    s = _fast(sid)
    if not _folder_mode(s):
        raise HTTPException(400, "환자 폴더에 저장하는 세션입니다")
    prefix = (req.prefix or "").strip()
    if prefix and (_BAD_CHARS & set(prefix)):
        raise HTTPException(400, '접두어에 \\ / : * ? " < > | 는 쓸 수 없습니다')
    s.folder = _check_folder_name(req.folder)
    s.prefix = prefix
    return {"folder": s.folder, "prefix": prefix or _label(s),
            "name": _label(s),
            "dir": str(_dest(s)), "folder_exists": _dest(s).is_dir()}


# ── 배정 조작 ─────────────────────────────────────────────────────────────────
class AssignReq(BaseModel):
    session_id: str
    photo_id: str
    slot: str | None = None       # None 이면 상자에서 빼서 OTHERS 로
    at: int | None = None


@router.post("/assign")
def assign(req: AssignReq):
    """드래그로 분류를 고친다. 사진이 제 풀을 들고 다니므로 두 줄이 섞이지 않는다."""
    s = _fast(req.session_id)
    photo = M._photo(s, req.photo_id)
    _invalidate(s, photo)
    if req.slot:
        _put(s, photo, req.slot, req.at)
    else:
        _detach(s, photo)
    return _state(s)


class SortReq(BaseModel):
    session_id: str
    slot: str
    pool: str = "cur"


@router.post("/sort")
def sort_bin(req: SortReq):
    """상자 안을 촬영 순서로 세운다 — EXIF 시각, 없으면 일련번호·파일명."""
    if req.pool not in POOLS:
        raise HTTPException(400, "pool 은 ref 또는 cur")
    s = _fast(req.session_id)
    lst = _bins_of(s, req.pool).get(req.slot, [])
    seen = {pid: i for i, pid in enumerate(lst)}
    lst.sort(key=lambda pid: M._shot_order_key(M._photo(s, pid), seen[pid]))
    n = len(lst)
    with_time = sum(1 for pid in lst if M._photo(s, pid).taken_at)
    return {"n": n, "with_time": with_time, **_state(s)}


class AdjustReq(BaseModel):
    session_id: str
    photo_id: str                 # 자리가 아니라 **사진**이다 — 얼굴은 슬롯이 없다
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0
    angle: float = 0.0


@router.post("/adjust")
def adjust(req: AdjustReq):
    """얼굴 사진의 구도. 구내 다섯 자리는 본편의 `/api/adjust` 가 그대로 받는다 —
    그쪽은 슬롯으로 찾는데 얼굴에는 슬롯이 없어서 여기만 따로 둔다."""
    s = _fast(req.session_id)
    photo = M._photo(s, req.photo_id)
    win = FACE_WINDOW if photo.slot == "FACE" else s.slot_windows[photo.slot]
    bw, bh = M.cover_base_ext_cm(photo.w, photo.h, win)
    st = M._clamp(EditorState(req.dx, req.dy, req.scale, req.angle), win, bw, bh)
    photo.editor = st
    pl = M.editor_to_placement(st, win, bw, bh, M.PPC)
    return {"placement": {"off_x": pl.off_x, "off_y": pl.off_y,
                          "ext_cx": pl.ext_cx, "ext_cy": pl.ext_cy, "rot": pl.rot},
            "clamped_scale": st.scale}


# ── 저장 ──────────────────────────────────────────────────────────────────────
# 이름은 **본편과 같은 규칙**을 쓴다 (`naming.photo_pattern`). 한 환자 폴더에
# 이름 체계가 둘이면 나중에 어느 것이 무엇인지 사람이 못 읽는다. 차수 글자도
# 본편이 PPT 에서 읽어 온 그것 그대로다 — 이 파일은 장부를 새로 만들지 않는다.
def _existing(s) -> set[str]:
    """저장될 폴더에 이미 있는 파일(폴더 기준 상대경로, 소문자)."""
    d = _dest(s)
    if not d or not d.is_dir():
        return set()
    return {q.relative_to(d).as_posix().lower() for q in d.rglob("*") if q.is_file()}


def _entries(s) -> list[dict]:
    """저장 순서대로 늘어선 사진. 이름을 짓기 전의 뼈대다."""
    out = []
    for slot in M.cfg.ppt.slot_names:
        for i, pid in enumerate(s.bins.get(slot, [])):
            out.append({"pid": pid, "slot": slot, "category": M._slot_to_class(slot),
                        "extra": i > 0, "n": i + 1})
    for pid in s.face:
        out.append({"pid": pid, "slot": "FACE", "category": "FACE",
                    "extra": False, "n": 1})
    return out


def _patient_names(s, entries, ppre):
    """환자 폴더에 저장할 때의 이름 — **본편 규칙 그대로**(`naming.photo_pattern`).

    한 환자 폴더에 이름 체계가 둘이면 나중에 어느 것이 무엇인지 사람이 못 읽는다.
    차수 글자도 본편이 PPT 에서 읽어 온 그것 그대로다.
    """
    ids, pat = s.ids, M.cfg.naming
    want, idxs = [], []
    fidx = M.cfg.face.start_index
    for e in entries:
        if e["slot"] == "FACE":
            idx = fidx; fidx += 1
        else:
            idx = M.cfg.index_by_class[e["category"]]
        idxs.append(idx)
        want.append(ppre + (
            M.N.photo_extra_filename(ids.ortho_id, s.visit, idx, e["n"],
                                     pat.photo_extra_pattern) if e["extra"]
            else M.N.photo_filename(ids.ortho_id, s.visit, idx, pat.photo_pattern)))

    def bump(i, taken):
        """겹치면 본편의 '같은 자리 추가 촬영본' 서식으로 다음 빈 번호를 받는다."""
        n = 2
        while True:
            nm = ppre + M.N.photo_extra_filename(
                s.ids.ortho_id, s.visit, idxs[i], n, pat.photo_extra_pattern)
            if nm.lower() not in taken:
                return nm
            n += 1

    return want, bump


def _folder_names(s, entries):
    """환자 없이 진행할 때의 이름 — `{접두어}{구분자}{이름}`.

    **구분자는 접두어와 이름을 잇는 자리다.** 늘 쓰이므로 설정에서 바꾸면 파일
    이름이 바로 달라진다. 이름은 카테고리마다 정하고, 기본값은 본편이 붙이는
    번호 그대로다((1)~(5), 얼굴 (6)).

    이름이 **`(숫자)` 꼴**이면 본편의 번호 규칙을 그대로 쓴다:
      · 같은 카테고리의 대표가 여럿이면(얼굴) **숫자가 올라간다** — (6) (7) (8)
      · 같은 자리의 추가 촬영본은 뒤에 `-2`, `-3` 이 붙는다 — (1)-2
    글자 이름(`정면` 처럼 사람이 바꾼 것)이면 여러 장일 때 뒤에 번호가 붙는데,
    그 앞에도 같은 구분자가 온다 — `정면_1`, `정면_2`.

    접두어를 비웠으면 폴더 이름을 그대로 쓴다.
    """
    naming = _naming_prefs()
    prefix = s.prefix or _label(s)
    al, sep = naming["aliases"], naming["separator"]
    join = f"{prefix}{sep}" if prefix else ""
    total: dict[str, int] = {}
    for e in entries:
        total[e["category"]] = total.get(e["category"], 0) + 1

    seen: dict[str, int] = {}
    stems, numeric = [], []
    for e in entries:
        cat = e["category"]
        name = FN.alias_of(cat, al)
        m = _NUM_ALIAS.match(name)
        k = seen.get(cat, 0)
        seen[cat] = k + 1
        numeric.append(bool(m))
        if m:
            n = int(m.group(1))
            stems.append(f"{join}({n})-{e['n']}" if e["extra"] else f"{join}({n + k})")
        else:
            need = naming["number_mode"] == "always" or total[cat] > 1
            base = join + name
            stems.append(f"{base}{sep}{naming['start'] + k}" if need else base)
    want = [st + ".jpg" for st in stems]

    def bump(i, taken):
        """겹치면 뒤에 번호를 붙여 첫 빈 이름을 준다. 번호 이름은 본편처럼 `-n`."""
        if numeric[i]:
            n = 2
            while f"{stems[i]}-{n}.jpg".lower() in taken:
                n += 1
            return f"{stems[i]}-{n}.jpg"
        stem_taken = {t[:-4] if t.endswith(".jpg") else t for t in taken}
        got = FN.bump(stems[i].lower(), stem_taken, sep)
        # bump 는 소문자로 비교한다 — 원 표기를 살려 다시 붙인다
        return stems[i] + got[len(stems[i].lower()):] + ".jpg"

    return want, bump


class _ExampleSession:
    """이름 예시를 만들 때만 쓰는 껍데기 — `_folder_names` 는 접두어만 본다."""
    ids = None

    def __init__(self, prefix: str):
        self.prefix = self.folder = prefix


def _name_example(prefix: str = "접두어") -> dict:
    """지금 규칙이 **실제로** 만드는 이름. 화면이 지어내지 않게 서버가 준다.

    구내와 얼굴을 나눠 돌려준다 — 얼굴은 번호가 이어 오르는 것이 이 규칙의
    핵심인데, 한 줄로 늘어놓으면 `(6)` 부터가 얼굴이라는 것이 안 보인다.
    """
    # 자리 순서가 아니라 **번호 순서**로 늘어놓는다 — 예시는 읽으라고 있는 것이라
    # (1)(2)(3)… 으로 이어져야 규칙이 한눈에 들어온다.
    io = [{"category": cls, "extra": False, "n": 1}
          for cls, _si in sorted(M.cfg.intraoral_slots.items(),
                                 key=lambda kv: kv[1].index)]
    face = [{"category": "FACE", "extra": False, "n": 1} for _ in range(2)]
    want, _ = _folder_names(_ExampleSession(prefix or "접두어"), io + face)
    # `join` 은 이름들이 공유하는 머리(접두어+구분자)다. 화면이 두 번째부터
    # 꼬리만 보일 때 이만큼을 뗀다 — 접두어만 떼면 구분자가 남아 `_(2)` 가 된다.
    return {"prefix": prefix or "접두어",
            "join": f"{prefix or '접두어'}{_naming_prefs()['separator']}",
            "io": want[:len(io)], "face": want[len(io):]}


def _build_plan(s, overwrite: set[str] | None = None) -> dict:
    """확정하면 **무엇이 어떤 이름으로 어디에 생기는지** 계산한다. 부수효과 없음.

    `commit` 이 이 결과를 그대로 쓴다 — 검토 화면과 실제 저장이 각자 이름을
    계산하면 언젠가 반드시 갈라지고, 그건 기록물에서 나면 안 되는 버그다.

    본편과 다른 것은 **덮어쓰기 판정**이 붙는다는 점이다. 차수의 진실은 PPT 인데
    이 모드는 PPT 를 쓰지 않으므로, 사람이 PowerPoint 에 차수를 넣기 전에 같은
    환자를 한 번 더 돌리면 차수 글자가 같아진다. 그때 조용히 덮어쓰지 않고
    **파일마다 [자동 번호 | 덮어쓰기] 를 사람에게 물어본다.**
    """
    folder = _folder_mode(s)
    if folder and not s.folder:
        raise HTTPException(400, "저장할 폴더 이름을 먼저 적어 주세요")
    picked = {o.lower() for o in (overwrite or set())}
    rdir = M._raw_dir()
    raw = rdir != "none"
    entries = _entries(s)

    if folder:
        ppre = ""
        rpre = "raw/"
        want, bump = _folder_names(s, entries)
    else:
        ids = s.ids
        ppre = "" if M._photo_dir() == "flat" else f"{M.N.visit_dir(ids.ortho_id, s.visit)}/"
        rpre = "" if rdir == "flat" else f"{M.N.visit_raw_dir(ids.ortho_id, s.visit)}/"
        want, bump = _patient_names(s, entries, ppre)

    taken = _existing(s)
    files = []
    for i, (e, nm) in enumerate(zip(entries, want)):
        exists = nm.lower() in taken
        if exists and nm.lower() not in picked:
            final, action = bump(i, taken), "number"
        else:
            final, action = nm, ("overwrite" if exists else "new")
        taken.add(final.lower())
        photo = M._photo(s, e["pid"])
        stem = Path(final).stem
        files.append({
            "pid": e["pid"], "slot": e["slot"], "extra": e["extra"],
            "label": photo.label,
            # base = 충돌 전 원래 이름. 화면의 선택과 commit 의 overwrite 목록이
            # 이 이름을 열쇠로 쓴다.
            "base": nm, "file": final, "exists": exists, "action": action,
            "raw": (rpre + (FN.raw_name(stem, photo.orig_name) if folder
                            else M.N.raw_filename(Path(final).name, photo.path.name))
                    if raw and not e["extra"] else None),
        })

    dest = _dest(s)
    return {"patient_dir": str(dest),
            "patient_dir_exists": dest.is_dir(),
            "folder_mode": folder,
            "folder": _label(s) if folder else (dest.name if dest else ""),
            "prefix": (s.prefix or _label(s)) if folder else "",
            "visit": s.visit, "fast": True,
            "files": files,
            "save_raw": raw,
            # 이 모드는 PPT 를 만들지도 고치지도 않는다. 화면이 그 사실을 사람에게
            # 알려야 한다 — 차수는 사람이 PowerPoint 에서 넣어야 남는다.
            "writes_ppt": False,
            "missing": [sl for sl in M.cfg.ppt.slot_names if sl not in s.slots]}


@router.get("/plan/{sid}")
def plan(sid: str, overwrite: str = ""):
    """저장 직전 검토용 드라이런. 아무것도 쓰지 않는다."""
    picked = {x for x in overwrite.split("|") if x}
    return _build_plan(_fast(sid), picked)


# 확정 저장이 마지막으로 쓴 폴더. 저장이 끝나면 세션이 사라지므로, "방금 저장한
# 곳을 열어 보자" 를 위해 여기 한 칸만 남겨 둔다.
_LAST_SAVED: Path | None = None


class OpenReq(BaseModel):
    session_id: str = ""


@router.post("/open")
def open_dir(req: OpenReq):
    """저장 폴더를 탐색기로 연다.

    **경로를 화면에서 받지 않는다.** 열 자리는 둘뿐이다 — 열려 있는 세션이 저장할
    곳(저장 전)이거나, 방금 저장한 곳(저장 후). 이 모드는 저장 루트 밖도 고를 수
    있어서 본편의 `/api/open-dir`(루트 기준 상대경로)로는 안 되는데, 그렇다고
    브라우저가 보낸 절대경로를 그대로 여는 것은 다른 이야기다.
    """
    dest = _dest(_fast(req.session_id)) if req.session_id else _LAST_SAVED
    if dest is None or not dest.is_dir():
        raise HTTPException(404, "아직 만들어지지 않은 폴더입니다")
    M._os_open(dest)
    return {"ok": True, "opened": str(dest)}


class CommitReq(BaseModel):
    # 덮어쓰기로 고른 파일들의 `base` 이름. 여기 없는 충돌은 자동 번호를 받는다.
    overwrite: list[str] = []


@router.post("/commit/{sid}")
def commit(sid: str, req: CommitReq = Body(default=CommitReq()),
           allow_missing: bool = False):
    """사진만 환자 폴더에 원자적으로 쓴다. 덱은 만들지도 고치지도 않는다."""
    s = _fast(sid)
    pl = _build_plan(s, set(req.overwrite))
    dest = _dest(s)
    if pl["missing"] and not allow_missing:
        return M.JSONResponse(status_code=409, content={
            "error": "missing_slots", "missing": pl["missing"]})

    try:
        with M.S.Transaction(dest) as tx:
            for e in pl["files"]:
                photo = M._photo(s, e["pid"])
                if e["extra"]:
                    # 같은 자리의 추가 촬영본은 편집값이 없다 — 본편과 같이
                    # 원본 그대로 간다.
                    tx.stage_file(photo.path, e["file"])
                else:
                    win = FACE_WINDOW if e["slot"] == "FACE" else s.slot_windows[e["slot"]]
                    baked, _wh = M._bake_window(photo, win, photo.editor, photo.flip_v,
                                                s.tmp / f"bake_{e['pid']}.jpg")
                    # 굽지 못하면 원본으로 물러난다 — 저장 자체가 실패하는 것보다 낫다.
                    tx.stage_file(baked or photo.path, e["file"])
                if e["raw"]:
                    tx.stage_file(photo.path, e["raw"])
            moved = tx.commit()
    except Exception as e:                                        # noqa: BLE001
        _audit({"event": "commit_failed", "fast": True,
                "error": f"{type(e).__name__}: {e}"[:300]})
        raise HTTPException(500, f"확정 실패(롤백됨): {e}")

    global _LAST_SAVED
    _LAST_SAVED = dest            # 저장이 끝나면 세션이 사라진다 — 자리만 남겨 둔다
    _audit({"event": "commit", "fast": True, "visit": s.visit,
            "patient": dest.name, "folder_mode": _folder_mode(s),
            "files": [q.relative_to(dest).as_posix() for q in moved],
            "slots": {k: M._photo(s, v).label for k, v in s.slots.items()}})
    out = {"ok": True, "patient_dir": str(dest), "visit": s.visit,
           "folder_mode": _folder_mode(s),
           "files": [q.relative_to(dest).as_posix() for q in moved]}
    M.discard_session(s)
    return out


# ── 앱에 붙이기 ───────────────────────────────────────────────────────────────
# main 과 이 파일은 서로를 부른다. 붙이는 일을 **이쪽 끝에서** 하면 어느 쪽이
# 먼저 임포트되든 그 시점엔 양쪽이 다 서 있다 (main.py 맨 끝 주석 참고).
#
# 두 번 붙지 않게 **앱에** 표를 남긴다. `app.routes` 를 훑어 확인하려 했는데
# 그게 안 된다 — 이 FastAPI 는 포함된 라우터를 `_IncludedRouter` 로 감싸 넣고
# 그 객체에는 `path` 가 없다. 경로로 찾으면 늘 '없다'가 나와서, 재임포트가
# 있으면 같은 라우터가 조용히 두 벌 쌓인다. 표는 모듈이 아니라 앱에 두어야
# `importlib.reload` 를 견딘다(모듈 변수는 재실행 때 초기화된다).
if not getattr(M.app, "_fastlap_installed", False):
    M.app.include_router(router)
    M.app._fastlap_installed = True
