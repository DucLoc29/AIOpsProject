#!/usr/bin/env bash
# Demo kịch bản sự cố: đứt WAN1 -> OSPF chuyển sang WAN2 -> khôi phục WAN1.
P=clab-aiops-prod-branch-router
DST=${1:-192.168.10.0/24}
R() { sudo docker exec $P vtysh -c "show ip route $DST" 2>/dev/null | grep '\*'; }

echo "[1] Ban đầu:";            R
sudo docker exec $P ip link set eth2 down
sleep 2
echo "[2] Cắt WAN1 (eth2):";    R
sudo docker exec clab-aiops-prod-branch-client ping -c2 -W1 192.168.10.10 | tail -1

sudo docker exec $P ip link set eth2 up
for i in $(seq 1 60); do
  sleep 1
  if R | grep -q 10.0.12.2; then echo "[3] Khôi phục WAN1 sau ${i}s:"; R; exit 0; fi
done
echo "[3] WAN1 chưa khôi phục sau 60s"; exit 1
