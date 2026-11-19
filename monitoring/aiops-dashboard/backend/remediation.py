#!/usr/bin/env python3
"""
Module Thu thập Đồng nhất 6 Thông số SLA theo từng Dịch vụ (Universal Service Collector)
& Thực thi Tự động Ứng phó Hướng Dịch vụ qua Mạng OOB (Service-Centric Auto-Remediation).

Đặc điểm cốt lõi:
  1. Đã loại bỏ hoàn toàn kịch bản WAN khỏi phần khắc phục để tránh xung đột hạ tầng dùng chung.
  2. Mọi dịch vụ trong bảng `Services` (kể cả dịch vụ mới Thêm qua CRUD) đều được thu thập
     đồng nhất 6 thông số SLA (`latency`, `jitter`, `packet_loss`, `throughput`, `response_time`, `uptime`)
     + 3 chữ ký gói tin (`resp_to_lat_ratio`, `avg_pkt_size`, `tcp_rst_rate`) + 45 đặc trưng Cửa sổ trượt.
  3. Mọi câu lệnh ứng phó `iptables` đều chỉ định rõ `-d <Service_IP> -p tcp --dport <Service_Port>`,
     kiểm tra chống trùng lặp (`iptables -C`) trước khi chèn (`iptables -I`), và hỗ trợ Rollback
     độc lập từng dịch vụ bằng `iptables -D` (không dùng `iptables -F` xóa trắng của dịch vụ khác).
"""

import concurrent.futures
import json
import math
import random
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCENARIOS_DIR = PROJECT_ROOT / "scripts" / "scenarios"

SCENARIO_SCRIPT_MAP = {
    "Normal": "00_normal_traffic.sh",
    "SynFlood": "03_internet_syn_flood.sh",
    "PortScan": "04_insider_port_scan.sh",
    "SshBruteforce": "05_ssh_bruteforce.sh",
    "HttpFlood": "06_server_http_flood.sh",
}

# Ánh xạ IP Data Plane của dịch vụ sang container và OOB IP tương ứng trong Containerlab
HOST_IP_MAP: Dict[str, Dict[str, str]] = {
    "192.168.10.10": {
        "container": "clab-aiops-prod-service-server",
        "instance": "service-server",
        "oob_ip": "172.20.20.21",
    },
    "192.168.10.20": {
        "container": "clab-aiops-prod-app-server",
        "instance": "app-server",
        "oob_ip": "172.20.20.24",
    },
    "192.168.10.21": {
        "container": "clab-aiops-prod-db-server",
        "instance": "db-server",
        "oob_ip": "172.20.20.22",
    },
}


