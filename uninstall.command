#!/bin/bash
# Uninstall (macOS / Linux).  Works even when the app will not start.
cd "$(dirname "$0")" || exit 1
PROG="$PWD"
DATA="${PROG}_data"
if [ -f settings.json ]; then
  R=$(sed -n 's/.*"root"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' settings.json | head -1)
  [ -n "$R" ] && DATA="$R"
fi

echo "==============================================="
echo "  Uninstall"
echo "==============================================="
echo
echo "  Program folder : $PROG"
echo "  Patient data   : $DATA"
echo
echo "  The program folder will be removed."
echo "  Patient data is NEVER touched by this uninstaller."
echo "  Delete it yourself in Finder if you no longer need it."
echo
read -r -p "Remove the program? (y/N): " GO
[ "$GO" = "y" ] || [ "$GO" = "Y" ] || { echo; echo "Cancelled."; exit 0; }

# 환자 자료는 여기서 지우지 않는다. 의료 기록은 `rm -rf` 로 지우면 되돌릴 길이
# 없고(휴지통을 거치지 않는다), 확인 문구를 잘못 친 한 번과 맞바꿀 값이 아니다.
# 경로만 알려 주고 지우는 일은 사람이 Finder 에서 하게 둔다.

# run from /tmp so we are not deleting our own directory
S=$(mktemp /tmp/acf_uninstall.XXXXXX.sh)
cat > "$S" <<EOF
#!/bin/bash
sleep 2
echo "Removing $PROG ..."
rm -rf "$PROG"
echo
echo "Uninstall complete."
rm -f "$S"
EOF
chmod +x "$S"
exec "$S"
