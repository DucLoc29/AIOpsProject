#!/usr/bin/env bash
# Huỷ AIOps lab và xoá bridge LAN.
set -uo pipefail
cd "$(dirname "$0")/.."

sudo containerlab destroy -t topo.yml --cleanup
sudo iptables -D FORWARD -i lan-switch -o lan-switch -j ACCEPT 2>/dev/null
sudo ip link del lan-switch 2>/dev/null && echo "[-] Đã xoá bridge lan-switch"
