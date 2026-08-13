#!/bin/bash
# ============================================================
#  교정과 사진 자동화 웹앱 — macOS 설치 스크립트
#
#  내려받은 뒤 터미널에서:   bash ~/Downloads/install.command
#  (웹에서 받은 파일은 실행 권한이 없어 더블클릭이 안 됩니다.
#   설치가 끝나면 바탕화면 바로가기는 더블클릭으로 실행됩니다.)
# ============================================================
set -u
REPO="https://github.com/pnudh-ortho/automated-crossvisit-framing.git"
NAME="automated-crossvisit-framing"

echo "==============================================="
echo "  교정과 사진 자동화 - 설치 (macOS)"
echo "==============================================="
echo

# ── [1/4] 개발 도구 — git·python3 는 Xcode Command Line Tools 하나로 온다 ──
if ! xcode-select -p >/dev/null 2>&1; then
  echo "[1/4] 개발 도구(git, python3)가 없습니다 — Apple 설치 창을 엽니다."
  echo "      설치가 끝날 때까지 여기서 기다립니다 (취소: Ctrl+C)."
  xcode-select --install >/dev/null 2>&1
  until xcode-select -p >/dev/null 2>&1; do
    printf "."
    sleep 5
  done
  echo
fi
git --version >/dev/null 2>&1 || { echo "[오류] git 을 찾지 못했습니다."; exit 1; }
python3 --version >/dev/null 2>&1 || { echo "[오류] python3 를 찾지 못했습니다."; exit 1; }
echo "[1/4] git·python3 확인."

# ── [2/4] 설치 위치 ─────────────────────────────────────────────
echo "[2/4] 설치 위치를 고릅니다 — 폴더 선택 창이 열립니다."
BASE=$(osascript -e 'POSIX path of (choose folder with prompt "프로그램을 설치할 위치를 고르세요 — 이 안에 프로그램 폴더가 만들어집니다")' 2>/dev/null || true)
if [ -z "${BASE:-}" ]; then
  read -r -p "      설치할 폴더 경로 [기본: $HOME/Applications]: " BASE
  BASE=${BASE:-$HOME/Applications}
fi
mkdir -p "$BASE"
DEST="${BASE%/}/$NAME"
echo "      설치 위치: $DEST"

if [ -d "$DEST/.git" ]; then
  echo "      이미 설치되어 있습니다 - 업데이트합니다."
  git -C "$DEST" pull --ff-only
elif [ -e "$DEST" ]; then
  echo "[오류] 같은 이름의 폴더가 이미 있는데 설치본이 아닙니다: $DEST"
  exit 1
else
  echo "[3/4] 내려받는 중..."
  git clone --depth 1 "$REPO" "$DEST" \
    || { echo "[오류] 내려받기 실패 — 인터넷 연결을 확인하세요."; exit 1; }
fi

# ── [4/4] 가상환경 ──────────────────────────────────────────────
echo "[4/4] 가상환경과 의존성을 설치합니다 (처음엔 몇 분 걸립니다)..."
cd "$DEST" || exit 1
[ -x .venv/bin/python ] || python3 -m venv .venv \
  || { echo "[오류] 가상환경을 만들지 못했습니다."; exit 1; }
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r webapp/requirements.txt \
  || { echo "[오류] 의존성 설치 실패 — 인터넷 연결을 확인하세요."; exit 1; }

# ── 바탕화면 바로가기 (선택) ────────────────────────────────────
read -r -p "바탕화면에 CRoCs 바로가기를 만들까요? (Y/n): " MK
case "${MK:-Y}" in
  n|N) : ;;
  *) if [ -d "$HOME/Desktop" ]; then
       ln -sf "$DEST/run.command" "$HOME/Desktop/CRoCs.command"
       echo "      바탕화면에 CRoCs.command 를 만들었습니다."
     fi ;;
esac

echo
echo "==============================================="
echo "  설치 완료"
echo "==============================================="
echo "  폴더: $DEST"
echo
echo "  다음 순서:"
echo "    1. 안내받은 드라이브에서 모델 파일(약 400MB)을 받아"
echo "       $DEST/models 폴더에 넣으세요"
echo "    2. run.command (또는 바탕화면의 CRoCs) 를 더블클릭하세요"
echo
read -r -p "models 폴더를 지금 열까요? (Y/n): " OP
case "${OP:-Y}" in
  n|N) : ;;
  *) open "$DEST/models" 2>/dev/null ;;
esac
