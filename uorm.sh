#!/usr/bin/env bash
# uormbt control script — use this inside the screen session.
#
#   ./uorm.sh start     dashboard + routine every 6h (foreground; Ctrl+C stops it,
#                       screen session stays alive so you can start it again)
#   ./uorm.sh once      run one routine pass and print the digest
#   ./uorm.sh status    print one dashboard snapshot
#   ./uorm.sh new NAME [REFCODE]   create an account
#
set -u
cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then PY=".venv/bin/python"; elif command -v python3 >/dev/null; then PY="python3"; else echo "python3 tidak ditemukan"; exit 1; fi

case "${1:-start}" in
  start)  exec "$PY" uorm_dashboard.py ;;
  once)   exec "$PY" uorm_daily.py ;;
  status) exec "$PY" uorm_dashboard.py --once ;;
  new)    shift; exec "$PY" new_account.py "$@" ;;
  *)
    cat <<'EOF'
uormbt — perintah:
  ./uorm.sh start        # dashboard live + rutin tiap 3 jam  (Ctrl+C = stop, tetap di screen)
  ./uorm.sh once         # jalankan rutin sekali
  ./uorm.sh status       # tampilkan dashboard sekali (tanpa live)
  ./uorm.sh new NAME [KODE]   # daftar akun baru
EOF
    ;;
esac
