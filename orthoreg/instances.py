"""
시맨틱 + 중심 + 오프셋 → 치아 인스턴스. **단계를 분리해 갈아끼울 수 있게** 한다.

기존 구현(`train_seg.py`의 `find_centers` / `assign_instances`)은 세 단계가 한 덩어리로
붙어 있고 튜닝 상수가 셋(`nms=61`, `max_vote=40`, `MIN_PX=400`) 박혀 있다. 전부 검증
**28장**에서 고른 값이라 표본이 바뀌면 같이 흔들린다. 여기서는 단계를 나눠 각각을
독립적으로 비교할 수 있게 한다.

    검출  detect_*   히트맵(+오프셋) → 중심점 K개
    배정  assign_*   전경 픽셀 → 어느 중심
    정리  prune_*    쓸 수 없는 인스턴스 제거

### 무엇을 기준으로 고르나

하류(`revseg/register.py`)가 마스크에서 꺼내 쓰는 것은 **치아별 무게중심**이다. 그래서
순위는 **무게중심 오차**가 먼저이고 IoU 는 참고다. 다만 마스크가 거칠어지면 `usable()`
이 쓰는 품질 신호(`solidity`·`on_border`)가 같이 망가지므로 면적도 함께 본다.

### 왜 절대 픽셀 상수를 피하나

1024 기준 치아 간격이 뷰마다 74~116px 로 **1.6배** 다르다(라벨 200장 실측). 같은 40px
문턱이 뷰마다 다른 강도로 작동한다. `match_ransac` 은 이미 같은 이유로 허용오차를 치아
간격 대비로 두고 있다 — 여기도 그 규율을 따라 **간격 대비**를 기본으로 한다.
"""

from __future__ import annotations

import cv2
import numpy as np

# ── 검출 ────────────────────────────────────────────────────────────────────
def _peak_seeds(heat: np.ndarray, *, thresh: float, nms: int, blur: float) -> np.ndarray:
    """문턱을 넘은 국소최대 덩어리들의 중심 (기존 `find_centers` 와 같은 절차)."""
    h = cv2.GaussianBlur(heat, (0, 0), blur) if blur > 0 else heat
    mx = cv2.dilate(h, np.ones((nms, nms), np.uint8))
    ys, xs = np.nonzero((h >= mx - 1e-6) & (h >= thresh))
    if len(ys) == 0:
        return np.zeros((0, 2), np.float32)
    lab = np.zeros(h.shape, np.uint8)
    lab[ys, xs] = 1
    n, _, _, cen = cv2.connectedComponentsWithStats(lab, 8)
    return cen[1:n][:, ::-1].astype(np.float32)          # (y, x)


def detect_cc(heat, *, thresh=0.3, nms=61, blur=3.0, **_):
    """**기존 방식.** 봉우리를 이진화한 뒤 연결성분의 중심을 쓴다.

    이진화가 히트맵의 봉우리 **모양**을 버린다 — 문턱을 넘었는지만 보고 얼마나 높은지는
    안 본다. 그래서 위치가 문턱값과 격자에 양자화된다.
    """
    return _peak_seeds(heat, thresh=thresh, nms=nms, blur=blur)


def detect_softargmax(heat, *, thresh=0.3, nms=61, blur=3.0, win=18, **_):
    """봉우리 자리에서 **히트맵 가중 평균**으로 서브픽셀 보정 (soft-argmax).

    씨앗은 `detect_cc` 와 같게 잡고, 각 씨앗 주변 `win` 창에서 히트값을 가중치로 무게
    중심을 낸다. 학습도 파라미터도 필요 없고, 이진화가 버린 봉우리 모양을 되살린다.
    창은 히트맵 타깃의 σ=12 를 감싸는 크기다.
    """
    seeds = _peak_seeds(heat, thresh=thresh, nms=nms, blur=blur)
    if len(seeds) == 0:
        return seeds
    H, W = heat.shape
    out = []
    for cy, cx in seeds:
        y0, y1 = max(int(cy) - win, 0), min(int(cy) + win + 1, H)
        x0, x1 = max(int(cx) - win, 0), min(int(cx) + win + 1, W)
        p = heat[y0:y1, x0:x1].astype(np.float64)
        p = np.clip(p - thresh, 0, None)              # 문턱 아래는 무게 0
        s = p.sum()
        if s <= 1e-9:
            out.append((cy, cx))
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        out.append(((p * yy).sum() / s, (p * xx).sum() / s))
    return np.array(out, np.float32)


