"""
회차 정합 — 치아 중심점으로 닮음변환을 맞춘다.

**학습하지 않는다.** 대응점이 주어지면 변환은 닫힌 해로 풀린다. 회차 쌍이 10쌍뿐이라
배울 데이터도 없고, 분리해두면 정합 규칙을 바꿔도 분할기를 재학습할 필요가 없다.

### 왜 닮음변환인가

결과물이 **회전된 사각형 crop** 이다. 닮음변환은 사각형을 사각형으로 보내지만
호모그래피는 사다리꼴로 보낸다. 실측에서 호모그래피가 잔차를 절반으로 줄였지만
(0.99% → 0.49%), 그걸 다시 직사각형으로 근사하면 이득이 사라진다.

사람이 준 변환도 실측상 **배율 1.000, 회전+평행이동뿐**이었다 — 잘라내고 돌릴 뿐
확대축소를 하지 않는다. 원근은 실재하지만 crop 으로는 보정할 수 없는 종류다.

### 왜 치아 중심점으로 한 번에 맞추나

치아마다 크기 비(면적√·폭·높이)를 재서 중앙값을 쓰는 방법을 먼저 시도했는데,
**치아 간 산포가 6~10%** 였다. 폭은 양 끝이 인접면이라 이웃 위치에 좌우되고,
높이는 치은 변화에, 면적은 와이어 가림에 흔들린다.

중심점 12~20개로 변환 하나를 함께 제약하면 실측 잔차가 **0.99%** 다. 훨씬 안정적이다.

### 못 재는 치아와 움직인 치아를 갈라야 한다

    선별      crop 경계에 잘림 · 마스크가 부서짐 · 한쪽에만 있음
              → **미리** 안다. 빼고 시작한다
    로버스트   교정으로 실제 움직인 치아
              → **미리 모른다.** 맞춰 봐야 안다. 잔차로 걸러낸다

움직인 치아를 미리 빼려 하면 원하는 답이 나오도록 데이터를 고르는 셈이 된다.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

MIN_TEETH = 2          # 이보다 적으면 정합을 포기한다
#
# **2점이면 닮음변환이 정확히 결정된다** — 4자유도에 방정식 4개라 잉여가 0 이다.
# 그래서 `fit_robust` 가 아무것도 못 자르고, 두 중심점 중 하나만 틀려도 배율이 그대로
# 틀어진다. 배율 오차는 두 치아 사이 거리에 반비례하므로 인접한 둘이 걸리면 특히 나쁘다.
# 4 → 2 로 연 것은 "못 재겠다"보다 "재고 사람이 검수한다"를 택한 결정이다 (A7).
#
# 실측 (49쌍, 기준은 ① GT마스크+FDI 의 배율, 2026-08-08):
#
#     MIN_TEETH   성공쌍   중앙    p75    5%초과   최대
#     4            48     0.56   1.52      5     38.9
#     2            49     0.54   1.49      5     38.9
#
# 새로 들어온 쌍은 `76_de_IO_UPPER` 하나이고 **짝 3개로 배율오차 0.4%** — 중앙값보다
# 정확했다. 기존 쌍이 살짝 좋아진 것은 `MIN_TEETH` 가 `fit_robust` 의 절단 하한
# (`min_n`)도 겸하기 때문이다. `min_n=2` 면 이상치를 더 깊게 버릴 수 있다.
#
# **근거가 1쌍뿐이다.** 그것도 짝 3개이고 `IO_UPPER` 는 비등방 1.80%·크기 산포 2.48%
# 로 가장 조건이 좋은 뷰다. 2점 적합이 일반적으로 안전하다는 증거는 아니다 — 협측에서
# 치아가 2~3개만 보이는 경우는 이 데이터에 없어 검증되지 않았다.
#
# 라벨 49쌍에서는 4 가 애초에 제약이 아니었다 (모두 짝을 4개 이상 만든다). 문턱이
# 실제로 작동하는 곳은 **가철식 확장 장치 착용 회차**다 — 장치가 구개를 덮어
# `IO_UPPER` 에서 치아가 3~5개만 보인다 (전체 코퍼스 `IO_UPPER` 의 p10 이 7개).

# 품질 신호의 문턱. **학습셋 160장(치아 2,010개)에서 뽑고 검증 40장(536개)에서 확인**했다.
#
# 잡는 방법: 신호 값으로 정렬해 이동창(치아 168개)의 **중심오차 중앙값**이 전체 중앙값의
# 2배(1.16%)를 넘는 경계를 문턱으로 삼는다. 누적이 아니라 **국소** 관계를 봐야 한다 —
# "이 문턱을 넘긴 치아 전체의 중앙오차"로 잡으면 전체 중앙이 이미 0.56%라 어떤 문턱도
# 조건을 만족해서 문턱이 분포 밖으로 (solidity 0.46 < p10 0.58) 날아간다. 실제로 그렇게
# 잡았다가 검증셋 100%가 통과하는 무의미한 필터가 나왔다.
TAU = {
    "solidity":  0.526,     # 걸리는 치아  7%
    "semantic":  0.942,     #              8%
    "peak":      0.971,     #             25%
    "vote_sd":  19.709,     #             15%   (원본 해상도 픽셀)
    "vote_bias": 1.050,     #             14%   (치열 반경 대비 %)
    "frag_area": 0.003,     #             31%
}
HI = ("solidity", "semantic", "peak")       # 클수록 좋음
LO = ("vote_sd", "vote_bias", "frag_area")  # 작을수록 좋음

# **재현성 문턱** — 두 회차에서 이만큼 달라진 치아는 짝에서 뺀다 (2026-08-10).
#
# 정합이 쓰는 것은 한 장 안의 정확도가 아니라 **두 회차 사이의 변위**다. 중심점이
# 부정확해도 두 회차에서 같은 방식으로 부정확하면 상쇄된다. 그래서 목표를
# **오라클 대응·오라클 적합에서의 잔차**로 놓고 다시 쟀다 (치아 짝 472개).
#
#                    사진 단위 ρ    |Δ| ρ
#     area_rel          0.012      0.387
#     border_d          0.220      0.286
#     peak             -0.231      0.272
#     solidity         -0.186      0.240
#     vote_sd          -0.043     -0.002    ← 옛 1위(중심오차 0.62)가 사라졌다
#
# **사진 단위 신호는 전부 무력하다** (최대 |ρ|=0.21). 재현성은 한 장에서 안 보인다.
# `vote_sd` 가 0 이 된 것이 결정적이다 — 한 장 안의 불확실성은 두 회차에 같이 나타나
# 변위에서 상쇄된다.
#
# `arch_pos` 가 |Δ| 0.471 로 1위였지만 **뺐다.** 닮음 불변 좌표라 그 변화가 곧 잔차의
# 반경 성분이다 — 잔차로 잔차를 예측하는 것이고, 그건 `fit_robust` 가 이미 한다.
#
# 넷의 **최대 백분위**로 결합하면 ρ=0.496 (평균 결합 0.473, `area_rel` 단독 0.387).
# 아래 값은 결합 백분위 0.975 = **제거 10%** 지점의 절대 컷오프다:
#
#     제거    버린 치아 잔차   남는 치아   배수
#      5%        12.81%       4.29%    2.98
#     10%        10.21%       4.14%    2.46   ← 채택
#     20%         8.75%       3.96%    2.21
#
# 첫 구현은 **쌍 안에서 상대 문턱**(3×중앙값)을 썼다가 실패했다 — 뽑히는 것이 "그 쌍에서
# 가장 큰 치아"라 전역적으로는 평범했고, 버린 치아가 남는 치아보다 **1.12배** 나쁜 데
# 그쳐 지표가 하나도 안 움직였다. 전역 컷오프여야 한다.
DELTA_TAU = {
    "area_rel": 1.315,     # 면적/중앙 의 회차 간 차이
    "border_d": 0.670,     # 프레임까지 거리(치열 반경 대비)의 차이
    "peak":     0.302,     # 히트맵 정점의 차이
    "solidity": 0.192,     # 볼록껍질 대비 면적의 차이
}


def tooth_stats(inst: np.ndarray, *, prob=None, heat=None, off=None,
                border: int = 2) -> dict[int, dict]:
    """인스턴스 맵 → 치아마다 중심과 **품질 신호**.

    `prob`(시맨틱 확률)·`heat`(중심 히트맵)·`off`(오프셋)를 주면 모델의 확신도까지
    함께 낸다. 라벨로 돌릴 때는 안 줘도 된다.
    """
    H, W = inst.shape
    yy, xx = np.mgrid[0:H, 0:W]
    out: dict[int, dict] = {}
    for v in np.unique(inst):
        if v == 0:
            continue
        m = inst == v
        ys, xs = np.nonzero(m)
        a = int(m.sum())
        hull = cv2.convexHull(np.stack([xs, ys], 1))
        n_cc, _, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        ca = st[1:, cv2.CC_STAT_AREA]
        d = {
            "center": (float(xs.mean()), float(ys.mean())),
            "area": a,
            "on_border": bool(xs.min() < border or ys.min() < border or
                              xs.max() > W - 1 - border or ys.max() > H - 1 - border),
            # 볼록껍질 대비 면적. 엉뚱한 픽셀이 섞이면 바로 떨어진다.
            # 중심오차 상관 -0.61 — 6개 신호 중 2위.
            "solidity": float(a / max(cv2.contourArea(hull), 1.0)),
            # 본체 밖 면적 비율. 상관 +0.49 로 **조각 수(+0.46)보다 강하다.**
            "frag_area": float(1.0 - ca.max() / ca.sum()),
            "n_cc": int(n_cc - 1),
        }
        if prob is not None:
            d["semantic"] = float(prob[m].mean())          # 상관 -0.56
        if heat is not None:
            d["peak"] = float(heat[m].max())               # 상관 -0.42
        if off is not None:
            # 픽셀들의 (위치+오프셋)이 얼마나 한 점에 모이나. 모델이 낸 값들끼리의
            # 일치도라 **정답 없이 잴 수 있다.** 상관 +0.62 로 가장 강하다.
            vy = (yy + off[0])[m]
            vx = (xx + off[1])[m]
            d["vote_sd"] = float(np.sqrt(vy.var() + vx.var()))
            # 투표가 모이는 곳과 무게중심이 **어긋난 거리.** 산포와 달리 방향이 있는
            # 오차를 잡는다 — 마스크에 딴 것이 붙어 무게중심이 끌려가면 여기서 걸린다.
            # 상관 +0.59. `usable()` 이 치열 반경으로 나눠 쓴다.
            d["vote_bias"] = float(np.hypot(vy.mean() - ys.mean(), vx.mean() - xs.mean()))
        out[int(v)] = d
    return out


def quality(s: dict, *, radius: float) -> float:
    """0~1 품질 점수 — **중심오차**를 예측하도록 고른 신호들의 평균.

    예전에는 IoU 와의 상관으로 신호를 골랐는데, 정합이 쓰는 것은 마스크가 아니라
    **무게중심**이라 목표가 틀렸었다. 중심오차로 다시 재니 순위가 뒤집혔다 —
    `area`(면적비)는 0.22 로 밀려 빠지고, `vote_sd` 가 0.62 로 1위가 됐다.

    **조각 비율은 되살렸다.** 예전 주석은 "브라켓에 갈린 정상 치아를 탈락시키기만
    한다"며 뺐는데, 층화해 보니 그 전제가 틀렸다. GT 자체가 2조각인 치아(전체의 16%)는
    중심오차 중앙이 **1.60%로 1조각 치아의 0.50% 보다 3배 나쁘다.** 조각난 것이 정상
    라벨이든 아니든 그 치아의 중심점은 실제로 못 믿는다. `quality()` 는 "예측이 틀렸나"가
    아니라 **"이 중심점을 믿어도 되나"** 를 재는 것이므로 감점이 맞다.

    조각 수(`n_cc`)가 아니라 `frag_area` 를 쓰는 이유는 GT 조각 수도 중심오차와
    상관되기 때문이다(+0.37) — 정수 개수는 치아의 난이도까지 같이 세는데, 면적 비율은
    본체에서 떨어져 나간 정도를 잰다. 실측 상관도 +0.49 대 +0.46 으로 낫다.
    """
    f = [min(s[k] / TAU[k], 1.0) for k in HI if k in s]
    for k in LO:
        if k not in s:
            continue
        x = s[k] / radius * 100 if k == "vote_bias" else s[k]
        f.append(min(TAU[k] / max(x, 1e-6), 1.0))
    return float(np.mean(f)) if f else 0.0


def _cut_frac(m, k3):
    """마스크 둘레 중 화면 경계에 놓인 몫. 바깥을 **배경으로 패딩**하고 잰다."""
    q = np.pad(m, 1)
    bd = (q - cv2.erode(q, k3)) > 0
    fr = np.zeros_like(bd)
    fr[1] = fr[-2] = True
    fr[:, 1] = fr[:, -2] = True
    return float((bd & fr).sum() / max(bd.sum(), 1))


def tooth_signals(inst, *, fg_prob=None, rad=None, edge: int = 3):
    """`usable` 이 쓰는 신호 — 마스크가 **온전한 치아 하나**인가.

    `tooth_stats` 와 달리 **사진 전체의 맥락**이 필요하다 (치열 중심·면적 중앙값).

    `fg_prob` 는 현재 쓰이지 않는다 (`sharp`/누출 기각, 아래 참조). 호출부를 안 깨려고
    남겨두었다.
    """
    st = tooth_stats(inst)
    ks = sorted(st)
    if not ks:
        return {}
    med = float(np.median([st[k]["area"] for k in ks]))
    C = np.array([st[k]["center"] for k in ks])
    cen = C.mean(0)
    arad = float(np.sqrt(((C - cen) ** 2).sum(1).mean())) if len(C) > 1 else 1.0
    k3 = np.ones((3, 3), np.uint8)
    out = {}
    for k in ks:
        m = (inst == k).astype(np.uint8)
        x, y = st[k]["center"]
        out[int(k)] = dict(
            area_rel=st[k]["area"] / max(med, 1),
            solidity=st[k]["solidity"],
            arch_pos=float(np.hypot(x - cen[0], y - cen[1])) / max(arad, 1e-6),
            # **프레임 절단** — 둘레 중 화면 경계에 놓인 몫.
            #
            # 처음엔 중심점의 가장자리 거리를 치열 반경으로 나눈 `border_d` 를 썼는데,
            # 그건 "치열이 화면을 얼마나 채우나"를 쟀다. 교합면은 치열이 화면을 꽉
            # 채워 값이 통째로 내려앉아, 전역 p10 문턱이 **절반을 잘랐다** (IO_UPPER
            # 34% 배제, 실제로 닿은 것은 4%).
            #
            # 다음엔 불리언(닿나/안 닿나)으로 갔는데 과잉이었다 — 걸린 치아의 23% 가
            # 둘레의 2% 미만만 닿았다. `11_c_io_lower` id12 는 둘레 888px 중 68px
            # (0.077) 인데 배제됐고, 마스크는 이웃 구치와 품질이 같았다.
            #
            # 비율로 재면 접선(스치기)과 절단이 갈린다. 같은 사진에서
            # id12 0.077 · id13 0.093 · **id14 0.150**(실제로 잘림) 이다.
            # `np.pad` 로 바깥을 배경으로 채워야 한다 — `cv2.erode` 는 이미지 바깥을
            # 전경으로 취급해서, 안 하면 프레임에 붙은 화소가 둘레에서 빠진다(0.002).
            #
            # 이름을 `cut` → `cut_frac` 으로 바꿨다. 불리언을 쓰던 호출자가 float 를
            # 받아 **조용히 항상 True 가 되지 않게** 한다.
            cut_frac=_cut_frac(m, k3),
            center=st[k]["center"])
    return out


# **usable 문턱** — 마스크 온전성.
#
# 정의: `usable` 은 "GT 치아를 최대한 맞추는 것"이 아니라 **대응 단계에서 오류를 낼
# 마스크를 걸러내는 것**이다. 도메인 기준에서 나왔다:
#
#     정면 양끝 구치      원근으로 작아 보임 — 단축량이 머리 각도에 좌우돼 회차마다 다름
#     측면 리트랙터 가림   마스크가 잘림
#     측면 반대 치열       원근 단축이 극단적
#     교합면 경계 불명확   마스크 경계가 어디인지 모름
#
# 남은 신호 셋:
#
#     area_rel   면적 / 그 사진 치아 면적의 중앙값.  **`arch_pos` 와 AND 로만 건다** —
#                측절치는 원래 작다. 원근 문제인 것은 "작으면서 치열궁 끝에 있는"
#                치아다. OR 로 걸었더니 `34_c_io_left` 에서 16개 중 16개가 배제됐다
#     arch_pos   치열 무게중심에서의 거리 / 치열 반경
#     solidity   볼록껍질 대비 면적.  **결손(1−solidity)의 사진 중앙 대비 차이**로 건다
#     cut        마스크가 화면 경계에 닿는가 (불리언)
USABLE_TAU = {
    "area_rel": 0.50,      # 이 미만 **이면서**
    "arch_pos": 1.13,      # 이 초과일 때만 배제
    "sol_gap":  0.12,      # 결손(1−solidity) > 사진 중앙 결손 + 이 값
    "cut_frac": 0.10,      # 둘레 중 프레임 경계에 놓인 몫
}   # `cut` 은 불리언이라 문턱이 없다
#
# **형태는 비율이 아니라 차이다** — 누출과 반대다. 갈리는 이유는 그 양이 0 근처에
# 사는가이다 (라벨 199장 · 치아 2,555개):
#
#     양       사진 중앙값의 범위        비율이 성립하나
#     누출     0.125~0.193  (1.5배)       ✓  바닥에서 떨어져 있다
#     결손     0.016~0.111  (7배)         ✗  0 에 붙는 사진이 있다
#
# 결손 중앙이 0.016 인 깨끗한 사진에서 결손 0.04(=solidity 0.96, 훌륭한 값)가
# **2.5배**가 되어 걸린다. 반대로 사진 전체가 거친 `34_c`(중앙 0.123)에서는 비율이
# 하나도 안 걸린다 — 다 같이 나쁘면 튀는 게 없다. 차이는 양쪽을 다 막는다:
#
#     사진                중앙 결손   절대<0.82   비율>2.5x   차이>0.12
#     32_d_io_lower_vf     0.016         0          2          0
#     64_c_io_lower_vf     0.025         0          3          0
#     34_c_io_left         0.123         4          0          2
#     52_b_io_left         0.050         4          4          4
#
# 형태의 사진 간 변동계수는 0.571 로 누출(0.154)의 네 배다 — 전역 문턱이 더 심하게
# 어긋나 있었다. 전체 배제율은 9.4% → 7.9% 로 비슷하고 분포만 바뀐다.
#
# **기각: `sharp` / 누출** (2026-08-11).  마스크 경계가 뚜렷한가를 재려던 신호다.
# 바깥 2px 띠의 전경확률로 정의했는데, **그 띠에 깨끗한 배경이 아닌 것이 계속 섞였다.**
# 네 번 고쳤고 매번 "변별력의 근거"가 다른 것을 재고 있었다:
#
#     ① 확률 채널이 치아만       → 브라켓 면적을 쟀다
#                                 `41_de` 회차 e: 장치 75% 치아 0.05 · 19% 치아 0.33.
#                                 16개 중 11개가 걸렸고 마스크는 멀쩡했다
#     ② 띠가 이웃 인스턴스 위     → 총생을 쟀다
#                                 이웃 접촉 50% 이상인 치아의 **73%** 가 걸렸다
#     ③ 띠가 미배정 전경 위       → 인접 치아 이음매를 쟀다
#                                 `4_a` 상악 중절치: 띠의 61% 가 이음매(전경확률 0.883),
#                                 그 사진에서 가장 크고 깨끗한 치아가 최하위가 됐다
#     ④ 띠가 장치 근처            → 와이어 번짐을 쟀다
#                                 장치가 마스크의 35% 이상인 치아 203개의 배제율이
#                                 19.7% 로 기준선(6.6%)의 3배. 그 미만은 영향 없음
#
# 전역 문턱은 사진마다 분포가 이동해 못 쓰고(사진 중앙 0.125~0.193), 사진 중앙 대비
# 비율로 바꿔도 ④가 남는다. 네 번 고치는 동안 **대응이 좋아진다는 증거는 한 번도
# 나오지 않았다** — 전체 코퍼스 파국 수는 usable 없이 48, 있으면 51 이었다.
# 되살리려면 "이 마스크의 무게중심을 믿어도 되나"를 직접 재는 다른 정의가 필요하다.
#
# `border_d` 도 기각했다 — 프레임까지 거리를 **치열 반경**으로 나눠, 교합면처럼 치열이
# 화면을 채우는 뷰에서 분포 한가운데가 잘렸다 (전역 p10 문턱이 IO_UPPER 의 34% 를
# 걸었는데 실제로 경계에 닿은 것은 4%). `cut`(경계 접촉, 불리언)으로 대체했다.
USABLE_TAU_ALT = {
    "A": {"area_rel": 0.33, "arch_pos": 1.37, "sol_gap": 0.16, "cut_frac": 0.15},
    "C": {"area_rel": 0.67, "arch_pos": 0.80, "sol_gap": 0.08, "cut_frac": 0.06},
}


def usable(stats: dict[int, dict], *, tau: dict | None = None,
           min_quality: float | None = None) -> set[int]:
    """대응에 넣어도 되는 치아 — **마스크 온전성** 기준.

    근거와 문턱은 `USABLE_TAU` 주석에 있다. `stats` 는 `tooth_signals` 의 출력이어야
    한다 (`tooth_stats` 만으로는 사진 맥락이 없어 판정할 수 없다).

    `min_quality` 는 옛 인자다 — 0 이나 None 이면 문턱을 적용하지 않는다.
    """
    if not stats:
        return set()
    if min_quality is not None and min_quality <= 0:
        return {int(v) for v in stats}
    t = tau or USABLE_TAU
    # 형태는 **그 사진 안에서** 상대적으로 본다 — 결손 중앙이 사진마다 0.016~0.111 로 7배 다르다
    smed = float(np.median([1.0 - s["solidity"] for s in stats.values()]))
    out = set()
    for v, s in stats.items():
        small_end = s["area_rel"] < t["area_rel"] and s["arch_pos"] > t["arch_pos"]
        ragged = (1.0 - s["solidity"]) > smed + t.get("sol_gap", np.inf)
        if not (small_end or s["cut_frac"] > t.get("cut_frac", np.inf) or ragged):
            out.add(int(v))
    return out


def umeyama(P: np.ndarray, Q: np.ndarray) -> tuple[float, float, np.ndarray]:
    """P → Q 닮음변환의 닫힌 해. 반환 (배율, 회전각[rad], 평행이동)."""
    mp, mq = P.mean(0), Q.mean(0)
    A, B = P - mp, Q - mq
    U, S, Vt = np.linalg.svd(B.T @ A / len(P))
    D = np.eye(2)
    if np.linalg.det(U @ Vt) < 0:
        D[1, 1] = -1
    R = U @ D @ Vt
    var = (A ** 2).sum() / len(P)
    s = float(np.trace(np.diag(S) @ D) / var) if var > 0 else 1.0
    t = mq - s * (R @ mp)
    return s, float(math.atan2(R[1, 0], R[0, 0])), t


def fit_robust(P: np.ndarray, Q: np.ndarray, *, k_mad: float = 3.0,
               iters: int = 3, min_n: int = MIN_TEETH,
               min_keep: float = 0.6) -> dict:
    """잔차가 큰 점을 덜어내며 다시 맞춘다 — **움직인 치아**가 여기서 걸러진다.

    **비율이 아니라 잔차 크기로 자른다.** 처음엔 매번 20%씩 5번 깎았는데, 그러면
    이상값이 몇 개든 상관없이 무조건 3분의 2가 사라진다. 실측에서 치아 20개 중
    5개만 남아 4자유도를 4~5점으로 맞추는 과적합이 됐고, 배율 산포가 1.6%였다.

    지금은 잔차가 **중앙값의 `k_mad` 배**를 넘는 점만 버리고, 아무리 버려도
    `min_keep` 아래로는 안 내려간다. 이상값이 없으면 아무것도 안 버린다.
    """
    n0 = len(P)
    idx = np.arange(n0)
    s, th, t = umeyama(P, Q)
    for _ in range(iters):
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        r = np.linalg.norm(s * (P[idx] @ R.T) + t - Q[idx], axis=1)
        med = float(np.median(r))
        if med <= 1e-9:
            break
        keep = r <= k_mad * med
        k = int(keep.sum())
        if k == len(idx) or k < max(min_n, int(n0 * min_keep)):
            break
        idx = idx[keep]
        s, th, t = umeyama(P[idx], Q[idx])
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    res = np.linalg.norm(s * (P @ R.T) + t - Q, axis=1)
    return {"scale": s, "deg": math.degrees(th), "t": t, "used": idx,
            "resid": float(np.median(res[idx])), "resid_all": res}


def fit_homography_similarity(P: np.ndarray, Q: np.ndarray) -> dict | None:
    """원근을 흡수한 뒤 **닮음변환 성분만** 꺼낸다.

    치아는 대략 한 평면(치열궁) 위에 있어서, 카메라 각도가 달라지면 투영이
    호모그래피만큼 변한다. 닮음변환은 그걸 표현 못 해 협측 뷰에서 잔차가 남는다
    (실측 IO_RIGHT 닮음 1.94% → 호모 0.52%).

    그런데 **결과물이 crop 직사각형**이라 호모그래피를 그대로 못 쓴다 — 직사각형이
    사다리꼴로 간다. 그래서 이렇게 한다:

        ① 호모그래피 H 를 맞춘다              원근까지 설명
        ② 치열 중심 c 에서 야코비안 J 를 구한다  그 자리의 국소 선형 근사
        ③ J 에 가장 가까운 닮음변환을 꺼낸다     극분해 (SVD)

    즉 원근은 **더 나은 국소 추정을 얻는 데만** 쓰고, crop 에는 닮음변환 성분만 쓴다.
    """
    if len(P) < 4:
        return None
    H, _ = cv2.findHomography(P.astype(np.float32), Q.astype(np.float32), cv2.LMEDS)
    if H is None:
        return None
    c = P.mean(0)
    w = H[2, 0] * c[0] + H[2, 1] * c[1] + H[2, 2]
    if abs(w) < 1e-9:
        return None
    hc = np.array([(H[0, 0] * c[0] + H[0, 1] * c[1] + H[0, 2]) / w,
                   (H[1, 0] * c[0] + H[1, 1] * c[1] + H[1, 2]) / w])
    # 야코비안: d/dx [ (a·x)/(w·x) ] = (A - hc ⊗ h3) / w
    J = (H[:2, :2] - np.outer(hc, H[2, :2])) / w
    U, S, Vt = np.linalg.svd(J)
    D = np.eye(2)
    if np.linalg.det(U @ Vt) < 0:
        D[1, 1] = -1
    R = U @ D @ Vt
    s = float((S[0] + S[1]) / 2)                 # J 에 가장 가까운 배율
    t = hc - s * (R @ c)
    return {"scale": s, "deg": math.degrees(math.atan2(R[1, 0], R[0, 0])), "t": t,
            "used": np.arange(len(P)),
            "resid": float(np.median(np.linalg.norm(
                s * (P @ R.T) + t - Q, axis=1)))}


def apply(pts: np.ndarray, fit: dict) -> np.ndarray:
    th = math.radians(fit["deg"])
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return fit["scale"] * (np.asarray(pts, float) @ R.T) + fit["t"]


# 두 악이 함께 보이는 뷰. 교합면은 한 악뿐이라 악 제약을 걸면 안 된다.
TWO_ARCH_VIEWS = ("IO_FRONT", "IO_LEFT", "IO_RIGHT")

# 정중선을 쓸 수 있는 뷰. **협측은 한쪽만 보여 대칭축이 없다** — 실측 좌우 분리
# 정확도가 IO_LEFT 69% · IO_RIGHT 63% 로, 쓰면 오히려 해롭다.
#
#     IO_FRONT  99%      IO_UPPER 90%      IO_LOWER 82%
#     IO_LEFT   69%      IO_RIGHT 63%   ← 제외
#
# 대칭 비용으로는 자동 감지가 안 된다 (0.201~0.249 로 뷰별 차이가 거의 없다).
# `arch_labels` 와 같이 **호출자가 뷰로 걸러야 한다.**
MIDLINE_VIEWS = ("IO_FRONT", "IO_UPPER", "IO_LOWER")


def midline(P: np.ndarray, *, n_grid: int = 400):
    """치열의 **좌우 대칭축** 위의 기준점. 정중선이 없는 뷰에서는 쓰면 안 된다.

    치열은 정중선에 대해 좌우 대칭이다. 주축 `u` 에 사영한 1차원 좌표를 `c` 에 대해
    반사했을 때 원래 집합과 가장 잘 겹치는 `c` 가 정중선이다 — 1차원 탐색이라 안정적이고,
    두 악이 같이 있어도 정중선은 공유하므로 한 번에 구해진다.

    비용을 **절단**하는 것이 핵심이다. 한쪽에만 보이는 치아(프레임 밖·발치)는 반사해도
    짝이 없는데, 절단이 없으면 그런 치아가 비용을 지배해 축이 밀린다.

    반환은 축 위의 한 점(치열 무게중심을 축에 사영한 것)이다. **대응점 하나**로 쓰기
    위해서다 — `match_ransac` 이 이것을 씨앗으로 삼으면 2점 중 1점이 공짜가 된다.
    """
    if len(P) < 4:
        return None
    mu = P.mean(0)
    C = P - mu
    u = np.linalg.svd(C, full_matrices=False)[2][0]
    t = C @ u
    sp = float(np.median(np.diff(np.sort(t)))) or 1e-6
    best = (np.inf, 0.0)
    for c in np.linspace(np.percentile(t, 20), np.percentile(t, 80), n_grid):
        d = np.abs((2 * c - t)[:, None] - t[None, :]).min(1)
        cost = float(np.mean(np.minimum(d / sp, 1.0)))
        if cost < best[0]:
            best = (cost, float(c))
    return mu + best[1] * u


def arch_labels(P: np.ndarray, *, min_sep: float = 1.6):
    """중심점을 **두 악(위·아래 줄)** 으로 가른다. 한 줄뿐이면 `None`.

    **호출자가 뷰로 걸러야 한다** (`TWO_ARCH_VIEWS`). 기하만으로는 교합면을 못 가른다 —
    실측에서 `IO_LOWER` 16장 **전부**, `IO_UPPER` 17장 중 7장을 두 악으로 오판했다.
    교합면 치열이 U자라 주축 수직 투영이 이봉으로 보이기 때문이다. 두 악이 실제로
    보이는 뷰에서는 잘 맞는다 — IO_FRONT 94% · IO_LEFT 93% · IO_RIGHT 98%.

    추론에는 FDI 가 없으므로 기하로 판정한다. 협측·정면에서 두 악은 화면에서 두 줄로
    분리되고, 교합면은 한 줄이라 자동으로 꺼져야 한다.

        주축 u = 점 집합의 제1주성분 (치열궁이 뻗은 방향)
        v = u 의 수직 — **화면 아래쪽을 +로** 고정한다 (회전 |30°| 이내라 보존된다)
        v 방향 투영에 2-means → 두 무리의 간격이 산포의 `min_sep` 배를 넘으면 두 악

    라벨은 부호로 정한다(위=0, 아래=1). k-means 군집 순서는 실행마다 달라질 수 있어
    두 회차에서 뜻이 어긋나기 때문이다.
    """
    if len(P) < 6:
        return None
    C = P - P.mean(0)
    u = np.linalg.svd(C, full_matrices=False)[2][0]
    v = np.array([-u[1], u[0]])
    if v[1] < 0:
        v = -v
    t = C @ v
    lo, hi = float(t.min()), float(t.max())
    c = np.array([lo + (hi - lo) * .25, lo + (hi - lo) * .75])
    lab = np.zeros(len(t), int)
    for _ in range(20):
        lab = np.abs(t[:, None] - c[None, :]).argmin(1)
        for k in (0, 1):
            if (lab == k).any():
                c[k] = t[lab == k].mean()
    if (lab == 0).sum() < 2 or (lab == 1).sum() < 2:
        return None
    sd = np.std(t[lab == 0]) + np.std(t[lab == 1])
    if abs(c[1] - c[0]) < min_sep * max(sd, 1e-9):
        return None
    return (t > c.mean()).astype(int)          # 위=0, 아래=1


def match_kwargs(cls: str, P: np.ndarray, Q: np.ndarray) -> dict:
    """뷰에 맞는 제약을 골라 `match_ransac` 인자로 만든다.

    **정책을 한 곳에 모은다.** 두 제약 모두 뷰에 따라 켜고 꺼야 하는데, 호출자마다
    그 판단을 흩어놓으면 어긋난다 (실측에서 `assign_vote(max_vote_rel)` 이 구현만
    되고 배선이 안 된 채 방치된 적이 있다).

        악 제약    `TWO_ARCH_VIEWS`  — 교합면은 한 악뿐이라 걸면 안 된다
        정중선     `MIDLINE_VIEWS`   — 협측은 한쪽만 보여 대칭축이 없다
    """
    kw: dict = {}
    if cls in TWO_ARCH_VIEWS:
        la, lb = arch_labels(P), arch_labels(Q)
        if la is not None and lb is not None:
            kw.update(arch_a=la, arch_b=lb)
    if cls in MIDLINE_VIEWS:
        ma, mb = midline(P), midline(Q)
        if ma is not None and mb is not None:
            kw.update(mid_a=ma, mid_b=mb)
    return kw


def match_ransac(P: np.ndarray, Q: np.ndarray, *, tol: float = 0.5,
                 scale_range=(0.5, 2.0), max_deg: float = 30.0,
                 iters: int = 3000, seed: int = 0,
                 score: str = "msac",
                 size_a=None, size_b=None, w_size: float = 0.0,
                 sigma_size: float = 0.13,
                 arch_a=None, arch_b=None,
                 attr_a=None, attr_b=None,
                 mid_a=None, mid_b=None) -> dict:
    """두 회차의 치아 중심을 짝짓는다. **번호가 없어도 된다.**

    치열궁 서열 정렬 대신 RANSAC 을 쓴다. 정렬은 치아를 한 줄로 늘어놓아야 하는데,
    그 방법이 뷰마다 다르다 — 교합면은 U 자 곡선, 협측은 위·아래 두 줄이다.
    뷰별 규칙을 만들면 그만큼 깨질 곳이 늘어난다.

    RANSAC 은 **대응 2쌍이면 닮음변환 하나가 정해진다**는 성질만 쓴다. 뷰와 무관하다.

        ① 무작위로 대응 2쌍을 고른다 → 닮음변환
        ② 배율·회전이 말이 되는 범위인지 본다 (실측 배율 0.7~1.7, 회전 |12°| 이내)
        ③ 그 변환에서 **서로 가장 가까운** 짝의 수를 센다
        ④ 가장 많은 것을 고르고, 그 짝들로 다시 맞춘다

    `tol` 은 치아 간격 대비다 — 절대 픽셀로 두면 뷰마다 치아 크기가 1.4배 달라
    같은 값이 다르게 작동한다.

    ### tol 0.25 → 0.5 (2026-08-07)

    0.25 에서는 **옳은 변환을 그대로 넣어도 짝이 0~1개**였다. 정답이 후보에 못 드니
    엉뚱한 5~6개짜리 해가 이긴다. 개수로 고르는 ④ 가 고장난 게 아니라 문턱이 좁았다.

        쌍                참배율   옳은해 짝(0.25)   RANSAC이 고른 짝   0.5   1.0
        17_ab_IO_FRONT    0.818          0                5           7    10
        6_ab_IO_RIGHT     0.705          1                6          11    11

    실패한 쌍이 **회차 간 확대율 차이가 가장 큰 쌍**인 것과 맞는다. 확대율이 30% 다르면
    원근이 달라지고, 닮음변환이 못 흡수하는 잔차가 치아 간격의 1/4 을 넘는다.

    0.5 보다 넓히면 다시 나빠진다 — 엉뚱한 해도 짝을 얻어 개수 기준의 변별력이 사라지고,
    짝 수가 10.7 에서 포화한다. 49쌍 기준 5%초과 10 → 7쌍, 배율오차 중앙 1.33 → 1.02%.
    (표본이 49쌍뿐이라 이 차이 자체는 표준오차 안이다. 채택 근거는 위의 **기전**이다.)
    """
    rng = np.random.default_rng(seed)
    n, m = len(P), len(Q)
    if n < 2 or m < 2:
        return {"ok": False, "pairs": {}, "n": 0}
    # 치아 간격 — 허용 오차의 기준
    dd = np.linalg.norm(P[:, None] - P[None, :], axis=2)
    np.fill_diagonal(dd, np.inf)
    spacing = float(np.median(dd.min(1)))
    thr = tol * spacing

    best = (-math.inf if score == "msac" else 0, None)
    # **정중선 씨앗** — 치열은 준주기적이고 그 주기성을 깨는 유일한 해부학적 기준이
    # 정중선이다. 두 회차가 공유하는 대응점이므로 2점 중 1점을 공짜로 얻는다:
    #
    #   ① 탐색 공간이 `m²` → `m` 으로 줄어 정답을 훨씬 자주 뽑는다
    #   ② 모든 후보가 정중선을 맞추도록 강제되어 **한 칸 밀림이 원천 차단**된다
    #   ③ **점수 함수를 안 건드린다** — 크기 항이 잡음을 뿌려 실패한 방식을 피한다
    use_mid = mid_a is not None and mid_b is not None
    for _ in range(iters):
        if use_mid:
            i, j = int(rng.integers(n)), int(rng.integers(m))
            p1, p2, q1, q2 = P[i], np.asarray(mid_a), Q[j], np.asarray(mid_b)
        else:
            i, k = rng.choice(n, 2, replace=False)
            j, l = rng.choice(m, 2, replace=False)
            p1, p2, q1, q2 = P[i], P[k], Q[j], Q[l]
        d1 = np.linalg.norm(p1 - p2)
        d2 = np.linalg.norm(q1 - q2)
        if d1 < 1e-6 or d2 < 1e-6:
            continue
        sc = d2 / d1
        if not (scale_range[0] <= sc <= scale_range[1]):
            continue
        a1 = math.atan2(*(p2 - p1)[::-1])
        a2 = math.atan2(*(q2 - q1)[::-1])
        th = a2 - a1
        if abs(math.degrees(math.atan2(math.sin(th), math.cos(th)))) > max_deg:
            continue
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        t = q1 - sc * (R @ p1)
        pred = sc * (P @ R.T) + t
        # **서로** 가장 가까운 짝만 인정한다 (한쪽만 가까운 건 대응이 아니다)
        D = np.linalg.norm(pred[:, None] - Q[None, :], axis=2)
        a2b = D.argmin(1)
        b2a = D.argmin(0)
        pairs = {int(x): int(a2b[x]) for x in range(n)
                 if b2a[a2b[x]] == x and D[x, a2b[x]] <= thr * sc}
        if arch_a is not None and arch_b is not None:
            # **악 제약** — 상악 치아는 상악에만 짝지을 수 있다. 실측에서 `52_ab` 는
            # 회차 a 의 상악을 회차 b 의 하악에 포개 배율이 0.61 (참 1.00) 로 붕괴했다.
            pairs = {x: y for x, y in pairs.items() if arch_a[x] == arch_b[y]}
        if score == "msac":
            # **MSAC** — 개수 대신 절단 잔차의 합을 최소화한다.
            #
            # 개수만 세면 문턱 안에 들어오기만 하면 0.01·0.49 가 같은 값이다. `tol` 을
            # 0.25 → 0.5 로 넓혀 옳은 해가 후보에 들게 만든 대가로, 엉뚱한 해도 짝을
            # 얻어 개수가 포화했다 (실측 `inlier%` 가 전 조건 100%). 잔차로 재면 넓은
            # `tol` 의 이득은 유지하면서 변별력이 돌아온다.
            #
            # 짝이 없는 점은 `thr` 을 문 것으로 친다 — 그래야 "짝을 적게 만드는 해"가
            # 자동으로 벌점을 받아 개수 기준이 하던 일을 흡수한다.
            # **문턱으로 나눠 무차원으로 만든다.** 안 그러면 벌점이 후보 배율 `sc` 에
            # 비례해, 배율을 줄이는 퇴화 해가 점수에서 이긴다 (실측 p75 41%).
            # 정규화하면 짝 없는 점의 벌점이 정확히 1 이라 배율과 무관해진다.
            u = max(thr * sc, 1e-12)
            r2 = np.ones(n)
            if pairs:
                idx = np.fromiter(pairs, int, len(pairs))
                r2[idx] = np.minimum((D[idx, [pairs[int(x)] for x in idx]] / u) ** 2, 1.0)
                if w_size > 0 and size_a is not None and size_b is not None:
                    # **크기 항** — 치관은 안 변하므로 짝의 크기비가 변환 배율과 같아야
                    # 한다. **기본은 꺼져 있다** (`w_size=0`). 근거는 아래.
                    #
                    # 치아 791개에서 |log(크기비) − log(배율)| 분포 (FDI 로 정답 확인):
                    #
                    #            p25    p50    p75    p90
                    #   옳은 짝  0.027  0.063  0.130  0.282
                    #   밀린 짝  0.070  0.148  0.262  0.395     AUC 0.692
                    #
                    # `sigma_size` 를 이 표의 **옳은 짝 p75** 로 잡았다. 처음 쓴 0.065
                    # (옳은 짝 중앙값)는 너무 빡빡해서 **옳은 짝의 49%가 벌점 상한에
                    # 포화**했다 — 밀린 짝(77%)과 거의 같은 벌을 받아 변별력이 없었다.
                    #
                    # 49쌍 실측 (기준 ① GT마스크+FDI 배율):
                    #
                    #   조건        중앙   p75   5%초과   최대
                    #   기본        0.54  1.49     5     38.9
                    #   +크기       0.62  2.26    10     38.7
                    #   +악         0.54  1.74     7     76.1
                    #   +크기+악    0.62  4.68    11     19.2
                    #
                    # **겨냥한 실패는 정확히 고친다** — 변환이 붕괴한 `52_ab_IO_LEFT`
                    # 38.9 → **1.5%**, 한 칸 밀린 `12_aj_IO_LEFT` 6.3 → **3.7%**.
                    # 그런데 크기와 무관한 실패(정중선 거울·악 교차)에는 잡음만 더해
                    # `17_ab` 7.2 → 16.0%, `6_ab` 12.2 → 16.2% 로 나빠진다.
                    # AUC 0.692 는 47쌍에 잡음을 뿌리면서 2쌍을 고치기엔 약하다.
                    #
                    # 특정 실패 유형이 의심될 때만 켠다. 쌍이 49개뿐이라 `w_size` 를
                    # 제대로 정할 표본이 없다 — 훑으면 이 표본에 맞추는 것이 된다.
                    jj = np.array([pairs[int(x)] for x in idx])
                    lr = np.log(np.maximum(size_b[jj], 1e-9) /
                                np.maximum(size_a[idx], 1e-9)) - math.log(max(sc, 1e-9))
                    r2[idx] += w_size * np.minimum((lr / sigma_size) ** 2, 1.0)
            s = -float(r2.sum())          # 클수록 좋게 부호를 맞춘다
        else:
            s = len(pairs)
        if s > best[0]:
            best = (s, pairs)
    # **점수가 아니라 짝 개수로 판정한다** — `msac` 점수는 음수라 점수로 재면 항상 걸린다
    if best[1] is None or len(best[1]) < MIN_TEETH:
        return {"ok": False, "pairs": best[1] or {},
                "n": len(best[1] or {}), "spacing": spacing}

    # 인라이어로 다시 맞추고 한 번 더 짝짓는다 (초기 2쌍의 우연을 씻어낸다)
    def rematch(pr):
        i2 = sorted(pr)
        f2 = fit_robust(P[i2], Q[[pr[i] for i in i2]])
        pd = apply(P, f2)
        Dm = np.linalg.norm(pd[:, None] - Q[None, :], axis=2)
        a, b = Dm.argmin(1), Dm.argmin(0)
        return f2, {int(x): int(a[x]) for x in range(n)
                    if b[a[x]] == x and Dm[x, a[x]] <= thr * f2["scale"]}

    f, pairs = rematch(best[1])

    # **2단계 정련 — 두 회차에서 재현되지 않는 치아를 빼고 변환을 다시 세운다**
    #
    # 근거와 문턱은 `DELTA_TAU` 주석에 있다. 요점: 재현성은 사진 한 장으로 판정할 수
    # 없고(사진 단위 신호 최대 |ρ|=0.21), **두 회차의 차이**로 봐야 한다(결합 ρ=0.496).
    #
    # Δ 를 계산하려면 대응이 필요하다 — 대응을 보호하려는 필터가 대응을 요구하는
    # 순환이다. **1차 대응으로 그 순환을 끊는다.** 82% 의 쌍은 1차에서 이미 옳다.
    #
    # 빼는 것이 목적이 아니라 **변환을 재현되는 치아로 세우는** 것이 목적이다.
    # `rematch` 가 전체를 다시 배정하므로 잘못 짝지어졌던 치아가 되돌아올 수 있다.
    if attr_a and attr_b and len(pairs) >= 2 * MIN_TEETH:
        i2 = sorted(pairs)
        bad = np.zeros(len(i2), bool)
        for k, tau in DELTA_TAU.items():
            if k not in attr_a or k not in attr_b:
                continue
            d = np.abs(np.asarray(attr_b[k], float)[[pairs[i] for i in i2]] -
                       np.asarray(attr_a[k], float)[i2])
            bad |= d > tau
        if MIN_TEETH <= (~bad).sum() < len(i2):
            f, pairs = rematch({i2[t]: pairs[i2[t]] for t in np.nonzero(~bad)[0]})

    return {"ok": len(pairs) >= MIN_TEETH, "pairs": pairs, "n": len(pairs),
            "spacing": spacing, "seed_fit": f}


def register(a_inst: np.ndarray, b_inst: np.ndarray, *,
             pairs: dict | None = None, **kw) -> dict:
    """두 회차의 인스턴스 맵 → 변환.

    `pairs` 가 없으면 **같은 값끼리** 대응시킨다 (라벨은 값이 FDI 라 그대로 맞다).
    예측 인스턴스는 번호가 임의라 치열궁 서열 정렬이 따로 필요하다.
    """
    A, B = tooth_stats(a_inst), tooth_stats(b_inst)
    ua, ub = usable(A), usable(B)
    common = sorted((ua & ub) if pairs is None else
                    {k for k in pairs if k in ua and pairs[k] in ub})
    if len(common) < MIN_TEETH:
        return {"ok": False, "n": len(common),
                "why": f"쓸 만한 공통 치아 {len(common)}개 < {MIN_TEETH}"}
    P = np.array([A[v]["center"] for v in common])
    Q = np.array([B[v if pairs is None else pairs[v]]["center"] for v in common])
    f = fit_robust(P, Q, **kw)
    f.update(ok=True, n=len(common), keys=common,
             dropped=[common[i] for i in range(len(common)) if i not in set(f["used"])])
    return f


def bootstrap_scale(P: np.ndarray, Q: np.ndarray, *, n: int = 200,
                    frac: float = 0.7, seed: int = 0) -> dict:
    """치아 일부만으로 반복해 맞춰 **배율이 얼마나 흔들리는지** 본다.

    이게 정합이 실제로 낼 수 있는 정밀도다. 목표는 0.3% 아래 — 관찰하려는 실제 치열
    변화가 0.3~1.4%(문서 G2)이므로 그보다 훨씬 작아야 신호를 읽을 수 있다.

    옛 근거였던 "사람의 배율이 3~4.5% 흔들린다"는 **기각됐다** (문서 §10 — 그 값을 낸
    N1 이 재촬영 쌍이 아니었다). 사람 대비 기준선은 현재 없다.
    """
    rng = np.random.default_rng(seed)
    k = max(MIN_TEETH, int(len(P) * frac))
    out = []
    for _ in range(n):
        i = rng.choice(len(P), k, replace=False)
        out.append(fit_robust(P[i], Q[i])["scale"])
    s = np.array(out)
    return {"median": float(np.median(s)),
            "cv": float(np.std(s) / np.median(s)),
            "p5_p95": (float(np.percentile(s, 5)), float(np.percentile(s, 95)))}
