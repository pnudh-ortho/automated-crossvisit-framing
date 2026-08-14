#!/bin/bash
# ============================================================
#  교정과 사진 자동화 웹앱 — macOS (더블클릭)
#  최초 실행 시 가상환경을 만들고 의존성을 설치합니다.
# ============================================================
cd "$(dirname "$0")" || exit 1

# ── 파이썬 고르기 — 3.10 이상이어야 한다 ────────────────────────
# macOS 가 기본으로 주는 python3 는 3.9 다(Xcode Command Line Tools). 이 앱은
# `str | None` 같은 3.10 문법을 쓰므로, 3.9 로 만든 가상환경은 켜자마자 죽는다.
# 실제로 그렇게 설치된 맥이 있었다 — 설치는 끝났는데 실행이 안 됐다.
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
          >/dev/null 2>&1; }

find_python() {
  for c in python3.13 python3.12 python3.11 python3.10; do
    command -v "$c" >/dev/null 2>&1 && py_ok "$c" && { echo "$c"; return; }
  done
  command -v python3 >/dev/null 2>&1 && py_ok python3 && echo python3
}

need_python() {
  echo
  echo "[오류] Python 3.10 이상이 필요합니다."
  echo "       macOS 기본 python3 는 3.9 라 이 프로그램이 뜨지 않습니다."
  echo
  echo "  둘 중 하나로 설치해 주세요"
  echo "    1) https://www.python.org/downloads/  에서 최신판 내려받아 설치"
  echo "    2) 터미널에서:  brew install python@3.12"
  echo
  echo "  설치한 뒤 이 창을 닫고 다시 실행해 주세요."
  read -r -p "  Enter 를 누르면 닫습니다..."
  exit 1
}

# 이미 있는 가상환경이 3.10 미만이면 버리고 다시 만든다 — 그대로 두면 고친
# 스크립트로 실행해도 옛 파이썬을 계속 쓴다.
if [ -x ".venv/bin/python" ] && ! py_ok .venv/bin/python; then
  echo "[알림] 가상환경이 오래된 파이썬(3.9)으로 만들어져 있습니다 — 다시 만듭니다."
  rm -rf .venv
fi

if [ ! -x ".venv/bin/python" ]; then
  PY="$(find_python)"
  [ -n "$PY" ] || need_python
  echo "[최초 설정] $("$PY" -V) 로 가상환경을 만들고 의존성을 설치합니다..."
  "$PY" -m venv .venv || need_python
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r webapp/requirements.txt
fi

echo
echo "서버를 시작합니다. 브라우저가 자동으로 열립니다."
echo "종료하려면 이 창에서 Ctrl+C 를 누르세요."
echo

# 종료코드 42 = 업데이트 후 재시작 요청. 그 외에는 루프를 끝낸다.
while true; do
  .venv/bin/python webapp/backend/main.py
  code=$?
  [ "$code" -eq 42 ] || break
  echo
  echo "업데이트를 적용하고 다시 시작합니다..."
  echo
done
