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

echo
echo "[✓] Deploy xong. Kiểm tra kết nối: ./scripts/verify.sh"