def detect_votes(heat, off=None, fg=None, *, stride=4, nms=61, blur=3.0,
                 thresh=0.3, **_):
    """**투표 밀도**로 중심을 찾는다 — 오프셋을 검출에도 쓴다.

    기존 구조는 오프셋을 배정에만 쓰고 검출은 히트맵에만 맡긴다. 그런데 오프셋은 전경
    픽셀 수만 개가 "내 중심은 여기"라고 말하는 **독립적인 증거**다. 그 투표를 2D 로
    누적하면 히트맵과 다른 경로로 같은 것을 추정하게 된다.

    두 증거를 곱해서 쓴다 — 히트맵이 높고 투표도 몰린 곳만 중심으로 인정한다.
    """
    if off is None or fg is None or not fg.any():
        return detect_cc(heat, thresh=thresh, nms=nms, blur=blur)
    H, W = heat.shape
    ys, xs = np.nonzero(fg)
    vy = np.clip(ys + off[0][fg], 0, H - 1).astype(np.int32) // stride
    vx = np.clip(xs + off[1][fg], 0, W - 1).astype(np.int32) // stride
    hh, ww = (H + stride - 1) // stride, (W + stride - 1) // stride
    dens = np.bincount(vy * ww + vx, minlength=hh * ww).reshape(hh, ww).astype(np.float32)
    dens = cv2.GaussianBlur(dens, (0, 0), max(blur / stride, 1.0))
    if dens.max() > 0:
        dens /= dens.max()
    dens = cv2.resize(dens, (W, H), interpolation=cv2.INTER_LINEAR)
    both = np.sqrt(np.clip(dens, 0, None) * heat.astype(np.float32))   # 기하평균
    return detect_softargmax(both, thresh=thresh * 0.6, nms=nms, blur=blur)


