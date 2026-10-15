#!/usr/bin/env bash
# Deploy AIOps lab: tạo bridge LAN trên host rồi chạy containerlab.
set -euo pipefail
cd "$(dirname "$0")/.."

BR=lan-switch

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
sudo containerlab deploy -t topo.yml --reconfigure "$@"

# 4. FRR đổi owner file cấu hình khi chạy -> trả lại quyền đọc cho user
sudo chmod 644 config/* || true

echo
echo "[✓] Deploy xong. Kiểm tra kết nối: ./scripts/verify.sh"
