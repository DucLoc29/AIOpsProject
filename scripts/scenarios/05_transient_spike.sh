#!/usr/bin/env bash
# =============================================================================
# Kịch bản 05: Gai nhiễu thoáng qua thực tế (Transient Spike Burst)
# Mục đích: Bắn 1 đợt xung tải ngắn (~2 giây) vào Web/API rồi tự dừng ngay
# để kiểm chứng bộ lọc Cửa sổ trượt 5 bước & Dự báo 5m chống báo động giả.
# Nguồn: branch-client (172.16.1.10)
# Đích: service-server (192.168.10.10:80)
# =============================================================================
set -euo pipefail

DURATION="${1:-2}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 05: Transient Spike thực tế (${DURATION}s)..."

docker exec "$P-branch-client" sh -c "
  for i in \$(seq 1 18); do
    curl -sf -m1 'http://192.168.10.10/' >/dev/null 2>&1 &
    curl -sf -m1 'http://192.168.10.10/api/employees' >/dev/null 2>&1 &
  done
  wait
" || true

sleep "$DURATION"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 05: Transient Spike đã tự dừng (hệ thống trở về bình thường)."
