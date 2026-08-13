"""
차수 간 사진 정합 — **치아 중심점** 기반 (Stage 4)

분할 모델이 낸 치아 하나하나의 무게중심을 대응점으로 쓴다. 치아 중심점은 해부학적
실체라 회차가 달라도 같은 자리에 있고, 교정으로 실제 움직인 치아는 `fit_robust` 가
잔차로 걸러낸다. (이미지 특징점 매칭은 구강 사진에서 코너가 치면 반사광·타액·
와이어에 몰려 회차 간에 재현되지 않는다 — 실측 전패로 폐기·삭제됐다, 2026-08-12.)

### 파이프라인

    pseudo_frame        →  프레이밍 모델의 **임시 crop** — 분할기 입력을 학습 분포
                           (완성본 모습)로 맞춘다 (문서 `REVISIT_REGISTRATION.md`
                           §"pseudo crop 과 최종 crop")
    orthoreg.Segmenter  →  전경(치아 ∪ 장치) · 중심 히트맵 · 오프셋 → 인스턴스
    usable              →  대응에 못 쓸 마스크 배제 (기본 켜짐)
    match_ransac        →  번호 없이 짝짓기 (2점 표본 + MSAC)
    fit_robust          →  MAD 절사로 움직인 치아 제외 → 닮음변환
    합성                →  (pseudo→ref) ∘ (raw→pseudo) = raw→ref

`usable` 게이트는 **기본이 켜져 있다** (2026-08-12 결정). 연구 코퍼스(완성본↔완성본)
에서는 켜도 파국이 48 → 47 로 유의차가 없었지만, 그 측정은 양쪽 입력이 이미 학습
분포일 때 이야기다. 배포 실사(3_ab, 5뷰)에서 정합이 전부 잔차 게이트에 걸렸고, 못 쓸
마스크(경계 잘림·파편)를 미리 빼는 쪽을 택했다. 끄려면 `use_gate=False`.

### 좌표계 — 매칭은 pseudo 에서, 반환은 raw→ref 로

검출은 1024² 에서 하고 중심점은 **입력된 그 이미지의 픽셀 좌표**로 되돌린다 (종횡비가
달라도 리사이즈 왜곡이 변환에 안 섞인다). 매칭·적합은 **pseudo crop 좌표 ↔ 기준영상
좌표**에서 한다 — 둘 다 완성본 급이라 후보 배율에 해부학적 변화(≈1±α)만 남는다.
raw px ↔ ref px 를 직접 매칭하면 해상도 비율(0.4~0.6)이 배율에 곱해져
`match_ransac` 의 `scale_range=(0.5, 2.0)` 상식 검사가 **참해를 후보에서 배제**한다
(실측 3_ab: 교합면 참배율 0.41~0.44 로 전멸). 해상도 비율은 마지막 합성에서만
들어가고, 반환 `matrix` 는 언제나 new_px→ref_px 라 호출부는 이를 모른다.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class RegistrationResult:
    ok: bool
    method: str
    scale: float           # 합성된 new_px→ref_px 의 배율 (해상도 비율 포함)
    angle_deg: float
    tx: float
    ty: float
    n_matches: int         # 대응 후보 수 = min(신규 치아, 기준 치아)
    n_inliers: int         # fit_robust 절사 후 남은 짝
    inlier_ratio: float
    reproj_error_px: float # 인라이어 평균 잔차 (ref 픽셀)
    score: float           # 두 기준 비교용 — 잔차/치아간격만 본다
    matrix: list           # 2x3 new_px→ref_px (유사변환)

    def summary(self) -> str:
        return (f"[{'OK ' if self.ok else 'LOW'}] {self.method:5s} "
                f"scale={self.scale:.3f} angle={self.angle_deg:+.2f}° "
                f"t=({self.tx:+.1f},{self.ty:+.1f}) "
                f"inliers={self.n_inliers}/{self.n_matches} "
                f"({self.inlier_ratio:.2f}) reproj={self.reproj_error_px:.2f}px "
                f"score={self.score:.1f}")


def decompose_similarity(M: np.ndarray) -> tuple[float, float, float, float]:
    """유사변환 2x3 → (scale, angle_deg, tx, ty)."""
    a, b = M[0, 0], M[1, 0]
    scale = math.hypot(a, b)
    angle = math.degrees(math.atan2(b, a))
    return scale, angle, float(M[0, 2]), float(M[1, 2])

# ── orthoreg (추론 코어) ─────────────────────────────────────────────────────
# 배포 트리에서는 `webapp/` 의 형제다. 연구 저장소에서 쓸 때는 환경변수로 가리킨다.
PKG_ROOT = Path(os.environ.get("ORTHOREG_ROOT",
                               Path(__file__).resolve().parents[2]))
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

# 치아 정합용 임계값. 대응점이 치아 개수(보통 8~20개)뿐이라 문턱도 그 규모다.
# 잔차는 **치아 간격 대비**로 본다: 무차원이라 해상도·치아 개수와 무관하고,
# `match_ransac` 의 `tol` 과 같은 단위다.
# 키 이름은 `config.thresholds.registration` 과 같게 유지한다.
DEFAULT_THRESH = dict(teeth_min_inliers=4, teeth_min_inlier_ratio=0.45,
                      teeth_max_resid_spacing=0.25)
#
# **`teeth_max_resid_spacing` 0.25 는 아직 근거가 약하다** (2026-08-11 측정, 미결).
# 전체 코퍼스 2,044쌍에서 잰 결과 — 실패 라벨은 `trim`(치아 간격비, 대응이 필요 없는
# 값)과 적합 배율의 불일치 20% 초과로 만들었다:
#
#     문턱     실패 차단   정상 통과        실패 87쌍   정상 1,330쌍
#     0.10       70%        72%           p10  0.042     0.025
#     0.15       51%        89%           중앙 0.154     0.065
#     0.20       39%        96%           p90  0.294     0.156
#     0.25       20%        99%   ← 현재
#
# 0.25 는 사실상 아무것도 안 거른다. 다만 대안도 깨끗하지 않다:
#
#   ① **분포가 겹친다.** 실패의 p10(0.042)이 정상의 p25(0.040)보다 낮다. 한 칸씩
#      밀려 짝지어지면 그 틀린 대응에 변환이 잘 맞아 잔차가 작다 — 잔차만으로는
#      원리적으로 못 가른다.
#   ② **뷰마다 분포가 다르다.** 정상 쌍의 잔차 중앙이 교합면 0.043~0.044,
#      협측 0.092~0.096, 정면 0.111 로 2~3배다. 전역 문턱은 교합면만 빡빡하게
#      건다 — `sharp`·`border_d` 를 망가뜨린 것과 같은 구조다.
#
# 다음에 손댈 때: 뷰별 문턱으로 가고, 잔차와 **독립인** 신호를 하나 더 봐야 한다.
# `trim` 과 적합 배율의 불일치가 후보다 (대응이 필요 없어 자기 채점이 아니다).

_STATE: dict = {}
_CENTER_CACHE: dict = {}
_CACHE_MAX = 64


def _weights() -> Path | None:
    """쓸 가중치 파일. `weightstore` 가 검증해 설치한 것을 쓴다."""
    if os.environ.get("ORTHOREG_WEIGHTS"):
        return Path(os.environ["ORTHOREG_WEIGHTS"])
    try:
        sys.path.insert(0, str(PKG_ROOT))
        import weightstore                                        # noqa: PLC0415
        return weightstore.scan(verify=False).path("segmentation")
    except Exception:                                             # noqa: BLE001
        return None


def _load():
    """모델을 한 번만 올린다. 서버 프로세스 수명 동안 캐시."""
    if _STATE:
        return _STATE
    from orthoreg import Segmenter                                # noqa: PLC0415
    from orthoreg import register as RG                           # noqa: PLC0415
    w = _weights()
    if w is None or not Path(w).exists():
        raise FileNotFoundError("분할 가중치가 없습니다 (models/ 에 넣어주세요)")
    _STATE.update(seg=Segmenter(w), RG=RG)
    return _STATE


def available() -> bool:
    """가중치가 있고 모델을 올릴 수 있는가. 없으면 정합을 건너뛰고 프레이밍 모델로 간다."""
    try:
        _load()
        return True
    except Exception:                                             # noqa: BLE001
        return False


def warmup() -> bool:
    """서버 기동 때 모델을 미리 올린다.

    첫 요청에서 올리면 그 요청만 수 초 느려진다. 사진 수십 장을 한 번에 분류하는
    화면이라 그 지연이 사용자에게 그대로 보인다.
    """
    return available()


def _key(img: np.ndarray, use_gate: bool):
    """배열 내용을 싸게 식별한다 — 전체 해시는 4천만 화소라 비싸다.

    모양·dtype 에 **성긴 표본**을 더한다. 같은 세션 안에서 서로 다른 사진이 이 키까지
    같을 확률은 무시할 수 있고, 충돌해도 정합 실패로 드러날 뿐 안전성 문제는 없다.
    """
    a = img[::97, ::89]
    return (img.shape, img.dtype.str, use_gate, int(a.sum()), int(a[..., 0].std() * 1e6))


def centers(img_bgr: np.ndarray, *, use_gate: bool = False) -> np.ndarray:
    """사진 한 장 → 치아 중심점 (**원본 픽셀 좌표**, N×2).

    결과를 캐시한다. `register_best` 가 기준마다 `register()` 를 부르는데, 캐시가
    없으면 **신규 사진을 기준 수만큼 다시 추론**한다 (기준 둘이면 2배).
    """
    ck = _key(img_bgr, use_gate)
    hit = _CENTER_CACHE.get(ck)
    if hit is not None:
        return hit
    C = _load()["seg"].centers(img_bgr, use_gate=use_gate)
    if len(_CENTER_CACHE) >= _CACHE_MAX:            # 세션이 길어져도 안 부풀게
        _CENTER_CACHE.pop(next(iter(_CENTER_CACHE)))
    _CENTER_CACHE[ck] = C
    return C


def _spacing(C: np.ndarray) -> float:
    """치아 간 최근접 이웃 거리의 중앙값 — 잔차를 무차원화하는 자."""
    if len(C) < 2:
        return float("nan")
    d = np.linalg.norm(C[:, None] - C[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    return float(np.median(d.min(1)))


def pseudo_frame(arr_bgr: np.ndarray, framer, label: str | None, *,
                 flip_v: bool = False):
    """신규 raw 사진의 **임시 crop** — 분할기 입력을 학습 분포로 맞춘다.

    분할기는 완성본(crop 된 그림)으로 학습됐는데 재진 신규 사진은 raw 로 들어온다.
    그대로 분할하면 치아가 프레임의 일부만 차지해 유효 해상도가 기준영상 쪽과
    비대칭이고, 매칭 배율에 해상도 비율까지 섞인다. 프레이밍 모델로 대충 잘라
    그 좌표에서 분할·매칭하고, `register` 가 마지막에 raw→pseudo 를 합성한다 —
    **이 crop 의 오차는 최종 변환에 전파되지 않는다** (그래서 pseudo 다. 예측이
    `ok=False` 여도 치열이 창 안에 들어오면 충분해 그대로 쓴다).

    패딩은 흰색 — 완성본의 흰 여백(사람이 회전·확대하며 생긴 것)과 같은 모습이라
    분할기가 보던 그림이다.

    `flip_v` 면 반환 변환의 raw 쪽 좌표계가 **상하반전된 raw**(호출부가 정합에 쓰는
    `arr_reg`)다. 프레이밍 추론 자체는 항상 무반전 원본으로 한다 — 교합면은 상하
    비대칭이 커서 뒤집어 넣으면 정확도만 조용히 떨어진다 (`_auto_frame` 과 같은 규약).

    반환: (pseudo 이미지, arr_reg→pseudo 2x3) 또는 None (모델 없음 → raw 로 분할).
    """
    if framer is None or not label or not framer.has(label):
        return None
    res = framer.predict(arr_bgr, label)
    T = np.asarray(res.matrix, np.float64)
    if res.n_models == 0 or not np.isfinite(T).all():
        return None
    cw, ch = int(res.canon_wh[0]), int(res.canon_wh[1])
    img = cv2.warpAffine(arr_bgr, T.astype(np.float32), (cw, ch),
                         flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    if flip_v:
        img = cv2.flip(img, 0)
        h = float(arr_bgr.shape[0])
        F = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, h - 1.0], [0, 0, 1]])
        Fc = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, ch - 1.0], [0, 0, 1]])
        T = (Fc @ np.vstack([T, [0, 0, 1]]) @ F)[:2, :]
    return img, T


def register(new_bgr: np.ndarray, ref_bgr: np.ndarray, *,
             thresholds: dict | None = None, tol: float = 0.7,
             use_gate: bool = True, prewarp=None) -> RegistrationResult:
    """new 를 ref 에 맞추는 닮음변환.

    `prewarp` = `pseudo_frame()` 의 반환값. 주어지면 분할·매칭·적합을 **pseudo
    좌표**에서 하고 (모듈 독스트링 "좌표계" 참조), 마지막에 raw→pseudo 를 합성한다.
    반환 `matrix` 는 언제나 new_px→ref_px 라 호출부는 안 바뀐다. `prewarp` 가 없으면
    new_bgr 를 그대로 분할한다 — 프레이밍 모델이 없는 설치본의 폴백이다.

    밝기 기반 미세정련(ECC 류)은 두지 않는다 — 변환이 이미 치아 중심점 전역
    최소제곱이고, 차수 사이에 실제로 달라지는 밝기 패턴(브라켓·와이어·타액 반사)에
    끌려갈 수 있으며, 기준 하나당 22초로 분할 추론(2.1초)보다 10배 느렸다.

    실패하면 `ok=False` 를 돌린다. 호출부(`main.py`)는 그때 **프레이밍 모델**로
    물러나고 검수 배지를 `manual` 로 둔다.
    """
    th = {**DEFAULT_THRESH, **(thresholds or {})}
    # **모델을 못 올려도 서버가 죽으면 안 된다.** 가중치가 없는 설치본에서 예외가
    # `/api/classify` 로 올라가면 분류 전체가 실패한다. 정합만 포기한다.
    try:
        S = _load()
    except Exception as e:                                        # noqa: BLE001
        return _fail(f"모델 없음: {type(e).__name__}")
    RG = S["RG"]
    try:
        if prewarp is not None:
            pimg, pM = prewarp
            P = centers(pimg, use_gate=use_gate)     # pseudo 좌표 그대로 매칭한다
        else:
            pimg, pM = None, None
            P = centers(new_bgr, use_gate=use_gate)
        Q = centers(ref_bgr, use_gate=use_gate)
    except Exception as e:                                        # noqa: BLE001
        return _fail(f"추론 실패: {type(e).__name__}")

    n_cand = int(min(len(P), len(Q)))
    if n_cand < RG.MIN_TEETH:
        return _fail(f"치아 부족({len(P)}/{len(Q)})", n_cand)

    m = RG.match_ransac(P, Q, tol=tol)
    if not m["ok"] or len(m["pairs"]) < RG.MIN_TEETH:
        return _fail("대응 실패", n_cand)
    idx = sorted(m["pairs"])
    Pm, Qm = P[idx], Q[[m["pairs"][i] for i in idx]]
    f = RG.fit_robust(Pm, Qm, min_n=RG.MIN_TEETH)

    s, deg = float(f["scale"]), float(f["deg"])
    rad = math.radians(deg)
    R = np.array([[math.cos(rad), -math.sin(rad)], [math.sin(rad), math.cos(rad)]])
    M = np.hstack([s * R, np.asarray(f["t"], np.float64).reshape(2, 1)])
    if pM is not None:
        # 적합은 pseudo→ref 였다. raw→pseudo 를 뒤에 붙여 계약(new_px→ref_px)을 지킨다.
        M = (np.vstack([M, [0, 0, 1]]) @ np.vstack([pM, [0, 0, 1]]))[:2, :]
    M = M.astype(np.float32)

    used = np.asarray(f["used"], int)
    n_in = int(len(used))
    resid = float(np.mean(f["resid_all"][used])) if n_in else float("inf")
    resid_sp = resid / max(_spacing(Qm), 1e-9)

    name = "TEETH+RANSAC"
    sx, ang, tx, ty = decompose_similarity(M)
    ratio_in = n_in / max(1, len(idx))
    ok = (n_in >= th["teeth_min_inliers"] and ratio_in >= th["teeth_min_inlier_ratio"]
          and resid_sp <= th["teeth_max_resid_spacing"])
    # **점수는 잔차만 본다.** 짝 수를 점수에 넣으면 `n_in` 이 지배하는데, 짝 수는
    # **기준 사진에 치아가 몇 개 보이나**에 좌우된다 — 정합 품질이 아니다. 확장장치가
    # 구개를 덮어 치아가 7개만 보이는 직전 차수는, 정합이 더 정확해도 18개가 보이는
    # 초진에 진다.
    #
    # 개수는 채택 문턱(`teeth_min_inliers`)으로만 쓴다. 닮음변환은 4자유도라 4점이면
    # 이미 잉여가 있고, 그 이상은 많다고 더 옳아지지 않는다.
    score = 1.0 / (1.0 + resid_sp)
    return RegistrationResult(
        ok=ok, method=name, scale=float(sx), angle_deg=float(ang),
        tx=float(tx), ty=float(ty), n_matches=n_cand, n_inliers=n_in,
        inlier_ratio=float(ratio_in), reproj_error_px=float(resid),
        score=float(score), matrix=M.tolist())


def register_best(new_bgr, refs: dict, **kw) -> tuple[str, RegistrationResult, dict]:
    """여러 기준 차수에 모두 맞춰보고 최고 점수 채택 (사양 §5.1.2).

    비교되는 것은 **같은 방법의 서로 다른 기준**이라 점수 비교가 의미를 갖는다.

    실측(50건): 직전 35 · 초진 15 채택. 차수 간격이 벌어질수록 초진 잔차만 커지고
    (간격 2에서 0.090 → 간격 10에서 0.198) 직전 채택률이 오른다. 즉 **고정하지 않아도
    점수가 직전을 고른다**. 초진을 남기는 값은 표류 방지다 — 직전만 연쇄하면 이전
    차수의 배치가 그대로 물려 내려가 되돌릴 수 없다.
    """
    all_res = {name: register(new_bgr, ref, **kw) for name, ref in refs.items()}
    best_key = max(all_res, key=lambda k: all_res[k].score)
    return best_key, all_res[best_key], all_res


def _fail(reason: str, n_matches: int = 0) -> RegistrationResult:
    return RegistrationResult(
        ok=False, method=f"TEETH({reason})", scale=1.0, angle_deg=0.0,
        tx=0.0, ty=0.0, n_matches=n_matches, n_inliers=0, inlier_ratio=0.0,
        reproj_error_px=float("inf"), score=0.0,
        matrix=[[1, 0, 0], [0, 1, 0]])
