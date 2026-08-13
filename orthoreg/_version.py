"""코드 버전 — **`version.json` 하나가 출처**다.

업데이트 시스템이 읽고, 사용자가 화면에서 보고, 가중치 버전도 같이 담는 파일이다.
`pyproject.toml` 에 숫자를 또 적으면 둘이 어긋나고 어느 게 진짜인지 알 수 없게 된다.

**무거운 것을 import 하지 않는다.** `pyproject.toml` 의 `dynamic` 버전이 이 모듈을
빌드 시점에 읽는데, 그때는 격리 환경이라 cv2·numpy 가 없다.
"""

from __future__ import annotations

import json
from pathlib import Path


def _read() -> str:
    f = Path(__file__).resolve().parents[1] / "version.json"
    if f.exists():
        try:
            return str(json.loads(f.read_text(encoding="utf-8"))["app"])
        except Exception:                                          # noqa: BLE001
            pass
    return "0.0.0+unknown"          # 저장소 밖에 단독 설치된 경우


__version__ = _read()
