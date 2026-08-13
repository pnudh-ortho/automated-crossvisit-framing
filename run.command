#!/bin/bash
# ============================================================
#  교정과 사진 자동화 웹앱 — macOS (더블클릭)
#  최초 실행 시 가상환경을 만들고 의존성을 설치합니다.
# ============================================================
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "[최초 설정] 가상환경을 만들고 의존성을 설치합니다..."
  python3 -m venv .venv || { echo "python3 가 필요합니다."; read -r; exit 1; }
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
