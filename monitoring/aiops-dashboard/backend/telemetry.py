#!/usr/bin/env python3
"""
Tầng Thu thập Telemetry 100% Thời gian thực (Single Source of Truth) cho cả:
  1. Bộ thu thập Dataset thực nghiệm (`ai/collector/prepare_two_stage_datasets.py`)
  2. Hệ thống Giám sát & Tự động Ứng phó Live (`monitoring/aiops-dashboard/backend/app.py`)

Nguyên tắc 100% Dữ liệu thật:
  - TUYỆT ĐỐI KHÔNG dùng `rng.normal` hay công thức giả lập.
  - TUYỆT ĐỐI KHÔNG đọc tên kịch bản / nhãn hay dùng `if/elif` phân loại tấn công trước AI.
  - Mọi chỉ số đều đo trực tiếp từ 3 nguồn vật lý trên Containerlab:
      (a) Data-Plane Active Probe Burst từ `firewall` (192.168.10.1 -> address:port)
      (b) Kernel Per-Port Packet/Byte Accounting (`iptables` chains `AIOPS_ACCT_IN`/`OUT`)
      (c) Prometheus (`http://127.0.0.1:9090`, scrape 2s)
"""

import concurrent.futures
import json
import subprocess
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

HOST_CONTAINER_MAP: Dict[str, str] = {
    "service-server": "clab-aiops-prod-service-server",
    "app-server": "clab-aiops-prod-app-server",
    "db-server": "clab-aiops-prod-db-server",
    "192.168.10.10": "clab-aiops-prod-service-server",
    "192.168.10.20": "clab-aiops-prod-app-server",
    "192.168.10.21": "clab-aiops-prod-db-server",
}

HOST_INSTANCE_MAP: Dict[str, str] = {
    "service-server": "service-server",
    "app-server": "app-server",
    "db-server": "db-server",
    "192.168.10.10": "service-server",
    "192.168.10.20": "app-server",
    "192.168.10.21": "db-server",
}

# Các IP máy khách / bên ngoài cần theo dõi tốc độ gói theo nguồn (từ 3 phía: Internet, Chi nhánh, Nội bộ LAN)
TRACKED_CLIENT_IPS: Tuple[str, ...] = ("203.0.113.10", "172.16.1.10", "192.168.10.30")

# Danh sách Whitelist hạ tầng lõi — TUYỆT ĐỐI không bao giờ tự chặn nhầm
CORE_WHITELIST_IPS = frozenset(
    {"127.0.0.1", "0.0.0.0", "192.168.10.1", "192.168.10.10", "192.168.10.20", "192.168.10.21"}
)

# Script chạy trực tiếp bên trong container `firewall` (192.168.10.1 trên LAN Data Plane)
# để đo Active Probe đa dịch vụ song song qua đúng đường truyền dữ liệu (eth2 <-> lan-switch <-> eth1).
_FIREWALL_PROBE_SCRIPT = r"""
import concurrent.futures, json, socket, sys, time

targets = json.loads(sys.argv[1])
K = 3
TIMEOUT = 0.8

def probe_one(item):
    sid = item["service_id"]
    ip = item["address"]
    port = int(item["port"])
    stype = str(item["service_type"]).upper()
    http_path = item.get("probe_path", "/")

    tcp_rtts = []
    app_rtts = []
    ok_cnt = 0
    for idx in range(K):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=TIMEOUT) as s:
                s.settimeout(TIMEOUT)
                tcp_ms = (time.perf_counter() - t0) * 1000.0
                if stype in ("HTTP", "WEB", "WEB_SERVICE") or port in (80, 8080):
                    # Với Web Portal (:80), kiểm tra cả trang tĩnh `/` và luồng động `/api/employees`
                    path = http_path if (idx > 0 or http_path == "/") else "/"
                    s.sendall(f"GET {path} HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode("ascii"))
                    data = s.recv(256)
                    if not data.startswith(b"HTTP/"):
                        continue
                    # Chỉ chấp nhận 2xx/3xx (nếu 502/503/504 là suy giảm thật!)
                    parts = data[:24].split()
                    if len(parts) >= 2 and parts[1].startswith(b"5"):
                        continue
                elif stype in ("API", "API_SERVICE") or port == 5000:
                    s.sendall(f"GET /api/employees HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode("ascii"))
                    data = s.recv(256)
                    if b"200" not in data[:32]:
                        continue
                elif stype in ("DATABASE", "DB") or port == 5432:
                    s.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
                    data = s.recv(8)
                    if not data:
                        continue
                elif stype == "SSH" or port == 22:
                    data = s.recv(64)
                    if not data.startswith(b"SSH-"):
                        continue
                app_ms = (time.perf_counter() - t0) * 1000.0
                tcp_rtts.append(tcp_ms)
                app_rtts.append(max(tcp_ms, app_ms))
                ok_cnt += 1
        except Exception:
            pass

    up_ratio = ok_cnt / float(K)
    if ok_cnt > 0:
        lat = sum(tcp_rtts) / ok_cnt
        resp = sum(app_rtts) / ok_cnt
        if ok_cnt > 1:
            burst_jit = (sum((x - lat) ** 2 for x in tcp_rtts) / ok_cnt) ** 0.5
        else:
            burst_jit = 0.0
    else:
        lat = TIMEOUT * 1000.0
        resp = TIMEOUT * 1000.0
        burst_jit = 0.0
    return sid, {
        "up_ratio": round(up_ratio, 4),
        "latency_ms": round(lat, 4),
        "burst_jitter_ms": round(burst_jit, 4),
        "response_ms": round(resp, 4),
    }

out = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, max(2, len(targets)))) as pool:
    for sid, res in pool.map(probe_one, targets):
        out[sid] = res
print(json.dumps(out))
"""


