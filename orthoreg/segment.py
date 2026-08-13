"""전경 마스크 + 중심 히트맵 + 오프셋 → **치아 인스턴스 맵**.

`train_seg.py` 에서 추론에 쓰는 것만 떼어냈다 (평가 지표 `instance_stats` 는 뺐다).
튜닝 상수의 유도 근거를 주석째로 옮겼다 — 값만 옮기면 왜 그 값인지가 사라지고,
다음 사람이 근거 없이 만진다.
"""

from __future__ import annotations

import cv2
import numpy as np

from . import instances as IN

def find_centers(heat: np.ndarray, *, thresh: float = 0.3, nms: int = 61,
                 blur: float = 3.0) -> np.ndarray:
    """히트맵의 봉우리를 중심으로 삼는다.

    **먼저 흐린다.** 예측 히트맵에는 잡음이 있고, 봉우리(σ=12, 폭 ~36px) 안에서 국소
    최대점이 여러 개 생긴다. 흐리지 않고 NMS 창 9px 로 뽑았더니 치아 21개에서 중심이
    **91개** 나왔다.

    NMS 창 61px 은 검증 28장에서 훑어 고른 값이다. 41px 은 쪼개짐이 24개, 81px 은
    붙음이 20개였고 61px 에서 붙음 10 · 쪼개짐 15 로 가장 균형이 좋았다.
    **한 장에 맞추면 안 된다** — `20_h_io_left` 한 장에서 개수가 딱 맞던 0.6·81 은
    전체에서는 잡힘 88%, 붙음 37 로 나빴다 (어둡거나 작은 치아를 통째로 놓친다).
    """
    h = cv2.GaussianBlur(heat, (0, 0), blur) if blur > 0 else heat
    mx = cv2.dilate(h, np.ones((nms, nms), np.uint8))
    ys, xs = np.nonzero((h >= mx - 1e-6) & (h >= thresh))
    if len(ys) == 0:
        return np.zeros((0, 2), np.float32)
    lab = np.zeros(h.shape, np.uint8)
    lab[ys, xs] = 1
    n, _, _, cen = cv2.connectedComponentsWithStats(lab, 8)
    return cen[1:n][:, ::-1].astype(np.float32)      # (y, x)


# 인스턴스 최소 면적(1024 기준). 라벨 1,404개에서 잰 **온전한 치아 최소가 667px**
# (프레임에 안 닿는 것만, p1 = 1,623px, 중앙값 12,386px) 이라 그 60% 다.
#
# 옛 값 100px 은 라벨 정리의 1,000px(원본 해상도)을 넓이비로 환산한 57px 에 여유를 둔
# 것이었는데, **반사 얼룩이 그대로 통과했다** — 정반사는 100px 을 쉽게 넘는다.
# 검증 28장에서 900px 까지 올려도 잡힘·붙음·쪼개짐이 하나도 안 변했으므로(402/418 ·



# 인스턴스 최소 면적(1024 기준). 라벨 1,404개에서 잰 **온전한 치아 최소가 667px**
# (프레임에 안 닿는 것만, p1 = 1,623px, 중앙값 12,386px) 이라 그 60% 다.
#
# 옛 값 100px 은 라벨 정리의 1,000px(원본 해상도)을 넓이비로 환산한 57px 에 여유를 둔
# 것이었는데, **반사 얼룩이 그대로 통과했다** — 정반사는 100px 을 쉽게 넘는다.
# 검증 28장에서 900px 까지 올려도 잡힘·붙음·쪼개짐이 하나도 안 변했으므로(402/418 ·
# 붙음 9 · 쪼개짐 11 고정) 진짜 치아가 걸릴 여지는 없다. 400 은 그 안에서 보수적인 쪽.
MIN_PX = 400


