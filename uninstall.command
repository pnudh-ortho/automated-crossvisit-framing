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
echo "  Patient data is KEPT unless you ask otherwise."
echo
read -r -p "Remove the program? (y/N): " GO
[ "$GO" = "y" ] || [ "$GO" = "Y" ] || { echo; echo "Cancelled."; exit 0; }

DROP=""
echo
read -r -p "Also delete PATIENT DATA?  This cannot be undone. (y/N): " D2
if [ "$D2" = "y" ] || [ "$D2" = "Y" ]; then
  echo
  echo "  Type  DELETE  to confirm removal of patient records."
  read -r -p "  > " W
  if [ "$W" = "DELETE" ]; then DROP="$DATA"; else echo "  Not confirmed - patient data will be kept."; fi
fi

# run from /tmp so we are not deleting our own directory
S=$(mktemp /tmp/acf_uninstall.XXXXXX.sh)
cat > "$S" <<EOF
#!/bin/bash
sleep 2
echo "Removing $PROG ..."
rm -rf "$PROG"
[ -n "$DROP" ] && { echo "Removing $DROP ..."; rm -rf "$DROP"; }
echo
echo "Uninstall complete."
rm -f "$S"
EOF
chmod +x "$S"
exec "$S"