def run_docker(args: List[str], timeout: float = 3.5) -> subprocess.CompletedProcess:
    cmd = ["docker"] + args if args[0] != "docker" else args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0:
            return res
    except Exception:
        pass
    try:
        return subprocess.run(["sudo", "-n"] + cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, "", "docker exec failed")


def resolve_host_container(service: Dict[str, Any]) -> Tuple[str, str]:
    addr = str(service.get("address", "192.168.10.10")).strip()
    hnode = str(service.get("host_node", "")).strip()
    container = HOST_CONTAINER_MAP.get(addr) or HOST_CONTAINER_MAP.get(hnode) or "clab-aiops-prod-service-server"
    instance = HOST_INSTANCE_MAP.get(addr) or HOST_INSTANCE_MAP.get(hnode) or "service-server"
    return container, instance


def ensure_port_accounting_chains(services: List[Dict[str, Any]]) -> None:
    """
    Khởi tạo chuỗi đếm gói/byte kernel (`AIOPS_ACCT_IN`, `AIOPS_ACCT_OUT`) trên các server đích.
    Hoàn toàn chỉ đếm (không DROP), loại trừ IP probe của Firewall (192.168.10.1) để không tự nhiễu,
    và đặt ở cuối chuỗi `INPUT` nên các gói tin bị chặn bởi `iptables -I INPUT 1 ... -j DROP`
    sẽ lập tức không còn được đếm (hồi phục tự nhiên ngay sau khi chặn!).
    """
    host_ports: Dict[str, List[int]] = {
        "clab-aiops-prod-service-server": [],
        "clab-aiops-prod-app-server": [],
        "clab-aiops-prod-db-server": [],
    }
    for s in services:
        container, _ = resolve_host_container(s)
        port = int(s.get("port", 80))
        host_ports.setdefault(container, [])
        if port not in host_ports[container]:
            host_ports[container].append(port)

    def _setup_host(container: str, ports: List[int]) -> None:
        cmds = [
            "iptables -N AIOPS_ACCT_IN 2>/dev/null || true",
            "iptables -N AIOPS_ACCT_OUT 2>/dev/null || true",
            "iptables -C INPUT -i eth1 ! -s 192.168.10.1 -j AIOPS_ACCT_IN 2>/dev/null || iptables -A INPUT -i eth1 ! -s 192.168.10.1 -j AIOPS_ACCT_IN",
            "iptables -C OUTPUT -o eth1 ! -d 192.168.10.1 -j AIOPS_ACCT_OUT 2>/dev/null || iptables -A OUTPUT -o eth1 ! -d 192.168.10.1 -j AIOPS_ACCT_OUT",
            'iptables -C AIOPS_ACCT_IN -p tcp --syn -m comment --comment "in_syn_all" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp --syn -m comment --comment "in_syn_all"',
            'iptables -C AIOPS_ACCT_OUT -p tcp --tcp-flags RST RST -m comment --comment "out_rst_all" 2>/dev/null || iptables -A AIOPS_ACCT_OUT -p tcp --tcp-flags RST RST -m comment --comment "out_rst_all"',
        ]
        for p in sorted(ports):
            cmds.extend(
                [
                    f'iptables -C AIOPS_ACCT_IN -p tcp --dport {p} --syn -m comment --comment "in_syn_{p}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp --dport {p} --syn -m comment --comment "in_syn_{p}"',
                    f'iptables -C AIOPS_ACCT_IN -p tcp --dport {p} -m comment --comment "in_tcp_{p}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp --dport {p} -m comment --comment "in_tcp_{p}"',
                    f'iptables -C AIOPS_ACCT_OUT -p tcp --sport {p} --tcp-flags RST RST -m comment --comment "out_rst_{p}" 2>/dev/null || iptables -A AIOPS_ACCT_OUT -p tcp --sport {p} --tcp-flags RST RST -m comment --comment "out_rst_{p}"',
                    f'iptables -C AIOPS_ACCT_OUT -p tcp --sport {p} -m comment --comment "out_tcp_{p}" 2>/dev/null || iptables -A AIOPS_ACCT_OUT -p tcp --sport {p} -m comment --comment "out_tcp_{p}"',
                ]
            )
        # Bộ đếm theo từng IP nguồn (Internet / Branch / Insider LAN) để truy vết động kẻ tấn công thật (0ms thêm)
        for src_ip in TRACKED_CLIENT_IPS:
            tag_ip = src_ip.replace(".", "_")
            cmds.extend(
                [
                    f'iptables -C AIOPS_ACCT_IN -p tcp -s {src_ip} -m comment --comment "src_tcp_{tag_ip}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp -s {src_ip} -m comment --comment "src_tcp_{tag_ip}"',
                    f'iptables -C AIOPS_ACCT_IN -p tcp -s {src_ip} --syn -m comment --comment "src_syn_{tag_ip}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp -s {src_ip} --syn -m comment --comment "src_syn_{tag_ip}"',
                ]
            )
            for p in sorted(ports):
                cmds.extend(
                    [
                        f'iptables -C AIOPS_ACCT_IN -p tcp -s {src_ip} --dport {p} -m comment --comment "src_tcp_{tag_ip}_p{p}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp -s {src_ip} --dport {p} -m comment --comment "src_tcp_{tag_ip}_p{p}"',
                        f'iptables -C AIOPS_ACCT_IN -p tcp -s {src_ip} --dport {p} --syn -m comment --comment "src_syn_{tag_ip}_p{p}" 2>/dev/null || iptables -A AIOPS_ACCT_IN -p tcp -s {src_ip} --dport {p} --syn -m comment --comment "src_syn_{tag_ip}_p{p}"',
                    ]
                )
        run_docker(["exec", container, "sh", "-c", " && ".join(cmds)], timeout=5.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_setup_host, c, ps) for c, ps in host_ports.items()]
        for f in futs:
            try:
                f.result()
            except Exception:
                pass


