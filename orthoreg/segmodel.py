"""
치아·브라켓 분할 모델 — SAM2 인코더 + 새 디코더.

SAM2 를 **프롬프트 모델이 아니라 사전학습 인코더로** 쓴다. 프롬프트 인코더·마스크
디코더는 버리고, `image_encoder` 만 가져와 우리 클래스를 뱉는 디코더를 새로 붙인다.

    입력 1024×1024
      → SAM2 ImageEncoder (Hiera + FPN)
      → backbone_fpn: [256ch@256², 256ch@128², 256ch@64²]
      → 디코더 (+ 원본 이미지 stem skip)
      → 시맨틱 3채널 + 중심 1채널 + 오프셋 2채널

### 왜 프롬프트 기구를 버리나

전체 3,480장에 **프롬프트 없이 자동으로** 돌려야 한다. SAM2 를 SAM2 로 쓰면 프롬프트를
만들어 줘야 하고(prompt generator), 클래스도 따로 붙여야 한다(mask classifier).
시맨틱 헤드를 달면 그 두 부품이 통째로 불필요하다.

> 2026-08-03: 프롬프트 기반 경로(자동 제안·대화형 클릭)는 전부 폐기했다. SAM2 는
> **오직 사전학습 가중치로만** 남는다. 학습 라벨은 사람이 직접 그린다.

### 왜 trunk 를 동결하나

| | 파라미터 |
|---|---|
| trunk (Hiera) | **212.1 M** |
| neck (FPN) | 0.6 M |

라벨이 100장인데 212 M 을 전부 풀면 과적합 지름길이다. trunk 동결 + neck 학습 +
새 디코더면 학습 대상이 수 M 으로 떨어진다. 부족하면 그때 trunk 상단 블록을 열거나
LoRA 를 얹는다(§사다리).

### 왜 stem skip 이 필요한가

손실이 두 군데서 일어난다. **리사이즈**(4,287px → 1024px, 4.2배)와 **인코더**(FPN 최고
해상도가 stride 4). stem skip 은 뒤엣것만 되돌린다 — 원본 이미지에서 stride 1·2 특징을
뽑아 디코더 후반(256²→512²→1024²)에 합친다.

인코더는 "여기 어디쯤 경계가 있다"를 주고, stem 은 "정확히 이 픽셀"을 준다. 이것만으로
경계 위치 정확도가 원본 기준 ~17px 에서 ~4px 로 간다. 리사이즈 손실까지 없애려면
타일로 잘라야 하는데, 문맥을 잃고 계산이 10배라 **먼저 이걸로 재보고 결정한다.**

### 학습 목표는 `teeth_only` 다 (2026-08-04)

`label/teeth_only/` — **보이는 치아**. 브라켓 자리는 빠져 있다.
amodal `teeth`(브라켓 포함 전체)는 정합이 면적을 쓸지 폭·높이를 쓸지 정해진 뒤에 붙인다.
폭·높이는 바깥 끝으로 재므로 내부 가림에 강해, amodal 이 필요 없을 수도 있다.

브라켓을 별도 클래스로 두는 이유는 셋이다:
  · 배경에 묻으면 **금속 브라켓과 치은을 같은 것**으로 배운다 — 더 어려운 문제가 된다
  · 정합에서 믿을 만한 치아를 고를 때 "경계가 장치에 얼마나 닿았나"가 필요하다
  · 출력 채널 하나 차이라 비용이 없다

### 인스턴스는 중심 + 오프셋으로 뽑는다 (2026-08-04)

시맨틱만으로는 안 된다. 실측: 치아 21개가 1024 시맨틱에서 **8덩어리**로 뭉친다
(연결성분 기준). 이웃 치아가 맞닿아 있고, 정면 뷰에서는 상·하악까지 붙기 때문이다.
치아 사이 이음매가 원본 1~5px 이라 1024 로 줄이면 1px 미만이 되어 사라진다.

경계 채널로 풀려던 시도는 접었다. 얇은 이음매를 예측하는 데 의존하는데, 그게 바로
안 되는 지점이다. 대신:

    중심 히트맵    치아마다 봉우리 하나. **간격이 ~125px** 라 여유가 크다
    오프셋         픽셀 → 자기 치아 중심. 부드러운 벡터장이라 배우기 쉽다
    배정           픽셀+오프셋이 가장 가까운 중심으로

**연결성을 안 쓴다.** 그래서 두 방향을 동시에 고친다:

| | 연결성분 | 중심+오프셋 |
|---|---|---|
| 이웃과 딱 붙은 치아 | 하나로 뭉침 | **갈림** (중심이 다름) |
| 브라켓·와이어에 잘린 치아 | 여럿으로 쪼개짐 | **합쳐짐** (중심이 같음) |

라벨로 검증: 오프셋에 20px 잡음을 줘도 21개가 그대로 복원되고 픽셀 일치 97%.
`38_d_io_front` 은 연결성분이 29덩어리(치아 24개보다 많다)인데 중심+오프셋은 24개다.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

# 문서 §6.1 — 배경 / 치아 / 브라켓
N_CLASSES = 3
CLS_BG, CLS_TOOTH, CLS_APPLIANCE = 0, 1, 2
N_ARCH = 3          # 배경 / 상악 / 하악


def conv_bn(cin: int, cout: int, k: int = 3, d: int = 1) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(cin, cout, k, padding=d * (k // 2), dilation=d, bias=False),
        nn.BatchNorm2d(cout),
        nn.ReLU(inplace=True),
    )


class UpBlock(nn.Module):
    """2배 업샘플 후 skip 과 합친다."""

    def __init__(self, cin: int, cskip: int, cout: int):
        super().__init__()
        self.reduce = conv_bn(cin, cout, 1)
        self.fuse = nn.Sequential(conv_bn(cout + cskip, cout), conv_bn(cout, cout))

    def forward(self, x: torch.Tensor, skip: torch.Tensor | None) -> torch.Tensor:
        x = F.interpolate(self.reduce(x), scale_factor=2, mode="bilinear", align_corners=False)
        if skip is not None:
            if skip.shape[-2:] != x.shape[-2:]:
                skip = F.interpolate(skip, size=x.shape[-2:], mode="bilinear", align_corners=False)
            x = torch.cat([x, skip], dim=1)
        return self.fuse(x)


class ToothSegNet(nn.Module):
    """
    SAM2 인코더 + 시맨틱 디코더.

    출력은 `(logits, heat, offset)`:
      · `logits` (B, 3, H, W) — 배경 / 치아 / 브라켓
      · `heat`   (B, 1, H, W) — 중심 히트맵 (sigmoid 전 로짓)
      · `offset` (B, 2, H, W) — 픽셀 → 자기 치아 중심 (dy, dx)

    시맨틱은 **어디까지가 치아인지**를, 중심·오프셋은 **몇 개이고 어느 것인지**를 말한다.
    오프셋은 전경 위에서만 의미가 있어 시맨틱이 없으면 적용할 자리를 모른다.
    """

    def __init__(self, sam2_model, *, width: int = 128,
                 freeze_trunk: bool = True, train_neck: bool = True,
                 arch: bool = False, inst_stride: int = 1,
                 inst_width: int = 256, scale_head: bool = False,
                 inst_src: str = "fpn0",
                 stem_mult: int = 1, up4_mult: int = 1):
        super().__init__()
        self.encoder = sam2_model.image_encoder
        self.image_size = int(getattr(sam2_model, "image_size", 1024))

        for p in self.encoder.trunk.parameters():
            p.requires_grad = not freeze_trunk
        for p in self.encoder.neck.parameters():
            p.requires_grad = bool(train_neck)

        # 원본 이미지에서 고해상 skip — FPN 이 stride 4 까지만 주기 때문
        c1, c2 = 32 * stem_mult, 64 * stem_mult
        self.stem1 = conv_bn(3, c1)                        # stride 1
        self.stem2 = nn.Sequential(nn.MaxPool2d(2), conv_bn(c1, c2))   # stride 2

        w = width
        w4 = (w // 4) * up4_mult
        self.up1 = UpBlock(256, 256, w)       # 64²  → 128²  (+fpn[1])
        self.up2 = UpBlock(w, 256, w)         # 128² → 256²  (+fpn[0])
        self.up3 = UpBlock(w, c2, w // 2)     # 256² → 512²  (+stem2)
        self.up4 = UpBlock(w // 2, c1, w4)    # 512² → 1024² (+stem1)

        self.head_sem = nn.Conv2d(w4, N_CLASSES, 1)
        self.head_arch = nn.Conv2d(w4, N_ARCH, 1) if arch else None

        # ── 인스턴스 가지 ────────────────────────────────────────────────
        # `inst_stride=4` 면 중심·오프셋·반경을 **fpn[0] 의 256² 에서** 두껍게 뽑는다.
        #
        # 왜: 셋 다 저주파다. 중심은 가우시안 봉우리, 오프셋은 인스턴스 **안에서 정확히
        # 선형**(`v = c − x`), 반경은 치아마다 상수다. 1024² 에서 뽑을 이유가 없는데
        # 그 대가로 최종 특징 32채널을 시맨틱과 나눠 쓰며 굶고 있었다.
        #
        # 증거: 악 헤드 3채널을 같은 32채널에 얹었더니 **시맨틱 IoU 는 0.925 로 불변인데
        # 잡힘만 89% → 55%** 로 무너졌다. 중심 경로가 그 병목에서 가장 얇게 서 있다.
        #
        # Panoptic-DeepLab 도 중심·오프셋을 stride 4 에서 예측한다. 256² × 256ch 는
        # 1024² × 32ch 보다 활성 메모리가 **오히려 적으면서** 채널은 8배다.
        self.inst_stride = int(inst_stride)
        if inst_stride == 1:
            self.inst_branch = None
            ci = w4
        else:
            ci = inst_width
            # **입력을 어디서 받나** (2026-08-07 추가)
            #   fpn0 — 인코더 FPN 최고해상 레벨. 디코더를 안 거친 값이라 인스턴스
            #          가지가 시맨틱 경로와 완전히 분리된다
            #   y2   — `up2` 출력. `fpn[2]→up1→up2` 로 **저해상 레벨의 문맥이 이미
            #          합쳐진** 특징이다. 오프셋처럼 "치아 전체의 배치"를 알아야 하는
            #          과제에는 팽창으로 수용영역을 억지로 키우는 것보다 유리할 수 있다
            self.inst_src = inst_src
            cin = 256 if inst_src == "fpn0" else width
            # **팽창 스택으로 수용영역을 치아 크기까지 키운다.**
            #
            # 앞선 실패: conv 2층이면 수용영역이 5×5 (@256² = 20px @1024²) 인데 치아
            # 지름이 126px 이다. 픽셀이 자기 치아 중심을 못 보므로 오프셋을 **원리적으로
            # 계산할 수 없다.** 그때 L1 최적해는 "한 치아 안 오프셋의 평균 = 정확히 0"
            # 이고, 모델은 그 최적해를 정확히 찾았다 — 실측 예측 |off| 0.82 대 타깃 7.6,
            # 손실이 8.29→7.77 로 9,600스텝 동안 평평했다. 학습 실패가 아니라 설계 오류다.
            #
            # dilation 1·2·4·8 이면 수용영역 = 1 + 2·(1+2+4+8) = **31px @256²
            # = 124px @1024²** 로 치아 하나를 정확히 덮는다. 파라미터당 수용영역이
            # 가장 싸고, 해상도를 안 잃는다 (풀링과 달리).
            self.inst_branch = nn.Sequential(
                conv_bn(cin, ci, d=1), conv_bn(ci, ci, d=2),
                conv_bn(ci, ci, d=4), conv_bn(ci, ci, d=8))
        self.head_ctr = nn.Conv2d(ci, 1, 1)
        self.head_off = nn.Conv2d(ci, 2, 1)
        # 반경 헤드 — log 등가원반경. 후처리의 길이 상수(NMS·max_vote·MIN_PX)를
        # 전부 여기에 묶어 **화소별 적응**으로 만든다. 1채널이라 비용이 없다.
        self.head_scale = nn.Conv2d(ci, 1, 1) if scale_head else None

        # **focal 을 쓰려면 중심 헤드 편향을 사전확률로 초기화해야 한다.**
        # 초기 bias=0 이면 sigmoid=0.5 라, 음성 6만 화소가 각각 기여하고 그 합을
        # 양성 20여 개로 나눈다 → 초기 손실이 수백이 되고 옵티마이저가 먼저 전체를
        # 0 으로 짓누르느라 봉우리를 못 세운다 (실측: 학습 이미지에서도 heat 최대
        # 0.45, 문턱 0.3 을 넘는 화소가 GT 정점 수보다 적었다).
        # RetinaNet 의 π=0.01 사전편향, CenterNet 의 −2.19 가 같은 장치다.
        nn.init.constant_(self.head_ctr.bias, -2.19)

    def trainable_parameters(self):
        return [p for p in self.parameters() if p.requires_grad]

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        H, W = x.shape[-2:]
        s1 = self.stem1(x)
        s2 = self.stem2(s1)

        enc = self.encoder(x)
        fpn = enc["backbone_fpn"]            # [256², 128², 64²]

        y = self.up1(fpn[2], fpn[1])
        y2 = self.up2(y, fpn[0])                  # 256²
        y = self.up3(y2, s2)
        y = self.up4(y, s1)
        if y.shape[-2:] != (H, W):
            y = F.interpolate(y, size=(H, W), mode="bilinear", align_corners=False)
        if self.inst_branch is None:
            z = y
        else:
            z = self.inst_branch(fpn[0] if self.inst_src == "fpn0" else y2)
        return (self.head_sem(y), self.head_ctr(z), self.head_off(z),
                self.head_arch(y) if self.head_arch is not None else None,
                self.head_scale(z) if self.head_scale is not None else None)


# ── 손실 ────────────────────────────────────────────────────────────────────
def centroid_loss(prob: torch.Tensor, off_pred: torch.Tensor,
                  inst: torch.Tensor, *, eps: float = 1e-6) -> torch.Tensor:
    """**하류가 실제로 쓰는 값**에 직접 손실을 건다 (2026-08-05).

    나머지 손실은 전부 대리지표다 — 픽셀 CE 는 라벨 일치도를, 중심 히트맵은 봉우리
    위치를, 오프셋은 벡터장을 맞춘다. 그런데 `revseg/register.py` 의 `tooth_stats` 가
    마스크에서 꺼내 쓰는 것은 **치아별 무게중심 좌표 하나**뿐이고, 정합의 정확도는
    전적으로 그 값에 달려 있다. 지금까지 그 값에는 아무 손실도 안 걸려 있었다.

    ### 왜 IoU 를 올리는 것으로는 부족한가

    무게중심은 **균일한** 경계 오차에 대해 1차로 상쇄된다 — 라벨 25장 실측에서
    경계를 ±10px 팽창·침식해도 중심은 1.18px(치아 간격의 0.30%)밖에 안 움직인다.
    반대로 **비대칭** 오차(잇몸·입술로 한쪽만 새는 것)는 IoU 를 조금 깎으면서 중심을
    크게 옮긴다. 픽셀 손실은 이 둘을 같은 무게로 다루는데, **우리에게는 하나만 해롭다.**

    ### 무엇을 계산하나

        v(x) = x + ô(x)                 픽셀이 가리키는 중심 (오프셋 예측)
        ĉ_k  = Σ p(x)·v(x) / Σ p(x)     GT 인스턴스 k 안에서 치아확률로 가중평균
        L    = mean_k |ĉ_k − c_k|₁      c_k 는 라벨 무게중심

    `p`(시맨틱 확률)와 `ô`(오프셋) 양쪽으로 기울기가 흘러, **둘이 함께 만들어 낼
    무게중심**을 직접 맞춘다. 사실상 `register.py` 의 `vote_sd` 품질 신호를 지도학습
    하는 것이다.

    **GT 중심은 `center_mode` 와 무관하게 항상 픽셀 평균**이다. `center_mode="dt"` 는
    히트맵 타깃을 거리변환 최대점에 두지만(브라켓 위에 봉우리가 생기는 것을 막으려고),
    정합이 쓰는 값은 무게중심이므로 이 손실만은 무게중심을 따라간다.

    한계: 적분 구간이 GT 마스크 **안**이라 마스크 **밖으로** 새어 나간 픽셀은 여기서
    안 잡힌다. 그쪽은 CE 가 맡는다.
    """
    B, H, W = prob.shape
    dev = prob.device
    p = prob
    yy = torch.arange(H, device=dev, dtype=torch.float32).view(H, 1).expand(H, W)
    xx = torch.arange(W, device=dev, dtype=torch.float32).view(1, W).expand(H, W)

    tot = prob.new_zeros(())
    n = 0
    for b in range(B):
        m = inst[b] > 0
        if not m.any():
            continue
        _, idx = torch.unique(inst[b][m], return_inverse=True)
        K = int(idx.max()) + 1
        w = p[b][m].clamp(min=eps)
        z = lambda: torch.zeros(K, device=dev, dtype=w.dtype)          # noqa: E731
        sw = z().index_add_(0, idx, w).clamp(min=eps)
        cnt = z().index_add_(0, idx, torch.ones_like(w)).clamp(min=1.0)
        vy = (yy + off_pred[b, 0])[m]
        vx = (xx + off_pred[b, 1])[m]
        py = z().index_add_(0, idx, w * vy) / sw
        px = z().index_add_(0, idx, w * vx) / sw
        gy = z().index_add_(0, idx, yy[m]) / cnt
        gx = z().index_add_(0, idx, xx[m]) / cnt
        tot = tot + (torch.abs(py - gy) + torch.abs(px - gx)).sum()
        n += K
    return tot / max(n, 1)


def center_focal(heat_logit: torch.Tensor, heat: torch.Tensor,
                 *, alpha: float = 2.0, beta: float = 4.0, eps: float = 1e-4):
    """CenterNet/CornerNet 계열 **penalty-reduced focal**.

    기존은 가중 MSE 였다 — 봉우리가 화면의 0.1% 도 안 돼서 그냥 MSE 를 걸면 "전부 0"이
    최적해가 되고, 그래서 양성 근처에 `w_center=20` 을 곱해 억지로 균형을 맞췄다.
    **그 20 은 튜닝값이고 근거가 없다.**

    focal 은 그 균형을 구조적으로 잡는다:
      · 양성(정점): 이미 잘 맞히면 `(1-p)^α` 가 기여를 줄인다
      · 음성: **가우시안 값으로 벌점을 깎는다** `(1-y)^β` — 정점 바로 옆은 "거의 정답"
        이므로 세게 벌하지 않는다. 이게 근접 오탐(쪼개짐의 원인)을 직접 겨냥한다
      · 정규화는 **양성 개수**로 — 배경 픽셀 수에 안 휘둘린다

    가중치 상수가 사라지고, 대신 α·β 라는 표준값(2, 4)만 남는다.
    """
    p = torch.sigmoid(heat_logit).squeeze(1).clamp(eps, 1 - eps)
    pos = heat >= 1.0 - 1e-6
    n = pos.sum().clamp(min=1).float()
    lp = -((1 - p) ** alpha) * torch.log(p) * pos
    ln = -((1 - heat) ** beta) * (p ** alpha) * torch.log(1 - p) * (~pos)
    return (lp.sum() + ln.sum()) / n


def seg_loss(logits: torch.Tensor, target: torch.Tensor,
             heat_logit: torch.Tensor | None = None,
             off_pred: torch.Tensor | None = None,
             heat: torch.Tensor | None = None,
             off: torch.Tensor | None = None,
             inst: torch.Tensor | None = None,
             arch_logit: torch.Tensor | None = None,
             arch: torch.Tensor | None = None,
             scale_logit: torch.Tensor | None = None,
             scale: torch.Tensor | None = None,
             *, ignore_index: int = 255,
             class_weight: torch.Tensor | None = None,
             w_center: float = 20.0, w_offset: float = 0.02,
             w_centroid: float = 0.05, w_arch: float = 0.5,
             w_scale: float = 0.1, focal: bool = False) -> dict:
    """
    시맨틱 CE + 중심 히트맵 + 오프셋.

    `ignore_index` 는 **라벨이 없는 곳**이다 — `raw_quad` 바깥(사람이 회전·확대하며
    생긴 흰 여백, 100장 중 19장)과 검수에서 배정 안 된 영역. 조용히 배경으로 치지
    않고 손실에서 뺀다. 원본이 없는 자리를 배경이라 가르칠 근거가 없다.

    `class_weight` 가 필요한 이유: 실측 픽셀 비율이 배경 80.6% / 치아 18.6% /
    **브라켓 0.79%** 다. 가중치 없이 두면 브라켓을 아예 안 그려도 손실이 거의 안 오른다.
    """
    ce = F.cross_entropy(logits, target, weight=class_weight,
                         ignore_index=ignore_index)
    out = {"loss": ce, "ce": ce.detach()}
    if heat is None:
        return out

    # 중심: focal 이면 가중치 상수가 필요 없다. 아니면 옛 가중 MSE.
    if focal:
        ctr = center_focal(heat_logit, heat)
    else:
        hp = torch.sigmoid(heat_logit.squeeze(1))
        wmap = 1.0 + w_center * heat
        ctr = (wmap * (hp - heat) ** 2).mean()

    # 오프셋: **치아 픽셀에서만** 잰다. 배경에는 가리킬 중심이 없다.
    # 중심·오프셋이 stride 4 에 있으면 마스크도 그 격자의 인스턴스 맵에서 가져온다 —
    # 1024² 시맨틱과 섞으면 해상도가 안 맞는다.
    if inst is not None and inst.shape[-2:] == off_pred.shape[-2:]:
        m = (inst > 0).unsqueeze(1)
    else:
        m = (target == CLS_TOOTH).unsqueeze(1)
    n = m.sum().clamp(min=1)
    ofs = (torch.abs(off_pred - off) * m).sum() / (n * 2)

    out["loss"] = ce + ctr + w_offset * ofs
    out["ctr"] = ctr.detach()
    out["off"] = ofs.detach()

    # 반경 — 치아 픽셀에서만 log 등가원반경을 L1 로 맞춘다. 로그라 배율 불변이고,
    # 후처리의 길이 상수를 여기에 묶으면 한 사진 안의 전치부/구치부 크기 차이(2배 이상)
    # 까지 따라간다. 고정 NMS 61px 은 그걸 못 한다.
    if scale_logit is not None and scale is not None and w_scale > 0:
        ms = scale > 0
        if ms.any():
            sc = (torch.abs(scale_logit.squeeze(1) - scale) * ms).sum() / ms.sum()
            out["loss"] = out["loss"] + w_scale * sc
            out["scl"] = sc.detach()

    # 악(arch) — 상악/하악을 픽셀마다 예측한다.
    #
    # **왜 필요한가.** 협측 뷰에서 상악과 하악 치아가 화면에서 맞닿아 있어 시맨틱 전경이
    # 하나로 이어진다. 그래서 하악 치아 픽셀이 상악 중심으로 배정되는 일이 생기고
    # (실측: 배정 면적의 1.4%가 상하악을 걸친 인스턴스), 후처리로는 못 막았다 —
    # 침범한 픽셀이 인스턴스 본체와 같은 연결성분 안에 있어 조각 단위 규칙도 안 먹는다.
    # 악을 예측해 **배정을 같은 악 안으로 제한**하면 그 실패가 구조적으로 0 이 된다.
    #
    # 2클래스라 앞서 기각한 FDI 32클래스와 난이도가 다르고, 공간적으로 이어진 큰 영역이라
    # 배우기 쉽다. 교합면 뷰는 한 악뿐이라 제약이 자동으로 만족된다.
    if arch_logit is not None and arch is not None and w_arch > 0:
        ar = F.cross_entropy(arch_logit, arch, ignore_index=ignore_index)
        out["loss"] = out["loss"] + w_arch * ar
        out["arch"] = ar.detach()

    # 하류 정렬 — 정합이 실제로 쓰는 무게중심에 직접 건다
    if inst is not None and w_centroid > 0:
        pt = logits.softmax(1)[:, CLS_TOOTH:CLS_TOOTH + 1]
        if pt.shape[-2:] != off_pred.shape[-2:]:
            pt = F.adaptive_avg_pool2d(pt, off_pred.shape[-2:])
        cen = centroid_loss(pt.squeeze(1), off_pred, inst)
        out["loss"] = out["loss"] + w_centroid * cen
        out["cen"] = cen.detach()
    return out


@torch.no_grad()
def confusion(logits: torch.Tensor, target: torch.Tensor,
              ignore_index: int = 255) -> torch.Tensor:
    """(N_CLASSES, N_CLASSES) 혼동행렬. IoU 계산용."""
    pred = logits.argmax(1)
    m = target != ignore_index
    k = target[m] * N_CLASSES + pred[m]
    return torch.bincount(k, minlength=N_CLASSES ** 2).reshape(N_CLASSES, N_CLASSES)


def iou_from_confusion(cm: torch.Tensor) -> list[float]:
    inter = cm.diag().float()
    union = cm.sum(0).float() + cm.sum(1).float() - inter
    return [float(i / u) if u > 0 else float("nan") for i, u in zip(inter, union)]
