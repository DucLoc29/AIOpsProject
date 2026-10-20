#!/usr/bin/env bash
# Kiểm tra end-to-end AIOps lab: OSPF, định tuyến, ping, HTTP, công cụ và giám sát (Prometheus/Grafana).
cd "$(dirname "$0")/.."
P=clab-aiops-prod
PASS=0; FAIL=0

ok()   { echo -e "  \e[32m[PASS]\e[0m $1"; PASS=$((PASS+1)); }
bad()  { echo -e "  \e[31m[FAIL]\e[0m $1"; FAIL=$((FAIL+1)); }
dx()   { sudo docker exec "$P-$1" sh -c "$2" >/dev/null 2>&1; }

ping_test() { # node target label
  dx "$1" "ping -c2 -W1 $2" && ok "$3 ($1 -> $2)" || bad "$3 ($1 -> $2)"
}

echo "== 1. OSPF neighbors"
N=$(sudo docker exec $P-branch-router vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -c Full)
[ "$N" -eq 2 ] && ok "Branch có 2 neighbor Full (WAN1 + WAN2)" || bad "Branch chỉ có $N neighbor Full"

echo "== 2. Đường đi ưu tiên (phải qua WAN1 10.0.12.2)"
sudo docker exec $P-branch-router vtysh -c "show ip route 192.168.10.0/24" 2>/dev/null \
  | grep -q "10.0.12.2, via eth2" && ok "192.168.10.0/24 qua WAN1 Primary" || bad "192.168.10.0/24 không qua WAN1"
sudo docker exec $P-branch-router vtysh -c "show ip route 203.0.113.0/24" 2>/dev/null \
  | grep -q "10.0.12.2, via eth2" && ok "203.0.113.0/24 qua WAN1 Primary" || bad "203.0.113.0/24 không qua WAN1"

echo "== 3. Kết nối chi nhánh"
ping_test branch-client 172.16.1.1     "Gateway chi nhánh"
ping_test branch-client 192.168.10.10  "Chi nhánh -> Service Server"

echo "== 4. Kết nối Internet"
ping_test internet-attacker 203.0.113.1    "Gateway Internet"
ping_test internet-attacker 192.168.10.10  "Internet -> Service Server"
ping_test internet-attacker 172.16.1.10    "Internet -> Branch Client"

echo "== 5. Kết nối LAN"
ping_test client-pc 192.168.10.1   "LAN -> Firewall"
ping_test client-pc 192.168.10.10  "LAN -> Service Server (nội bộ)"
ping_test client-pc 172.16.1.10    "LAN -> Branch Client"

echo "== 6. HTTP tới Service Server"
for n in branch-client internet-attacker client-pc; do
  dx $n "curl -sf -m3 http://192.168.10.10" && ok "HTTP từ $n" || bad "HTTP từ $n"
done

echo "== 7. Mạng quản lý OOB (Prometheus -> các node qua 172.20.20.0/24)"
for ip in 11 12 13 21 23 31 41 52 53; do
  sudo docker exec $P-prometheus wget -q --spider --timeout=1 http://172.20.20.$ip:9100/metrics 2>/dev/null \
    || sudo docker exec $P-prometheus ping -c1 -W1 172.20.20.$ip >/dev/null 2>&1 \
    && ok "OOB 172.20.20.$ip reachable" || bad "OOB 172.20.20.$ip unreachable"
done

echo "== 8. Công cụ kiểm thử bảo mật"
dx firewall "iptables -L -n" && ok "iptables trên firewall" || bad "iptables trên firewall"
for n in branch-client client-pc internet-attacker; do
  dx $n "which hping3 && which nmap && which iperf3" && ok "hping3/nmap/iperf3 trên $n" || bad "Thiếu công cụ trên $n"
done

echo "== 9. Giám sát & AIOps Pipeline (Prometheus, Alertmanager, Grafana)"
# Kiểm tra Prometheus ready
sudo docker exec $P-prometheus wget -qO- http://127.0.0.1:9090/-/ready >/dev/null 2>&1 \
  && ok "Prometheus server ready" || bad "Prometheus server chưa sẵn sàng"

# Kiểm tra Alertmanager ready
sudo docker exec $P-alertmanager wget -qO- http://127.0.0.1:9093/-/ready >/dev/null 2>&1 \
  && ok "Alertmanager server ready" || bad "Alertmanager server chưa sẵn sàng"

# Kiểm tra Grafana API health
sudo docker exec $P-grafana wget -qO- http://127.0.0.1:3000/api/health 2>/dev/null | grep -q "ok" \
  && ok "Grafana server healthy" || bad "Grafana server chưa sẵn sàng"

# Kiểm tra node_exporter trên các node (scrape status từ Prometheus API)
ACTIVE_TARGETS=$(sudo docker exec $P-prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets 2>/dev/null \
  | grep -o '"health":"up"' | wc -l)
[ "$ACTIVE_TARGETS" -ge 7 ] \
  && ok "Prometheus scrape targets active ($ACTIVE_TARGETS targets UP)" \
  || bad "Prometheus scrape targets: chỉ có $ACTIVE_TARGETS targets UP"

echo
echo "Kết quả: $PASS PASS / $FAIL FAIL"
[ "$FAIL" -eq 0 ]
