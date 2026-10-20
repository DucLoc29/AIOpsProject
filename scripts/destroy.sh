#!/usr/bin/env bash
# Huỷ AIOps lab và xoá bridge LAN.
set -uo pipefail
cd "$(dirname "$0")/.."

CLAB_BIN=""
for candidate in \
  "$(command -v containerlab 2>/dev/null || true)" \
  "$(command -v clab 2>/dev/null || true)" \
  "/usr/local/bin/containerlab" \
  "/home/${SUDO_USER:-dash}/.local/bin/containerlab" \
  "$HOME/.local/bin/containerlab"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    CLAB_BIN="$candidate"
    break
  fi
done

if [ -n "$CLAB_BIN" ]; then
  sudo "$CLAB_BIN" destroy -t topo.yml --cleanup
fi

sudo iptables -D FORWARD -i lan-switch -o lan-switch -j ACCEPT 2>/dev/null
sudo ip link del lan-switch 2>/dev/null && echo "[-] Đã xoá bridge lan-switch"