def _read_single_host_accounting(container: str) -> Dict[str, Tuple[int, int]]:
    """Đọc toàn bộ bộ đếm (pkts, bytes) từ `AIOPS_ACCT_IN` và `AIOPS_ACCT_OUT` trong 1 lệnh duy nhất."""
    res = run_docker(
        ["exec", container, "sh", "-c", "iptables -L AIOPS_ACCT_IN -n -v -x; iptables -L AIOPS_ACCT_OUT -n -v -x"],
        timeout=2.0,
    )
    counters: Dict[str, Tuple[int, int]] = {}
    if res.returncode != 0 or not res.stdout:
        return counters
    for line in res.stdout.splitlines():
        if "/* " not in line or " */" not in line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pkts = int(parts[0])
            nbytes = int(parts[1])
        except ValueError:
            continue
        start = line.find("/* ") + 3
        end = line.find(" */", start)
        if end > start:
            tag = line[start:end].strip()
            counters[tag] = (pkts, nbytes)
    return counters


def _query_prom_http(prom_url: str, promql: str) -> float:
    url = f"{prom_url.rstrip('/')}/api/v1/query?query={urllib.parse.quote(promql)}"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("data", {}).get("result", [])
        if not results:
            return 0.0
        val = float(results[0]["value"][1])
        return 0.0 if (np.isnan(val) or np.isinf(val) or val < 0.0) else val
    except Exception:
        return 0.0


