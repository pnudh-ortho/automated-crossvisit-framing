"""orthoreg — 차수 간 사진 정합의 **추론 전용** 코어.

    from orthoreg import build_from_ckpt, instances_from, register as RG
    from orthoreg import CLS_TOOTH, CLS_APPLIANCE

배포물(webapp)과 연구 저장소(train_revisit)가 **같은 파일**을 쓴다. 예전에는
webapp 이 `sys.path` 로 연구 저장소를 끌어왔는데, 그러면 배포물이 학습 스크립트와
22GB 산출물에 의존하고 두 곳의 코드가 갈라진다.
"""

from ._version import __version__
from .constants import CLS_APPLIANCE, CLS_BG, CLS_TOOTH, IGNORE, N_CLASSES
from .runtime import SegResult, Segmenter
from .segment import MIN_PX, assign_instances, instances_from

__all__ = ["__version__", "CLS_APPLIANCE", "CLS_BG", "CLS_TOOTH", "IGNORE", "N_CLASSES",
           "Segmenter", "SegResult", "MIN_PX", "assign_instances",
           "instances_from", "build", "build_from_ckpt"]


def __getattr__(name):
    """`build`·`build_from_ckpt` 는 **torch 가 있을 때만** 쓸 수 있다.

    최상단에서 import 하면 배포본(onnxruntime 만 설치)에서 `import orthoreg` 자체가
    죽는다. 실제로 부를 때 끌어온다.
    """
    if name in ("build", "build_from_ckpt"):
        from . import model                                       # noqa: PLC0415
        return getattr(model, name)
    raise AttributeError(name)