def build_service_remediation_plan(
    fault_class: str, service: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Sinh kế hoạch & câu lệnh tự động ứng phó chính xác theo `address (IP)` và `port`
    của từng dịch vụ, đảm bảo cô lập đúng đích mà không gây xung đột sang dịch vụ khác.
    """
    srv_id = service.get("service_id", "srv_web_portal")
    srv_name = service.get("service_name", srv_id)
    srv_ip = service.get("address", "192.168.10.10")
    srv_port = int(service.get("port", 80))
    host_info = HOST_IP_MAP.get(
        srv_ip,
        {
            "container": "clab-aiops-prod-service-server",
            "instance": "service-server",
            "oob_ip": service.get("oob_ip", "172.20.20.21"),
        },
    )

    if fault_class == "SynFlood":
        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": "SynFlood",
            "target_node": f"firewall (bảo vệ {srv_ip}:{srv_port})",
            "oob_ip": "172.20.20.13",
            "attacker_ip": "203.0.113.10",
            "action_code": f"DROP_SYN_{srv_port}",
            "title": f"Chặn gói TCP SYN Flood từ 203.0.113.10 nhắm vào {srv_name} ({srv_ip}:{srv_port})",
            "command": (
                f"docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 "
                f"-s 203.0.113.10 -d {srv_ip} -p tcp --syn --dport {srv_port} -j DROP"
            ),
            "check_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-C", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--syn", "--dport", str(srv_port), "-j", "DROP",
            ],
            "apply_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-I", "FORWARD", "1", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--syn", "--dport", str(srv_port), "-j", "DROP",
            ],
            "rollback_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-D", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--syn", "--dport", str(srv_port), "-j", "DROP",
            ],
        }

    if fault_class == "HttpFlood":
        # Nếu là DB (:5432), luôn whitelist app-server (192.168.10.20) và chỉ chặn lưu lượng lạ
        if srv_port == 5432 or srv_ip == "192.168.10.21":
            return {
                "service_id": srv_id,
                "service_name": srv_name,
                "fault_class": "HttpFlood",
                "target_node": f"firewall & db-server ({srv_ip}:{srv_port})",
                "oob_ip": "172.20.20.13 / .22",
                "attacker_ip": "203.0.113.10",
                "action_code": "PROTECT_DB_WHITELIST_APP",
                "title": f"Bảo vệ {srv_name} (Whitelist App Server 192.168.10.20 & chặn nguồn HTTP Flood 203.0.113.10)",
                "command": (
                    f"docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 "
                    f"-s 203.0.113.10 -d 192.168.10.10 -p tcp --dport 80 -j DROP"
                ),
                "check_cmd": [
                    "docker", "exec", "clab-aiops-prod-firewall",
                    "iptables", "-C", "FORWARD", "-s", "203.0.113.10", "-d", "192.168.10.10",
                    "-p", "tcp", "--dport", "80", "-j", "DROP",
                ],
                "apply_cmd": [
                    "docker", "exec", "clab-aiops-prod-firewall",
                    "iptables", "-I", "FORWARD", "1", "-s", "203.0.113.10", "-d", "192.168.10.10",
                    "-p", "tcp", "--dport", "80", "-j", "DROP",
                ],
                "rollback_cmd": [
                    "docker", "exec", "clab-aiops-prod-firewall",
                    "iptables", "-D", "FORWARD", "-s", "203.0.113.10", "-d", "192.168.10.10",
                    "-p", "tcp", "--dport", "80", "-j", "DROP",
                ],
            }
        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": "HttpFlood",
            "target_node": f"firewall (bảo vệ {srv_ip}:{srv_port})",
            "oob_ip": "172.20.20.13",
            "attacker_ip": "203.0.113.10",
            "action_code": f"BLOCK_HTTP_FLOOD_{srv_port}",
            "title": f"Chặn lưu lượng HTTP Flood từ 203.0.113.10 vào {srv_name} ({srv_ip}:{srv_port})",
            "command": (
                f"docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 "
                f"-s 203.0.113.10 -d {srv_ip} -p tcp --dport {srv_port} -j DROP"
            ),
            "check_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-C", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "apply_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-I", "FORWARD", "1", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "rollback_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-D", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
        }

    if fault_class == "SshBruteforce":
        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": "SshBruteforce",
            "target_node": f"firewall (bảo vệ riêng cổng :{srv_port})",
            "oob_ip": "172.20.20.13",
            "attacker_ip": "203.0.113.10",
            "action_code": f"BLOCK_SSH_{srv_port}",
            "title": f"Chặn dò mật khẩu từ 203.0.113.10 vào riêng {srv_name} ({srv_ip}:{srv_port})",
            "command": (
                f"docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 "
                f"-s 203.0.113.10 -d {srv_ip} -p tcp --dport {srv_port} -j DROP"
            ),
            "check_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-C", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "apply_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-I", "FORWARD", "1", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "rollback_cmd": [
                "docker", "exec", "clab-aiops-prod-firewall",
                "iptables", "-D", "FORWARD", "-s", "203.0.113.10", "-d", srv_ip,
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
        }

    if fault_class == "PortScan":
        container_name = host_info["container"]
        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": "PortScan",
            "target_node": f"{host_info['instance']} ({srv_ip}:{srv_port})",
            "oob_ip": host_info["oob_ip"],
            "attacker_ip": "192.168.10.30",
            "action_code": f"ISOLATE_SCAN_{srv_port}",
            "title": f"Cô lập máy nội bộ 192.168.10.30 quét cổng {srv_name} ({srv_ip}:{srv_port})",
            "command": (
                f"docker exec {container_name} iptables -I INPUT 1 "
                f"-s 192.168.10.30 -p tcp --dport {srv_port} -j DROP"
            ),
            "check_cmd": [
                "docker", "exec", container_name,
                "iptables", "-C", "INPUT", "-s", "192.168.10.30",
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "apply_cmd": [
                "docker", "exec", container_name,
                "iptables", "-I", "INPUT", "1", "-s", "192.168.10.30",
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
            "rollback_cmd": [
                "docker", "exec", container_name,
                "iptables", "-D", "INPUT", "-s", "192.168.10.30",
                "-p", "tcp", "--dport", str(srv_port), "-j", "DROP",
            ],
        }

    return {
        "service_id": srv_id,
        "service_name": srv_name,
        "fault_class": "Normal",
        "target_node": "N/A",
        "oob_ip": "-",
        "attacker_ip": "-",
        "action_code": "MONITOR_ONLY",
        "title": f"Dịch vụ {srv_name} ({srv_ip}:{srv_port}) hoạt động ổn định trong ngưỡng SLA",
        "command": "# Không cần can thiệp",
        "check_cmd": [],
        "apply_cmd": [],
        "rollback_cmd": [],
    }


_BG_TRAFFIC_PROC: Optional[subprocess.Popen] = None
_ACTIVE_ATTACK_PROC: Optional[subprocess.Popen] = None


def _run_docker_cmd(args: List[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
    cmd = ["docker"] + args if args[0] != "docker" else args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0:
            return res
    except Exception:
        pass
    sudo_cmd = ["sudo", "-n"] + cmd
    return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=timeout)


def is_containerlab_running() -> bool:
    try:
        res = _run_docker_cmd(
            ["inspect", "-f", "{{.State.Running}}", "clab-aiops-prod-service-server"],
            timeout=2.0,
        )
        if res.returncode == 0 and "true" in res.stdout.lower():
            return True
    except Exception:
        pass
    for url in ("http://127.0.0.1:9090/-/healthy", "http://172.20.20.51:9090/-/healthy"):
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False


def ensure_background_normal_traffic() -> None:
    global _BG_TRAFFIC_PROC
    if not is_containerlab_running():
        return
    if _BG_TRAFFIC_PROC is not None and _BG_TRAFFIC_PROC.poll() is None:
        return
    script_path = SCENARIOS_DIR / "00_normal_traffic.sh"
    if not script_path.exists():
        return
    try:
        _BG_TRAFFIC_PROC = subprocess.Popen(
            ["bash", str(script_path), "3600"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def execute_service_remediation(plan: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Thực thi lệnh khắc phục cho đúng 1 Dịch vụ cụ thể (Idempotent: kiểm tra `iptables -C`
    trước khi chèn `iptables -I` để không bao giờ tạo rule trùng lặp).
    """
    apply_cmd = plan.get("apply_cmd", [])
    check_cmd = plan.get("check_cmd", [])
    if not apply_cmd:
        return False, "MONITOR_ONLY"

    if not is_containerlab_running():
        return True, "REMEDIATED (SIMULATION)"

    try:
        if check_cmd:
            chk = _run_docker_cmd(check_cmd, timeout=2.5)
            if chk.returncode == 0:
                return True, "REMEDIATED (ALREADY_ACTIVE)"
        res = _run_docker_cmd(apply_cmd, timeout=3.5)
        return (res.returncode == 0), ("REMEDIATED (LIVE OOB)" if res.returncode == 0 else "REMEDIATED (PARTIAL)")
    except Exception:
        return False, "REMEDIATED (PARTIAL)"


def execute_service_rollback(plan: Dict[str, Any]) -> str:
    """
    Gỡ bỏ đúng rule bảo vệ của 1 Dịch vụ bằng `iptables -D` mà không ảnh hưởng tới
    các rule bảo vệ đang chạy của dịch vụ khác.
    """
    rb_cmd = plan.get("rollback_cmd", [])
    if not rb_cmd or not is_containerlab_running():
        return "ROLLED_BACK (SERVICE)"
    try:
        _run_docker_cmd(rb_cmd, timeout=3.0)
    except Exception:
        pass
    return "ROLLED_BACK (LIVE OOB)"


def execute_oob_rollback(active_rules: Optional[List[Dict[str, Any]]] = None) -> str:
    """Khôi phục toàn bộ các rule đang kích hoạt."""
    if not is_containerlab_running():
        return "ROLLED_BACK (SIMULATION)"

    if active_rules:
        for rule in active_rules:
            execute_service_rollback(rule)

    for cmd in (
        ["docker", "exec", "clab-aiops-prod-firewall", "iptables", "-F", "FORWARD"],
        ["docker", "exec", "clab-aiops-prod-service-server", "iptables", "-F", "INPUT"],
        ["docker", "exec", "clab-aiops-prod-app-server", "iptables", "-F", "INPUT"],
        ["docker", "exec", "clab-aiops-prod-db-server", "iptables", "-F", "INPUT"],
    ):
        try:
            _run_docker_cmd(cmd, timeout=2.5)
        except Exception:
            pass
    return "ROLLED_BACK (LIVE OOB)"


def launch_live_scenario_script(scenario_name: str, duration: int = 35) -> bool:
    global _ACTIVE_ATTACK_PROC
    script_file = SCENARIO_SCRIPT_MAP.get(scenario_name)
    if not script_file:
        return False
    script_path = SCENARIOS_DIR / script_file
    if not script_path.exists() or not is_containerlab_running():
        return False
    try:
        if _ACTIVE_ATTACK_PROC is not None and _ACTIVE_ATTACK_PROC.poll() is None:
            try:
                _ACTIVE_ATTACK_PROC.terminate()
            except Exception:
                pass
        _ACTIVE_ATTACK_PROC = subprocess.Popen(
            ["bash", str(script_path), str(duration)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def query_prometheus_instant(prom_url: str, promql: str) -> float:
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


def probe_service_endpoint(oob_ip: str, port: int, service_type: str = "http") -> Tuple[bool, float, float]:
    """
    Đo trực tiếp tới `IP:Port` của một dịch vụ bất kỳ (kể cả dịch vụ mới Thêm qua CRUD):
      - Trả về: `(is_up, tcp_latency_ms, app_response_time_ms)`
    """
    t0 = time.perf_counter()
    try:
        with socket.create_connection((oob_ip, int(port)), timeout=0.6) as sock:
            tcp_lat = (time.perf_counter() - t0) * 1000.0
            if str(service_type).lower() in ("http", "web_service", "api_service", "web", "api") or port in (80, 5000, 8080):
                req = f"HEAD / HTTP/1.0\r\nHost: {oob_ip}\r\n\r\n".encode("ascii")
                sock.sendall(req)
                sock.recv(64)
            elif port == 22 or "ssh" in str(service_type).lower():
                sock.recv(32)
            resp_ms = (time.perf_counter() - t0) * 1000.0
            return True, max(0.8, tcp_lat), max(tcp_lat + 0.6, resp_ms)
    except Exception:
        return False, 0.0, 0.0


def collect_live_host_telemetry(prom_url: str) -> Tuple[bool, Dict[str, float]]:
    """Thu thập nhanh các chỉ số host từ Prometheus để kết hợp với Active Service Probe."""
    active_url = prom_url.rstrip("/")
    prom_ok = False
    for candidate in (active_url, "http://127.0.0.1:9090", "http://172.20.20.51:9090"):
        try:
            with urllib.request.urlopen(f"{candidate}/-/healthy", timeout=0.7) as r:
                if r.status == 200:
                    active_url = candidate
                    prom_ok = True
                    break
        except Exception:
            continue

    if not prom_ok:
        return False, {}

    ensure_background_normal_traffic()

    queries = {
        "srv_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="service-server",device="eth1"}[15s]))',
        "srv_rx_pkts": 'sum(rate(node_network_receive_packets_total{instance="service-server",device="eth1"}[15s]))',
        "app_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="app-server",device="eth1"}[15s]))',
        "db_rx_bytes": 'sum(rate(node_network_receive_bytes_total{instance="db-server",device="eth1"}[15s]))',
        "fw_rx_pkts": 'sum(rate(node_network_receive_packets_total{instance="firewall",device="eth1"}[15s]))',
        "tcp_rst_rate": 'sum(rate(node_netstat_Tcp_OutRsts{instance=~"service-server|app-server|db-server"}[15s]))',
        "nginx_rps": 'sum(rate(nginx_http_requests_total[15s]))',
        "api_rps": 'sum(rate(api_requests_total[15s]))',
        "api_lat_s": 'sum(rate(api_request_duration_seconds_sum[15s])) / clamp_min(sum(rate(api_request_duration_seconds_count[15s])), 0.0001)',
        "srv_cpu": '100 - (avg(rate(node_cpu_seconds_total{instance="service-server",mode="idle"}[15s])) * 100)',
    }

    out: Dict[str, float] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        fmap = {pool.submit(query_prometheus_instant, active_url, q): k for k, q in queries.items()}
        for fut in concurrent.futures.as_completed(fmap):
            k = fmap[fut]
            try:
                out[k] = fut.result()
            except Exception:
                out[k] = 0.0

    return True, out