def assign_instances(fg: np.ndarray, off: np.ndarray, centers: np.ndarray,
                     *, min_px: int = MIN_PX, max_vote: float = 40.0) -> np.ndarray:
    """전경 픽셀을 오프셋만큼 옮긴 뒤 **가장 가까운 중심**에 배정한다.

    연결성을 안 보므로 이웃과 붙어 있어도 갈리고, 브라켓에 잘려 있어도 합쳐진다.

    ### `max_vote` — 투표가 빗나간 픽셀은 버린다

    `argmin` 만 쓰면 **모든 전경 픽셀이 예외 없이 어딘가에 배정된다.** 잇몸이나 입술을
    치아로 잘못 본 픽셀도 "가장 가깝다"는 이유만으로 어느 치아에 붙어, 그 치아가
    화면 반대편까지 걸친 것처럼 보인다. 폭·높이를 재는 정합에 그대로 해가 된다.

    그래서 **픽셀+오프셋이 배정된 중심에서 `max_vote` 보다 멀면 배정을 취소한다.**
    그 픽셀은 그 중심을 가리킨 게 아니라 우연히 가까웠을 뿐이다.

    연결성분으로 자르지 않는 이유: 브라켓·와이어에 갈린 **진짜 조각**까지 버리게 된다.
    투표 거리는 그 조각을 살린다 — 자기 중심을 제대로 가리키기 때문이다.
    """
    out = np.zeros(fg.shape, np.int32)
    if len(centers) == 0 or not fg.any():
        return out
    ys, xs = np.nonzero(fg)
    ty = ys + off[0][fg]
    tx = xs + off[1][fg]
    d = (ty[:, None] - centers[None, :, 0]) ** 2 + (tx[:, None] - centers[None, :, 1]) ** 2
    j = np.argmin(d, 1)
    lab = j + 1
    if max_vote > 0:
        lab = np.where(d[np.arange(len(j)), j] <= max_vote ** 2, lab, 0)
    out[fg] = lab
    for v in range(1, len(centers) + 1):          # 너무 작은 배정은 버린다
        m = out == v
        if 0 < m.sum() < min_px:
            out[m] = 0
    return out