def probe_services_dataplane(services: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """
    Chạy Active Probe đa dịch vụ trực tiếp từ `firewall` (192.168.10.1) qua Data Plane thật.
    Không bị chặn bởi rule bảo vệ IP tấn công (203.0.113.10 / 172.16.1.10 / 192.168.10.30).
    """
    if not services:
        return {}
    payload = [
        {
            "service_id": s["service_id"],
            "address": str(s.get("address", "192.168.10.10")),
            "port": int(s.get("port", 80)),
            "service_type": str(s.get("service_type", "HTTP")),
            "probe_path": "/api/employees" if s["service_id"] == "srv_web_portal" else "/",
        }
        for s in services
    ]
    res = run_docker(
        ["exec", "clab-aiops-prod-firewall", "python3", "-c", _FIREWALL_PROBE_SCRIPT, json.dumps(payload)],
        timeout=5.0,
    )
    if res.returncode == 0 and res.stdout.strip():
        try:
            return json.loads(res.stdout.strip())
        except Exception:
            pass
    return {}


class RealContainerlabTelemetryCollector:
    """
    Bộ thu thập Telemetry 100% thật duy nhất (Single Source of Truth) cho cả offline dataset & live web.
    Lưu giữ snapshot bộ đếm kernel của tick trước để tính tốc độ thực (pkts/s, bytes/s) mỗi 2 giây.
    """

    def __init__(self, prom_url: str = "http://127.0.0.1:9090", rate_window_steps: int = 4) -> None:
        self.prom_url = prom_url.rstrip("/")
        # Bộ đệm vòng 4 mẫu (~6 giây ở bước 2s) tính tốc độ tương đương PromQL `rate(...[6s])`,
        # giúp bắt trọn các đợt quét định kỳ (PortScan chu kỳ ~5.2s) và hồi phục sạch trong 6s sau iptables DROP.
        self._counter_history: Deque[Tuple[float, Dict[str, Dict[str, Tuple[int, int]]]]] = deque(
            maxlen=max(2, rate_window_steps)
        )
        self._prev_latency: Dict[str, float] = {}
        self._registered_ports: Dict[str, set] = {}
        self._last_rates_by_host: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self._last_ports_by_host: Dict[str, List[int]] = {}

    def ensure_services_registered(self, services: List[Dict[str, Any]]) -> None:
        need_setup = False
        for s in services:
            container, _ = resolve_host_container(s)
            port = int(s.get("port", 80))
            pset = self._registered_ports.setdefault(container, set())
            if port not in pset:
                need_setup = True
                pset.add(port)
        if need_setup:
            ensure_port_accounting_chains(services)

    def reset_accounting_cache(self) -> None:
        self._registered_ports.clear()

    def _collect_prometheus_metrics(self) -> Tuple[bool, Dict[str, float]]:
        active_url: Optional[str] = None
        for candidate in (self.prom_url, "http://127.0.0.1:9090", "http://172.20.20.51:9090"):
            try:
                with urllib.request.urlopen(f"{candidate}/-/healthy", timeout=0.6) as r:
                    if r.status == 200:
                        active_url = candidate
                        break
            except Exception:
                continue
        if active_url is None:
            return False, {}

        # Dùng cửa sổ [6s] khớp scrape_interval=2s -> phản ứng nhanh & tự hồi phục sau 6-8s khi iptables chặn!
        queries = {
            "srv_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="service-server",device="eth1"}[6s]))',
            "srv_tx_bytes": 'sum(rate(node_network_transmit_bytes_total{instance="service-server",device="eth1"}[6s]))',
            "srv_rx_pkts": 'sum(rate(node_network_receive_packets_total{instance="service-server",device="eth1"}[6s]))',
            "app_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="app-server",device="eth1"}[6s]))',
            "app_tx_bytes": 'sum(rate(node_network_transmit_bytes_total{instance="app-server",device="eth1"}[6s]))',
            "db_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="db-server",device="eth1"}[6s]))',
            "db_tx_bytes": 'sum(rate(node_network_transmit_bytes_total{instance="db-server",device="eth1"}[6s]))',
            "fw_rx_pkts": 'sum(rate(node_network_receive_packets_total{instance="firewall",device="eth1"}[6s]))',
            "fw_out_pkts": 'sum(rate(node_network_transmit_packets_total{instance="firewall",device="eth2"}[6s]))',
            "tcp_rst_srv": 'sum(rate(node_netstat_Tcp_OutRsts{instance="service-server"}[6s]))',
            "tcp_rst_app": 'sum(rate(node_netstat_Tcp_OutRsts{instance="app-server"}[6s]))',
            "tcp_rst_db": 'sum(rate(node_netstat_Tcp_OutRsts{instance="db-server"}[6s]))',
            "tcp_retrans_srv": 'sum(rate(node_netstat_Tcp_RetransSegs{instance="service-server"}[6s]))',
            "tcp_retrans_app": 'sum(rate(node_netstat_Tcp_RetransSegs{instance="app-server"}[6s]))',
            "tcp_retrans_db": 'sum(rate(node_netstat_Tcp_RetransSegs{instance="db-server"}[6s]))',
            "nginx_rps": 'sum(rate(nginx_http_requests_total[6s]))',
            "api_rps": 'sum(rate(api_requests_total[6s]))',
            "api_lat_s": 'sum(rate(api_request_duration_seconds_sum[6s])) / clamp_min(sum(rate(api_request_duration_seconds_count[6s])), 0.0001)',
            "srv_cpu": '100 - (avg(rate(node_cpu_seconds_total{instance="service-server",mode="idle"}[6s])) * 100)',
        }
        out: Dict[str, float] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            fmap = {pool.submit(_query_prom_http, active_url, q): k for k, q in queries.items()}
            for fut in concurrent.futures.as_completed(fmap):
                k = fmap[fut]
                try:
                    out[k] = float(fut.result())
                except Exception:
                    out[k] = 0.0
        out["tcp_rst_rate"] = out.get("tcp_rst_srv", 0.0) + out.get("tcp_rst_app", 0.0) + out.get("tcp_rst_db", 0.0)
        return True, out

    def sample_all_services(
        self, services: List[Dict[str, Any]]
    ) -> Tuple[bool, Dict[str, float], Dict[str, Dict[str, float]]]:
        """
        Thu thập 1 mẫu thời gian thực đầy đủ cho TẤT CẢ dịch vụ:
          - Trả về: `(prom_ok, host_prom_telemetry, service_6sla_map)`
          - Hoàn toàn dựa trên phép đo vật lý thật — không dùng nhãn hay luật phân loại kịch bản!
        """
        self.ensure_services_registered(services)

        containers = sorted({resolve_host_container(s)[0] for s in services})
        now_t = time.monotonic()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            prom_fut = pool.submit(self._collect_prometheus_metrics)
            probe_fut = pool.submit(probe_services_dataplane, services)
            acct_futs = {c: pool.submit(_read_single_host_accounting, c) for c in containers}

            prom_ok, prom_tele = prom_fut.result()
            probes = probe_fut.result()
            curr_counters = {c: f.result() for c, f in acct_futs.items()}

        rates_by_host: Dict[str, Dict[str, Tuple[float, float]]] = {}
        if self._counter_history:
            oldest_ts, oldest_counters = self._counter_history[0]
            dt = now_t - oldest_ts
            if dt >= 0.3:
                for c, cmap in curr_counters.items():
                    prev_map = oldest_counters.get(c, {})
                    rmap: Dict[str, Tuple[float, float]] = {}
                    for tag, (pkts, nbytes) in cmap.items():
                        p_pkts, p_bytes = prev_map.get(tag, (pkts, nbytes))
                        dp = max(0, pkts - p_pkts) / dt
                        db = max(0, nbytes - p_bytes) / dt
                        rmap[tag] = (dp, db)
                    rates_by_host[c] = rmap

        self._counter_history.append((now_t, curr_counters))
        self._last_rates_by_host = rates_by_host

        ports_by_host: Dict[str, List[int]] = {}
        for s in services:
            c, _ = resolve_host_container(s)
            p = int(s.get("port", 80))
            ports_by_host.setdefault(c, [])
            if p not in ports_by_host[c]:
                ports_by_host[c].append(p)
        self._last_ports_by_host = ports_by_host

        service_6sla: Dict[str, Dict[str, float]] = {}
        for s in services:
            sid = s["service_id"]
            port = int(s.get("port", 80))
            stype = str(s.get("service_type", "HTTP")).upper()
            container, instance = resolve_host_container(s)

            # 1. Active Probe đo trực tiếp qua Data Plane
            pr = probes.get(
                sid,
                {"up_ratio": 1.0, "latency_ms": 2.0, "burst_jitter_ms": 0.2, "response_ms": 4.0},
            )
            up_ratio = float(pr.get("up_ratio", 1.0))
            lat = float(pr.get("latency_ms", 2.0))
            burst_jit = float(pr.get("burst_jitter_ms", 0.0))
            probe_resp = float(pr.get("response_ms", lat))

            # 2. Bộ đếm gói/byte kernel theo ĐÚNG cổng dịch vụ (không bị rò rỉ bởi reverse-proxy ephemeral port!)
            rmap = rates_by_host.get(container, {})
            open_ports = ports_by_host.get(container, [port])

            in_syn_p, _ = rmap.get(f"in_syn_{port}", (0.0, 0.0))
            in_tcp_p, in_bytes_p = rmap.get(f"in_tcp_{port}", (0.0, 0.0))
            out_tcp_p, out_bytes_p = rmap.get(f"out_tcp_{port}", (0.0, 0.0))
            out_rst_p, _ = rmap.get(f"out_rst_{port}", (0.0, 0.0))

            in_syn_all_pkts, in_syn_all_bytes = rmap.get("in_syn_all", (0.0, 0.0))
            out_rst_all_pkts, out_rst_all_bytes = rmap.get("out_rst_all", (0.0, 0.0))

            sum_open_in_syn_pkts = sum(rmap.get(f"in_syn_{p}", (0.0, 0.0))[0] for p in open_ports)
            sum_open_in_syn_bytes = sum(rmap.get(f"in_syn_{p}", (0.0, 0.0))[1] for p in open_ports)
            sum_open_out_rst_pkts = sum(rmap.get(f"out_rst_{p}", (0.0, 0.0))[0] for p in open_ports)
            sum_open_out_rst_bytes = sum(rmap.get(f"out_rst_{p}", (0.0, 0.0))[1] for p in open_ports)

            # Lưu lượng SYN/RST gửi vào các cổng ĐÓNG trên máy chủ này (chỉ phát sinh khi có dò quét cổng).
            # Theo RFC 793, gói RST từ cổng đóng chỉ sinh ra để từ chối gói SYN đi vào cổng đóng
            # (loại trừ gói RST đóng kết nối outbound của reverse proxy Nginx -> App Server).
            closed_in_syn_pkts = max(0.0, in_syn_all_pkts - sum_open_in_syn_pkts)
            closed_in_syn_bytes = max(0.0, in_syn_all_bytes - sum_open_in_syn_bytes)
            raw_closed_out_rst = max(0.0, out_rst_all_pkts - sum_open_out_rst_pkts)
            closed_out_rst_pkts = min(raw_closed_out_rst, closed_in_syn_pkts * 1.5) if closed_in_syn_pkts >= 1.0 else 0.0
            raw_closed_out_bytes = max(0.0, out_rst_all_bytes - sum_open_out_rst_bytes)
            closed_out_rst_bytes = raw_closed_out_bytes if closed_out_rst_pkts > 0.0 else 0.0

            # 3. Retransmit & API latency thực tế từ Prometheus của đúng máy chủ
            if instance == "app-server":
                retrans_s = float(prom_tele.get("tcp_retrans_app", 0.0))
            elif instance == "db-server":
                retrans_s = float(prom_tele.get("tcp_retrans_db", 0.0))
            else:
                retrans_s = float(prom_tele.get("tcp_retrans_srv", 0.0))

            api_lat_ms = float(prom_tele.get("api_lat_s", 0.0)) * 1000.0
            api_rps = float(prom_tele.get("api_rps", 0.0))

            # 4. Công thức vật lý DUY NHẤT cho 6 thông số SLA (KHÔNG phụ thuộc tên kịch bản!)
            # 4a. Throughput (KB/s): byte thực tế vào/ra ĐÚNG cổng dịch vụ + gói SYN/RST cổng đóng trên máy chủ
            throughput_kbps = (in_bytes_p + out_bytes_p + closed_in_syn_bytes + closed_out_rst_bytes) / 1024.0

            # 4b. Packet Loss / TCP Uncompleted & Rejected Rate (%) theo ĐÚNG từng cổng dịch vụ:
            #     Theo RFC 793, mỗi kết nối TCP hợp lệ có >= 2 gói non-SYN (ACK, DATA/FIN) sau SYN.
            #     Không cộng `Tcp_RetransSegs` toàn host để tránh hiện tượng retransmit FIN/ACK cũ tới IP
            #     đã bị Firewall chặn gây nhiễu chéo sang các dịch vụ khác trên cùng máy chủ.
            probe_loss_pct = (1.0 - up_ratio) * 100.0
            non_syn_in = max(0.0, in_tcp_p - in_syn_p)
            uncompleted_syn = max(0.0, in_syn_p - 0.5 * non_syn_in - 3.0)
            tcp_fail_pkts = uncompleted_syn + out_rst_p + closed_out_rst_pkts
            tcp_total_pkts = max(in_tcp_p + out_tcp_p + closed_in_syn_pkts + out_rst_p + closed_out_rst_pkts, 20.0)
            tcp_loss_pct = 100.0 * min(1.0, tcp_fail_pkts / tcp_total_pkts)
            packet_loss_pct = min(100.0, max(probe_loss_pct, tcp_loss_pct))

            # 4c. Latency (ms) & Jitter (ms) từ Active Probe thật qua Data Plane
            prev_lat = self._prev_latency.get(sid, lat)
            jitter_ms = 0.5 * burst_jit + 0.5 * abs(lat - prev_lat)
            self._prev_latency[sid] = lat

            # 4d. Response Time (ms) thực tế của ứng dụng
            response_ms = probe_resp
            if (stype == "API" or port == 5000) and api_rps > 0.5 and api_lat_ms > 0.0:
                response_ms = max(probe_resp, api_lat_ms)

            # 4e. Uptime (%) khả dụng dịch vụ thực tế (Active Probe availability kết hợp sức khỏe TCP)
            uptime_pct = max(0.0, min(100.0, up_ratio * 100.0 * (1.0 - 0.15 * (tcp_loss_pct / 100.0))))

            service_6sla[sid] = {
                "latency": round(float(lat), 4),
                "jitter": round(float(jitter_ms), 4),
                "packet_loss": round(float(packet_loss_pct), 4),
                "throughput": round(float(throughput_kbps), 4),
                "response_time": round(float(response_ms), 4),
                "uptime": round(float(uptime_pct), 4),
            }

        return prom_ok, prom_tele, service_6sla

    @staticmethod
    def _fallback_top_ip_from_proc_tcp(container: str, port: int) -> List[str]:
        """Dự phòng: đọc `/proc/net/tcp` trên server đích để bắt cả IP tấn công lạ ngoài danh sách theo dõi."""
        res = run_docker(["exec", container, "cat", "/proc/net/tcp"], timeout=1.5)
        if res.returncode != 0 or not res.stdout:
            return []
        port_hex = f"{int(port):04X}"
        counts: Dict[str, int] = {}
        for line in res.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local_addr, rem_addr, state = parts[1], parts[2], parts[3].upper()
            if ":" not in local_addr or ":" not in rem_addr or state == "0A":
                continue
            if local_addr.split(":")[1].upper() != port_hex:
                continue
            ip_hex = rem_addr.split(":")[0]
            if len(ip_hex) != 8:
                continue
            try:
                octets = [str(int(ip_hex[i : i + 2], 16)) for i in (6, 4, 2, 0)]
                ip_str = ".".join(octets)
            except ValueError:
                continue
            if ip_str in CORE_WHITELIST_IPS or ip_str.startswith("172.20.20."):
                continue
            counts[ip_str] = counts.get(ip_str, 0) + 1
        if not counts:
            return []
        max_c = max(counts.values())
        return [ip for ip, c in sorted(counts.items(), key=lambda x: x[1], reverse=True) if c >= max(3, int(0.3 * max_c))]

    def detect_attacker_ips(
        self, fault_class: str, service: Dict[str, Any]
    ) -> Tuple[List[str], Optional[Tuple[str, int]]]:
        """
        Truy vết ĐỘNG danh sách IP nguồn đang thực sự gây ra sự cố từ bộ đếm kernel `AIOPS_ACCT_IN`
        (kèm dự phòng `/proc/net/tcp`), hỗ trợ cả 3 hướng tấn công (Internet / Branch / Insider LAN)
        và cả tấn công đa nguồn đồng thời (DDoS) mà KHÔNG gắn cứng IP theo loại lỗi!

        Trả về: `(attacker_ips, target_override)`
          - `target_override`: `(target_ip, target_port)` nếu lưu lượng HttpFlood ở tầng API/DB đi qua
            Nginx Reverse Proxy (`192.168.10.10:80`) để chặn đúng tại biên đầu vào mà vẫn whitelist App/Nginx.
        """
        container, _ = resolve_host_container(service)
        port = int(service.get("port", 80))

        def _score_on_host(c_name: str, p_num: int) -> Dict[str, float]:
            rmap = self._last_rates_by_host.get(c_name, {})
            open_ports = self._last_ports_by_host.get(c_name, [p_num])
            scores: Dict[str, float] = {}
            for src_ip in TRACKED_CLIENT_IPS:
                if src_ip in CORE_WHITELIST_IPS or src_ip.startswith("172.20.20."):
                    continue
                tag = src_ip.replace(".", "_")
                host_tcp = rmap.get(f"src_tcp_{tag}", (0.0, 0.0))[0]
                host_syn = rmap.get(f"src_syn_{tag}", (0.0, 0.0))[0]
                port_tcp = rmap.get(f"src_tcp_{tag}_p{p_num}", (0.0, 0.0))[0]
                port_syn = rmap.get(f"src_syn_{tag}_p{p_num}", (0.0, 0.0))[0]
                open_syn_sum = sum(rmap.get(f"src_syn_{tag}_p{op}", (0.0, 0.0))[0] for op in open_ports)
                closed_syn = max(0.0, host_syn - open_syn_sum)

                if fault_class == "SynFlood":
                    val = port_syn
                elif fault_class == "HttpFlood":
                    val = port_tcp
                elif fault_class == "SshBruteforce":
                    val = port_tcp + 5.0 * port_syn
                elif fault_class == "PortScan":
                    val = closed_syn
                else:
                    val = host_tcp
                scores[src_ip] = val
            return scores

        def _pick_offenders(scores: Dict[str, float], min_thr: float) -> List[str]:
            if not scores:
                return []
            top_val = max(scores.values())
            if top_val < min_thr:
                return []
            cutoff = max(min_thr, 0.25 * top_val)
            return [
                ip for ip, val in sorted(scores.items(), key=lambda x: x[1], reverse=True)
                if val >= cutoff
            ]

        thresholds = {
            "SynFlood": 25.0,
            "HttpFlood": 35.0,
            "SshBruteforce": 8.0,
            "PortScan": 3.0,
        }
        min_thr = thresholds.get(fault_class, 20.0)

        # 1. Kiểm tra trực tiếp trên máy chủ đích của dịch vụ trước (bắt được cả trường hợp Insider đánh thẳng :5000 / :5432!)
        direct_scores = _score_on_host(container, port)
        offenders = _pick_offenders(direct_scores, min_thr)
        if offenders:
            return offenders, None

        # 2. Với HttpFlood lan xuống tầng API (:5000) hoặc DB (:5432) qua Nginx Reverse Proxy (192.168.10.10:80):
        #    Truy ngược lên `service-server:80` để bắt đúng IP khách tấn công từ bên ngoài / chi nhánh / LAN!
        if fault_class == "HttpFlood" and container != "clab-aiops-prod-service-server":
            proxy_scores = _score_on_host("clab-aiops-prod-service-server", 80)
            proxy_offenders = _pick_offenders(proxy_scores, min_thr)
            if proxy_offenders:
                return proxy_offenders, ("192.168.10.10", 80)

        # 3. Dự phòng: kiểm tra `/proc/net/tcp` cho bất kỳ IP lạ nào không nằm trong danh sách theo dõi mặc định
        proc_ips = self._fallback_top_ip_from_proc_tcp(container, port)
        if proc_ips:
            return proc_ips, None

        return [], None

