#!/usr/bin/env bash
# =============================================================================
# Kịch bản 04: Dò quét mạng nội bộ từ Insider (Internal Reconnaissance)
# Mục đích: Quét cổng tìm lỗ hổng trong LAN (East - West Traffic, không qua Firewall)
# Nguồn: client-pc (192.168.10.30 - Insider Threat)
# Đích: service-server (.10), app-server (.20), db-server (.21), firewall (.1)
# Điểm chặn tự động: Host đích (iptables trên Server, vì không đi qua Firewall)
# Công cụ: nmap SYN scan
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 04: Insider Port Scan (Thời lượng: ${DURATION}s)..."

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng nmap trên client-pc..."
  docker exec "$P-client-pc" killall nmap 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "  [+] Quét trinh sát liên tục các cổng dịch vụ trong dải LAN 192.168.10.0/24..."
STOP=0
(
  while [ "$STOP" -eq 0 ]; do
    # Quét SYN dồn dập các cổng quan trọng (22, 80, 5000, 5432, 9100...) và dải cổng 1-500
    docker exec "$P-client-pc" nmap -sS -T4 -Pn \
      -p 21,22,23,25,53,80,443,3000,5000,5432,8080,9090,9100,1-300 \
      192.168.10.1,10,20,21 >/dev/null 2>&1 || true
    sleep 1
  done
) &
SCAN_PID=$!

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

STOP=1
kill "$SCAN_PID" 2>/dev/null || true
wait "$SCAN_PID" 2>/dev/null || true
cleanup

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 04: Insider Port Scan hoàn thành."
