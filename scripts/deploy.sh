#!/usr/bin/env bash
# Deploy AIOps lab: tạo bridge LAN trên host rồi chạy containerlab.
set -euo pipefail
cd "$(dirname "$0")/.."

BR=lan-switch

# 0. Xác định đường dẫn thực thi của containerlab
CLAB_BIN=""
for candidate in \
  "$(command -v containerlab 2>/dev/null || true)" \
  "$(command -v clab 2>/dev/null || true)" \
  "/usr/local/bin/containerlab" \
  "/home/${SUDO_USER:-dash}/.local/bin/containerlab" \
  "$HOME/.local/bin/containerlab"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    CLAB_BIN="$candidate"
    break
  fi
done

if [ -z "$CLAB_BIN" ]; then
  echo "[!] Không tìm thấy lệnh containerlab trong PATH hay ~/.local/bin/."
  echo "    Hãy chạy: sudo cp ~/.local/bin/containerlab /usr/local/bin/"
  exit 1
fi

# 1. Tạo Linux bridge cho LAN Switch (kind: bridge yêu cầu bridge có sẵn)
if ! ip link show "$BR" &>/dev/null; then
  echo "[+] Tạo bridge $BR"
  sudo ip link add "$BR" type bridge
fi
sudo ip link set "$BR" up

# 2. Cho phép traffic đi qua bridge (Docker đặt FORWARD policy = DROP)
sudo iptables -C FORWARD -i "$BR" -o "$BR" -j ACCEPT 2>/dev/null \
  || sudo iptables -I FORWARD -i "$BR" -o "$BR" -j ACCEPT

# 3. Deploy topology
sudo "$CLAB_BIN" deploy -t topo.yml --reconfigure "$@"

# 4. FRR đổi owner file cấu hình khi chạy -> trả lại quyền đọc cho user
sudo chmod 644 config/* || true
sudo chmod -R 755 monitoring/ app/ || true

# 5. Build frontend React nếu có npm và chưa có dist/ (dist/ đã được commit sẵn trong git)
FE=monitoring/aiops-dashboard/frontend
if [ ! -f "$FE/dist/index.html" ] && command -v npm >/dev/null 2>&1; then
  echo "[+] Build frontend React (Vite)"
  (cd "$FE" && npm install --no-audit --no-fund && npm run build) || true
fi

# 6. Khởi chạy nền AIOps Command Center Web Server (:5000)
pkill -f "aiops-dashboard/backend/app.py" 2>/dev/null || true
sudo rm -f /tmp/aiops_dashboard.log 2>/dev/null || rm -f /tmp/aiops_dashboard.log 2>/dev/null || true
PY_BIN=""
for py_candidate in ".venv/bin/python" ".venv/bin/python3" "venv/bin/python" "venv/bin/python3"; do
  if [ -x "$py_candidate" ]; then
    PY_BIN="$py_candidate"
    break
  fi
done
if [ -n "$PY_BIN" ]; then
  nohup "$PY_BIN" monitoring/aiops-dashboard/backend/app.py </dev/null >/tmp/aiops_dashboard.log 2>&1 &
else
  USER_SITE=$(find "/home/${SUDO_USER:-$USER}/.local/lib" -maxdepth 2 -name "site-packages" 2>/dev/null | tr '\n' ':' || true)
  PYTHONPATH="${USER_SITE}${PYTHONPATH:-}" nohup python3 monitoring/aiops-dashboard/backend/app.py </dev/null >/tmp/aiops_dashboard.log 2>&1 &
fi

echo
echo "[✓] Deploy xong (Lab + AIOps Command Center tại http://localhost:5000). Kiểm tra: ./scripts/verify.sh"
