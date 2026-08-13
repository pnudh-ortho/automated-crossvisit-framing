"""시맨틱 클래스 상수.

`segdata.py` 에서 이것만 떼어냈다 — 그 파일은 학습용 `Dataset` 과 라벨 로더를
담고 있어 `torch.utils.data` 를 끌어온다. 추론에는 숫자 셋이면 된다.

전경은 **치아 ∪ 장치**다. 브라켓이 치아 위에 붙어 있어서, 치아 클래스만 전경으로
쓰면 브라켓이 치아 마스크를 가운데서 갈라 조각낸다.
"""

IGNORE = 255
CLS_BG, CLS_TOOTH, CLS_APPLIANCE = 0, 1, 2
N_CLASSES = 3
