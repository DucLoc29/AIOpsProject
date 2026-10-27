#!/usr/bin/env bash
# =============================================================================
# Kịch bản 01: Nghẽn băng thông WAN1 (WAN Congestion)
# Mục đích: Đẩy lưu lượng kịch trần trên đường WAN1 (Primary, cost 10)
# Nguồn: branch-client (172.16.1.10 - Chi nhánh)
# Đích: client-pc (192.168.10.30 - Trụ sở chính qua WAN1)
# Công cụ: iperf3 multi-stream
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 01: WAN Congestion (Thời lượng: ${DURATION}s)..."

# Khởi chạy iperf3 server trên client-pc (chạy nền)
echo "  [+] Khởi động iperf3 server trên client-pc..."
docker exec "$P-client-pc" sh -c "killall iperf3 2>/dev/null || true; nohup iperf3 -s >/dev/null 2>&1 &"
sleep 1

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng iperf3 trên cả 2 node..."
  docker exec "$P-branch-client" killall iperf3 2>/dev/null || true
  docker exec "$P-client-pc" killall iperf3 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "  [+] Bắn lưu lượng tải cao từ branch-client qua WAN1..."
# Bắn 4 luồng song song, tối đa băng thông, trong thời gian DURATION
docker exec "$P-branch-client" iperf3 -c 192.168.10.30 -t "$DURATION" -P 4 -b 0 >/dev/null 2>&1 || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 01: WAN Congestion hoàn thành."
