#!/usr/bin/env bash
# =============================================================================
# Kịch bản 02: Suy giảm chất lượng WAN1 (Loss & Latency - Gray Failure)
# Mục đích: Chèn trễ 200ms và rớt gói 20% trên link WAN1 (Primary, cost 10)
#          Chứng minh OSPF không tự chuyển đường khi suy hao mềm.
# Vị trí can thiệp: branch-router cổng eth2 (10.0.12.1/30)
# Công cụ: Linux Traffic Control (tc netem)
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 02: WAN Loss & Latency (Thời lượng: ${DURATION}s)..."

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Khôi phục chất lượng đường truyền WAN1 (xóa tc netem)..."
  docker exec "$P-branch-router" tc qdisc del dev eth2 root 2>/dev/null || true
  docker exec "$P-core-router" tc qdisc del dev eth2 root 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# 1. Chèn delay 200ms (+-20ms) và loss 20% trên cổng WAN1 của branch-router
echo "  [+] Kích hoạt tc netem delay 200ms loss 20% trên branch-router (eth2)..."
docker exec "$P-branch-router" tc qdisc del dev eth2 root 2>/dev/null || true
docker exec "$P-branch-router" tc qdisc add dev eth2 root netem delay 200ms 20ms loss 20%

# 2. Sinh lưu lượng liên tục để Prometheus ghi nhận được tỷ lệ drop và độ trễ cao
echo "  [+] Sinh ping và HTTP liên tục qua WAN1 trong ${DURATION}s..."
STOP=0
(
  while [ "$STOP" -eq 0 ]; do
    docker exec "$P-branch-client" ping -c 3 -W 2 10.0.12.2 >/dev/null 2>&1 || true
    docker exec "$P-branch-client" curl -sf -m5 "http://192.168.10.10/" >/dev/null 2>&1 || true
    sleep 1
  done
) &
BG_PID=$!

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

STOP=1
kill "$BG_PID" 2>/dev/null || true
wait "$BG_PID" 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 02: WAN Loss & Latency hoàn thành."
