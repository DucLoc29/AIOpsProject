#!/usr/bin/env bash
# =============================================================================
# Kịch bản 00: Sinh lưu lượng người dùng bình thường (Baseline)
# Mục đích: Tạo dữ liệu nền sạch cho mô hình Machine Learning (Isolation Forest)
# Nguồn: client-pc (LAN) và branch-client (Chi nhánh qua WAN)
# Đích: service-server (Nginx :80, API :5000, SSH :22)
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"
STOP=0

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 00: Normal Traffic (Thời lượng: ${DURATION}s)..."

cleanup() {
  STOP=1
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng sinh lưu lượng..."
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Luồng 1: Người dùng nội bộ (client-pc) duyệt portal và gọi API
run_lan_user() {
  while [ "$STOP" -eq 0 ]; do
    # 1. Truy cập trang chủ Web Portal
    docker exec "$P-client-pc" curl -sf -m3 "http://192.168.10.10/" >/dev/null 2>&1 || true
    sleep 0.5

    # 2. Xem danh sách nhân viên qua API (đọc DB)
    docker exec "$P-client-pc" curl -sf -m3 "http://192.168.10.10/api/employees" >/dev/null 2>&1 || true
    sleep 0.8

    # 3. Tìm kiếm nhân viên
    docker exec "$P-client-pc" curl -sf -m3 "http://192.168.10.10/api/search?q=IT" >/dev/null 2>&1 || true
    sleep 0.5

    # 4. Đăng nhập hợp lệ
    docker exec "$P-client-pc" curl -sf -m3 -X POST -H "Content-Type: application/json" \
      -d '{"username":"admin","password":"admin123"}' \
      "http://192.168.10.10/api/login" >/dev/null 2>&1 || true
    sleep 1.2

    # 5. Ping kiểm tra gateway
    docker exec "$P-client-pc" ping -c 2 -W 1 192.168.10.1 >/dev/null 2>&1 || true
    sleep 1.0
  done
}

# Luồng 2: Người dùng chi nhánh (branch-client) qua đường WAN1
run_branch_user() {
  while [ "$STOP" -eq 0 ]; do
    # 1. Truy cập trang chủ qua WAN
    docker exec "$P-branch-client" curl -sf -m3 "http://192.168.10.10/" >/dev/null 2>&1 || true
    sleep 1.0

    # 2. Tra cứu dữ liệu qua WAN
    docker exec "$P-branch-client" curl -sf -m3 "http://192.168.10.10/api/search?q=Kinh" >/dev/null 2>&1 || true
    sleep 1.5

    # 3. Ping kiểm tra Core Router và GW
    docker exec "$P-branch-client" ping -c 2 -W 1 10.0.12.2 >/dev/null 2>&1 || true
    sleep 1.0
  done
}

run_lan_user &
PID_LAN=$!
run_branch_user &
PID_BRANCH=$!

# Chạy theo thời lượng
ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ] && [ "$STOP" -eq 0 ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

STOP=1
kill "$PID_LAN" "$PID_BRANCH" 2>/dev/null || true
wait "$PID_LAN" "$PID_BRANCH" 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 00: Normal Traffic hoàn thành."