def instances_from(sem_fg, heat, off, *, scale=None, stride=1,
                   nms=61, max_vote=0.0, max_vote_rel=0.8, min_px=MIN_PX,
                   prob=None, min_prob=0.75, min_frac=0.05):
    """중심·오프셋이 `stride` 격자에 있어도 1024² 인스턴스 맵을 만든다.

    ### 절단은 **치아 간격 대비** 로 한다 — `max_vote_rel=0.8` (2026-08-07)

    `argmin` 만 쓰면 **모든 전경 화소가 예외 없이 어딘가에 배정된다.** 시맨틱이 잇몸을
    치아로 오인하면 그 화소가 가장 가까운 치아에 붙어 무게중심을 끌고 간다. 실측
    (`3_d_io_front`): 치은 위 덩어리 3,862px(인스턴스 면적의 35 %)이 아래 앞니에 붙어
    무게중심을 43px 밀었다. 조각의 어떤 성질로도 안 걸린다 — 면적 35 %, 시맨틱 확률
    0.914, 본체와의 거리 AUC 0.542. **모델이 확신하고 틀린 것**이라 후처리 신호로는
    원리적으로 못 잡는다. 배정 단계에서 막아야 한다.

    옛 절대 절단 `max_vote=40` 은 이 일을 했지만 **뷰마다 치아 간격이 74~116px 로 달라**
    같은 값이 다르게 작동했고, GT 치아 화소의 18~31 % 를 잘라내 기각됐다. 간격 대비로
    두면 그 결함이 사라진다 (`match_ransac` 의 `tol` 과 같은 원칙).

    실측 — 라벨 200장 (검증 40장에서도 같은 구간이 최적이었다):

        max_vote_rel   GT화소 잘림   잡힘    중앙    p90    최대
        없음               2.1 %   99.9 %   0.57   2.44   43.6
        0.3                5.5 %   99.6 %   0.64   2.83   36.1
        0.45               3.4 %   99.7 %   0.57   2.19   36.5
        0.6                2.7 %   99.7 %   0.55   1.90   36.5
        **0.8**            2.3 %   99.8 %   0.55   1.99   37.2   ← 기본값
        1.2                2.1 %   99.8 %   0.56   2.28   28.7

    **p90 이 2.44 → 1.99 (−18 %)**, 최대 43.6 → 37.2. 대가는 GT 화소 잘림 2.1 → 2.3 %
    로 거의 없다. 0.6 과 p90 이 사실상 동률인데 잘림이 적어 0.8 을 쓴다 — 절대값 버전을
    죽인 실패 양식에서 더 멀다.

    `max_vote_rel=0` 이면 옛 절대 경로(`max_vote`)로 물러난다.

    ### 옛 기록 — `max_vote`(절대) 기본값이 0 이 된 경위 (2026-08-07)

    옛 기본값 40px 은 **근거가 기록되지 않은 유일한 상수**였고(`nms=61` 은 스윕,
    `MIN_PX=400` 은 유도 근거가 주석에 있다), 실측에서 가장 크게 지표를 왜곡했다:

        GT 치아 픽셀의 18~31% 를 잘라냄 (뷰별)
        중심오차 0.48% → 1.35%  (2.8배)
        모델 간 잡힘 산포 3%p → 32%p  — **없는 차이를 만들어냈다**

    같은 저장소의 `register.py::match_ransac` 은 같은 종류의 허용오차를 **치아 간격
    대비**로 두고 "절대 픽셀로 두면 뷰마다 치아 크기가 1.4배 달라 같은 값이 다르게
    작동한다"고 주석까지 달아놨다. 원칙을 알고 있었는데 여기만 절대값이었다.

    잇몸 픽셀이 먼 치아에 붙는 것을 막는다는 원래 목적은 유효하지만, 그 대가로 진짜
    치아를 18~31% 잘라내는 것이 훨씬 크다. 필요하면 `max_vote` 를 명시로 주면 된다.

    **배정을 저해상에서 끝내고 라벨맵을 최근접으로 올린다.** 오프셋 필드는 인스턴스
    안에서 정확히 선형(`v = c − x`)이라 bilinear 업샘플이 내부에서는 무손실이지만,
    **인스턴스 경계에서는 불연속**이라 보간하면 그 띠가 오염된다. 그 띠가 하필
    인접면이다. 라벨맵을 최근접으로 올리면 불연속을 안 뭉갠다.

    마스크의 경계는 1024² 시맨틱이 그대로 정한다 — 무게중심은 그 마스크의 픽셀
    평균이므로 정밀도를 잃지 않는다.
    """
    # **focal 로 학습한 히트맵은 뾰족하다.** 정점 1화소에만 양성을 주므로 봉우리가
    # 사실상 델타다 (실측: 문턱을 넘는 화소 27개 ≈ 치아 26개). 여기에 `blur=3` 을
    # 걸면 델타의 정점이 `A/(2πσ²)` = 0.86/56.5 = **0.015** 로 떨어져 문턱 0.3 을
    # 못 넘고 **중심이 0개**가 된다. blur=3 은 σ=12 시절 넓은 가우시안에 맞춘 값이다.
    # CenterNet 은 블러 없이 3×3 max-pool 로 NMS 한다.
    blur = 0.0 if stride != 1 else 3.0

    def _assign(fg, o, cen, mp):
        """절단은 **치아 간격 대비**(`assign_vote`)로 한다 — 근거는 위 주석."""
        if max_vote_rel > 0:
            lab = IN.assign_vote(fg, o, cen, max_vote_rel=max_vote_rel)
            for v in range(1, len(cen) + 1):        # 너무 작은 배정은 버린다
                m = lab == v
                if 0 < m.sum() < mp:
                    lab[m] = 0
            return lab
        return assign_instances(fg, o, cen, max_vote=max_vote, min_px=mp)

    if stride == 1:
        out = _assign(sem_fg, off, find_centers(heat, nms=nms, blur=blur), min_px)
    else:
        h, w = heat.shape
        small = cv2.resize(sem_fg.astype(np.uint8), (w, h),
                           interpolation=cv2.INTER_NEAREST).astype(bool)
        cen = find_centers(heat, nms=max(nms // stride, 3), blur=blur)
        lab = _assign(small, off, cen, max(int(min_px / stride ** 2), 1))
        up = cv2.resize(lab.astype(np.int32), sem_fg.shape[::-1],
                        interpolation=cv2.INTER_NEAREST)
        out = np.where(sem_fg, up, 0).astype(np.int32)
    # 치은 위에 앉은 조각 정리 — 근거는 `instances.prune_frag` 주석.
    # `prob`(시맨틱 확률)을 주면 그걸로 가른다 (AUC 0.796 대 면적 0.717).
    if prob is None and min_frac <= 0:
        return out
    return IN.prune_frag(out, prob, min_prob=min_prob, min_frac=min_frac)