# ── 배정 ────────────────────────────────────────────────────────────────────
def _spacing(centers: np.ndarray) -> float:
    """중심점 간 최근접 거리의 중앙값 — 모든 길이 상수의 기준자."""
    if len(centers) < 2:
        return 100.0
    d = np.linalg.norm(centers[:, None] - centers[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    return float(np.median(d.min(1)))


def center_arch(base, arch, n_centers):
    """중심마다 자기 소속 악을 정한다 — 배정된 픽셀들의 다수결.

    중심 자체의 픽셀을 보면 안 된다. 무게중심 타깃은 브라켓 위에 떨어지는 일이 있고
    (실측 1,405개 중 73개) 브라켓은 악 라벨이 `IGNORE` 라 소속을 못 읽는다. 배정된
    픽셀 수천 개의 다수결이 훨씬 안정적이다.
    """
    out = np.zeros(n_centers + 1, np.uint8)
    for v in range(1, n_centers + 1):
        m = (base == v) & (arch > 0)
        if not m.any():
            continue
        out[v] = int(np.bincount(arch[m].ravel(), minlength=3)[1:].argmax()) + 1
    return out


def assign_vote(fg, off, centers, *, max_vote_rel=0.45, arch=None, **_):
    """전경 픽셀을 (픽셀+오프셋)이 가장 가까운 중심에 배정. **절단은 간격 대비.**

    기존 `assign_instances` 와 같은 원리인데 `max_vote` 가 절대 40px 이 아니라 **치아
    간격의 비율**이다. 40px 은 1024 기준 간격이 74~116px 로 다른 뷰들에 같은 값으로
    적용돼, 실측에서 GT 치아 픽셀의 **18~31%** 를 잘라냈다.
    """
    out = np.zeros(fg.shape, np.int32)
    if len(centers) == 0 or not fg.any():
        return out
    ys, xs = np.nonzero(fg)
    ty = ys + off[0][fg]
    tx = xs + off[1][fg]
    d = ((ty[:, None] - centers[None, :, 0]) ** 2 +
         (tx[:, None] - centers[None, :, 1]) ** 2)
    if arch is not None:
        # **악 제약** — 픽셀은 같은 악의 중심에만 갈 수 있다. 협측 뷰에서 상악·하악
        # 치아가 화면에서 맞닿아 전경이 이어지는 탓에 생기던 상하악 침범을 원천 차단한다.
        # 중심의 악은 무제약 배정의 다수결로 정한다 (닭-달걀을 한 번의 예비 배정으로 푼다).
        pre = np.zeros(fg.shape, np.int32)
        pre[fg] = np.argmin(d, 1) + 1
        ca = center_arch(pre, arch, len(centers))
        pa = arch[fg]
        bad = (pa[:, None] > 0) & (ca[None, 1:] > 0) & (pa[:, None] != ca[None, 1:])
        d = np.where(bad, np.inf, d)
    j = np.argmin(d, 1)
    lab = j + 1
    if max_vote_rel > 0:
        thr = (max_vote_rel * _spacing(centers)) ** 2
        lab = np.where(d[np.arange(len(j)), j] <= thr, lab, 0)
    out[fg] = lab
    return out


def assign_vote_grow(fg, off, centers, *, max_vote_rel=0.45, **kw):
    """투표로 **핵**을 만들고, 남은 전경을 가장 가까운 핵으로 채운다.

    투표는 "어느 치아인가"(정체성)만 정하고, "어디까지인가"(범위)는 시맨틱 마스크가
    정한다. 확장이 전경 안에 갇히므로 잇몸으로 번지지 않는다.

    실측 주의: 이 방식은 면적을 완전히 회복시키지만(0.71 → 1.02) **협측에서 무게중심을
    악화**시켰다(2.74 → 3.88%). 되찾은 픽셀이 원근으로 압축된 치아에서 비대칭이기
    때문이다. 면적·`solidity` 가 필요한 용도와 무게중심이 필요한 용도가 갈릴 수 있다.
    """
    # **실험용 함수다.** 프로덕션은 `assign_vote` 를 쓴다 (위 실측 참조). scipy 는
    # 배포 의존성에 없으므로 여기서만 늦게 끌어온다 — 이 함수를 되살리려면
    # `webapp/requirements.txt` 에 scipy 를 넣어야 한다.
    from scipy.ndimage import distance_transform_edt
    core = assign_vote(fg, off, centers, max_vote_rel=max_vote_rel, **kw)
    un = fg & (core == 0)
    if not un.any() or not (core > 0).any():
        return core
    _, (iy, ix) = distance_transform_edt(core == 0, return_indices=True)
    core[un] = core[iy[un], ix[un]]
    return core


def assign_vote_frag(fg, off, centers, *, tol_vote=0.6, tol_spatial=1.2,
                     min_frag=0.02, **_):
    """**조각 단위**로 투표를 집계해 자른다 — 픽셀 단위 절단의 대안.

    ### 왜 픽셀 단위 절단이 실패하나

    `max_vote` 는 픽셀 하나의 투표를 믿고 자른다. 그런데 픽셀 단위 투표는 잡음이 커서
    문턱을 조이면 **진짜 치아가 잘리고**(실측 GT 픽셀의 18~31% 손실 → 무게중심 오차 2배)
    풀면 **모델의 오프셋 오류가 그대로 들어온다**(실측 GT 밖 누출 3.7% → 6.9%, 상하악을
    걸치는 인스턴스 0% → 4%).

    ### 조각으로 올리면 둘 다 풀린다

        ① 절단 없이 전부 배정한다
        ② 인스턴스를 연결성분으로 조각낸다
        ③ **조각의 평균 투표**로 소속을 정하고, 말이 안 되는 조각만 버린다

    조각의 평균 투표는 픽셀 수백~수천 개의 평균이라 훨씬 안정적이다. 브라켓에 잘린 진짜
    조각은 평균 투표가 자기 중심을 정확히 가리켜 살아남고, 남의 치아 위에 얹힌 가짜
    조각은 걸러진다.

    **연결성분을 버리는 기준이 아니라 투표를 모으는 단위로 쓴다** — 원 설계가 연결성분을
    피한 이유(*"브라켓·와이어에 갈린 진짜 조각까지 버리게 된다"*)를 이렇게 우회한다.

    두 문턱 다 **치아 간격 대비**다:
      · `tol_vote`    조각의 평균 투표가 중심에서 이만큼 넘게 떨어지면 버린다
      · `tol_spatial` 조각이 중심에서 **공간적으로** 이만큼 넘게 떨어지면 버린다.
                      상하악을 걸치는 인스턴스를 직접 막는다 — 남의 악에 있는 조각은
                      자기 중심에서 1 치아간격 넘게 떨어져 있다
      · `min_frag`    중앙 치아 면적 대비 이보다 작은 조각은 판정 없이 버린다
    """
    base = assign_vote(fg, off, centers, max_vote_rel=0.0)
    if not base.any():
        return base
    sp = _spacing(centers)
    tv, ts = tol_vote * sp, tol_spatial * sp
    H, W = base.shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    vy, vx = yy + off[0], xx + off[1]

    areas = np.bincount(base.ravel())[1:]
    med = float(np.median(areas[areas > 0])) if (areas > 0).any() else 1.0
    out = np.zeros_like(base)
    for v in range(1, len(centers) + 1):
        m = base == v
        if not m.any():
            continue
        n, cc = cv2.connectedComponents(m.astype(np.uint8), 8)
        for k in range(1, n):
            f = cc == k
            a = int(f.sum())
            if a < min_frag * med:
                continue
            # 조각의 평균 투표 → 가장 가까운 중심을 고른다 (원 소속이 아닐 수 있다)
            mv = np.array([vy[f].mean(), vx[f].mean()])
            d = np.linalg.norm(centers - mv, axis=1)
            j = int(np.argmin(d))
            if d[j] > tv:
                continue
            # 공간 위생 — 조각이 그 중심에서 너무 멀면 남의 영역이다
            fc = np.array([yy[f].mean(), xx[f].mean()])
            if np.linalg.norm(centers[j] - fc) > ts:
                continue
            out[f] = j + 1
    return out


def assign_nearest(fg, off, centers, **_):
    """오프셋을 무시하고 **최근접 중심**(보로노이). 오프셋의 몫을 재는 대조군."""
    return assign_vote(fg, np.zeros_like(off), centers, max_vote_rel=0.0)


# ── 정리 ────────────────────────────────────────────────────────────────────
def prune_area(inst, *, min_rel=0.04, **_):
    """면적이 **중앙값 대비** `min_rel` 미만인 인스턴스를 버린다.

    기존 `MIN_PX=400` 은 절대 픽셀이었는데, 실측에서 GT 치아 픽셀의 **정확히 0.0%** 에만
    작용했다 — 아무 일도 안 하면서 상수 하나를 차지하고 있었다. 중앙값 대비로 두면
    뷰·해상도가 바뀌어도 뜻이 유지된다.
    """
    out = inst.copy()
    vals, cnts = np.unique(out[out > 0], return_counts=True)
    if len(vals) == 0:
        return out
    med = float(np.median(cnts))
    for v, n in zip(vals, cnts):
        if n < min_rel * med:
            out[out == v] = 0
    return out


def prune_frag(inst, prob=None, *, min_prob=0.75, min_frac=0.05, **_):
    """인스턴스 **안에서** 치아에서 떨어져 **치은 위에 앉은 조각**을 버린다.

    실측(검증 40장, vDy2): 본체에서 3반경 넘게 떨어진 조각 63개가 **100 % GT 배경
    (치은) 위**였다. 시맨틱 오탐이다. 브라켓·와이어가 끊은 **정상** 조각과 갈라야 한다.

    ### 무엇으로 가르나 — 조각 417개(가짜 231 / 진짜 186)에서 AUC

        시맨틱 확률 − 본체   0.809
        **시맨틱 확률**      0.796   ← 채택
        면적 비율            0.717
        붉기 (R−G)/(R+G)     0.657
        채도                 0.634
        **본체에서 거리**    0.542   ← 동전 던지기

    **거리로 자르면 안 된다** (AUC 0.542). 실제로 1.5반경으로 잘랐더니 치아의 절반을
    통째로 버려 중심오차 p90 이 **4.32 → 15.53 %** 로 3.6배 나빠졌다.

    **색도 면적보다 못하다** (0.657 < 0.717). "잇몸은 붉고 치아는 희다"는 직관이
    틀린 게 아니라, 모델이 이미 색을 쓰고 있어 **시맨틱 확률이 색을 더 잘 통합한
    형태**다. 조각 단위 생색은 잡음이 많다.

    작동점에서의 대가 — 가짜를 90 % 지울 때 **진짜를 몇 % 잃나**:

        시맨틱 확률   31 %
        면적          55 %
        붉기          82 %

    ### 문턱 0.75

        확률 문턱   가짜 지움   진짜 잃음   중앙    p90
        없음            0 %        0 %    0.56   4.32
        0.65           58 %       25 %    0.56   4.28
        **0.75**       87 %       30 %    0.55   3.91   ← 기본값
        0.85           98 %       44 %    0.54   3.99

    **정확도 개선이 아니라 보기 정리다** — 어느 설정에서도 중심오차 중앙이 0.54~0.56 %
    로 같다. 값은 사람이 전수 검수(A7)할 때 화면이 깨끗한 데 있다. 그래서 진짜 조각을
    44 % 나 잃는 0.85 대신 0.75 를 쓴다.

    `prob` 이 없으면 면적으로 물러난다(`min_frac`). 전체 코퍼스 750장에서 조각 면적은
    **이봉분포**다 — p50 0.4 % / p75 15 % / p90 40.6 % — 잇몸 섬과 브라켓 조각이
    각각 한 봉우리다. 검증 40장의 중앙값(~1 %)만 보고 10 % 로 잡았다가, 5~10 % 구간의
    조각 258개(3.7 %)를 근거 없이 지우고 있어 5 % 로 낮췄다.
    """
    out = inst.copy()
    for v in np.unique(out):
        if v == 0:
            continue
        m = (out == v).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        if n <= 2:
            continue
        a = st[1:, cv2.CC_STAT_AREA]
        big = 1 + int(a.argmax())
        for k in range(1, n):
            if k == big:
                continue
            c = lab == k
            drop = (float(prob[c].mean()) < min_prob if prob is not None
                    else a[k - 1] < min_frac * a.sum())
            if drop:
                out[c] = 0
    return out


def centroids(inst: np.ndarray) -> dict[int, tuple[float, float]]:
    """인스턴스별 무게중심 (x, y) — `register.py` 의 `tooth_stats` 와 같은 정의."""
    out = {}
    for v in np.unique(inst):
        if v == 0:
            continue
        ys, xs = np.nonzero(inst == v)
        out[int(v)] = (float(xs.mean()), float(ys.mean()))
    return out


DETECT = {"cc": detect_cc, "softargmax": detect_softargmax, "votes": detect_votes}
ASSIGN = {"vote": assign_vote, "vote_grow": assign_vote_grow,
          "frag": assign_vote_frag, "nearest": assign_nearest}


# ── 반경 기반 적응 (stride 4 인스턴스 가지와 함께 쓴다) ─────────────────────
def detect_radius(heat, scale=None, *, thresh=0.3, blur=1.0, nms_frac=0.9,
                  nms_min=3, **_):
    """**예측 반경으로 NMS 창을 화소마다 정한다.**

    고정 `nms=61px` 은 한 사진 안에서 전치부와 대구치의 간격이 2배 넘게 다른 것을
    무시한다 — 전치부에서 붙고 구치부에서 갈린다. 반경을 예측하면 그 상수가 사라진다.

        ① 작은 창으로 후보 봉우리를 넉넉히 뽑는다
        ② 점수 높은 순으로, 그 봉우리의 **예측 반경 × nms_frac** 안의 후보를 억제한다

    `scale` 이 없으면 고정 창으로 물러난다 (옛 동작).
    """
    h = cv2.GaussianBlur(heat, (0, 0), blur) if blur > 0 else heat
    k = max(int(nms_min) * 2 + 1, 3)
    mx = cv2.dilate(h, np.ones((k, k), np.uint8))
    ys, xs = np.nonzero((h >= mx - 1e-6) & (h >= thresh))
    if len(ys) == 0:
        return np.zeros((0, 2), np.float32)
    sc = h[ys, xs]
    order = np.argsort(-sc)
    ys, xs = ys[order], xs[order]
    rad = (np.exp(scale[ys, xs]) if scale is not None
           else np.full(len(ys), 30.0, np.float32))
    keep = []
    taken = np.zeros(len(ys), bool)
    for i in range(len(ys)):
        if taken[i]:
            continue
        keep.append(i)
        r = max(rad[i] * nms_frac, nms_min)
        d = np.hypot(ys - ys[i], xs - xs[i])
        taken |= d < r
    return np.stack([ys[keep], xs[keep]], 1).astype(np.float32)


def assign_radius(fg, off, centers, scale=None, *, vote_frac=0.0,
                  min_area_frac=0.04, **_):
    """배정·정리의 길이 상수를 **중심마다의 예측 반경**에 묶는다.

    `vote_frac=0` 이면 절단 없음 — 실측에서 절단이 GT 치아 픽셀의 18~31% 를 잘라내
    무게중심 오차를 2배로 만들었기 때문에 기본을 무절단으로 둔다. 필요하면 반경 대비로만
    켠다 (절대 픽셀 문턱은 뷰마다 다르게 작동한다).
    """
    out = np.zeros(fg.shape, np.int32)
    if len(centers) == 0 or not fg.any():
        return out
    ys, xs = np.nonzero(fg)
    d = ((ys + off[0][fg])[:, None] - centers[None, :, 0]) ** 2 + \
        ((xs + off[1][fg])[:, None] - centers[None, :, 1]) ** 2
    j = np.argmin(d, 1)
    lab = j + 1
    if vote_frac > 0:
        r = (np.exp(scale[centers[:, 0].astype(int), centers[:, 1].astype(int)])
             if scale is not None else np.full(len(centers), 30.0))
        thr = (vote_frac * r[j]) ** 2
        lab = np.where(d[np.arange(len(j)), j] <= thr, lab, 0)
    out[fg] = lab
    if min_area_frac > 0 and scale is not None:
        for v in range(1, len(centers) + 1):
            m = out == v
            a = int(m.sum())
            if a == 0:
                continue
            cy, cx = centers[v - 1].astype(int)
            if a < min_area_frac * np.pi * float(np.exp(scale[cy, cx])) ** 2:
                out[m] = 0
    return out


DETECT["radius"] = detect_radius
ASSIGN["radius"] = assign_radius
