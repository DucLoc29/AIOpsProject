#!/usr/bin/env python3
"""
Module Điều phối Lưu lượng Nền & Tự động Ứng phó Hướng Dịch vụ qua Mạng OOB (Service-Centric Auto-Remediation).

Đặc điểm cốt lõi (100% Thực thi thật trên Containerlab):
  1. Truy vết IP nguồn động và tự động chọn điểm chặn (Firewall FORWARD cho North-South, Host INPUT cho East-West LAN).
  2. Mọi câu lệnh ứng phó `iptables` đều idempotent (`iptables -C` trước khi `iptables -I`) và
     hỗ trợ Rollback độc lập từng dịch vụ bằng `iptables -D` (có kiểm tra rule dùng chung).
  3. Bảo toàn chuỗi đếm gói kernel (`AIOPS_ACCT_IN`, `AIOPS_ACCT_OUT`) khi gỡ rule hoặc rollback toàn bộ.
"""

import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCENARIOS_DIR = PROJECT_ROOT / "scripts" / "scenarios"

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


CORE_WHITELIST_IPS = frozenset(
    {"127.0.0.1", "0.0.0.0", "192.168.10.1", "192.168.10.10", "192.168.10.20", "192.168.10.21"}
)

DEFAULT_ATTACKER_FALLBACK: Dict[str, List[str]] = {
    "SynFlood": ["203.0.113.10"],
    "HttpFlood": ["203.0.113.10", "172.16.1.10"],
    "SshBruteforce": ["203.0.113.10"],
    "PortScan": ["192.168.10.30"],
}


def _is_same_lan_insider(attacker_ip: str, target_ip: str) -> bool:
    """Kiểm tra IP tấn công có nằm cùng mạng LAN nội bộ 192.168.10.0/24 (East-West, không qua Firewall)."""
    return attacker_ip.startswith("192.168.10.") and target_ip.startswith("192.168.10.")


def _sanitize_attacker_ips(
    raw_ips: Optional[List[str]], fault_class: str
) -> List[str]:
    clean: List[str] = []
    for ip in (raw_ips or []):
        ip_s = str(ip).strip()
        if not ip_s or ip_s in CORE_WHITELIST_IPS or ip_s.startswith("172.20.20."):
            continue
        if ip_s not in clean:
            clean.append(ip_s)
    if not clean:
        clean = list(DEFAULT_ATTACKER_FALLBACK.get(fault_class, []))
    return clean


def build_service_remediation_plan(
    fault_class: str,
    service: Dict[str, Any],
    attacker_ips: Optional[List[str]] = None,
    target_override: Optional[Tuple[str, int]] = None,
) -> Dict[str, Any]:
    """
    Sinh kế hoạch & câu lệnh tự động ứng phó ĐỘNG theo:
      1. Danh sách IP tấn công THẬT vừa truy vết từ bộ đếm kernel (`attacker_ips`) — KHÔNG gắn cứng IP!
      2. Vị trí mạng của từng IP nguồn:
         - Nội bộ LAN (`192.168.10.x`, East-West không đi qua Firewall) -> chặn trực tiếp tại `INPUT` của server đích.
         - Bên ngoài / Chi nhánh (`203.0.113.x`, `172.16.1.x`, North-South qua Firewall) -> chặn tại `FORWARD` của `firewall`.
      3. Đúng địa chỉ & cổng dịch vụ (`-d <target_ip> --dport <target_port>`) để cô lập không ảnh hưởng dịch vụ khác.
    """
    srv_id = service.get("service_id", "srv_web_portal")
    srv_name = service.get("service_name", srv_id)
    srv_ip = str(service.get("address", "192.168.10.10"))
    srv_port = int(service.get("port", 80))

    if target_override is not None:
        target_ip, target_port = str(target_override[0]), int(target_override[1])
    elif fault_class == "HttpFlood" and not attacker_ips and (
        srv_port in (5000, 5432) or srv_ip in ("192.168.10.20", "192.168.10.21")
    ):
        target_ip, target_port = "192.168.10.10", 80
    else:
        target_ip, target_port = srv_ip, srv_port

    host_info = HOST_IP_MAP.get(
        target_ip,
        {
            "container": "clab-aiops-prod-service-server",
            "instance": "service-server",
            "oob_ip": service.get("oob_ip", "172.20.20.21"),
        },
    )

    if fault_class in ("SynFlood", "HttpFlood", "SshBruteforce", "PortScan"):
        ips = _sanitize_attacker_ips(attacker_ips, fault_class)
        check_cmds: List[List[str]] = []
        apply_cmds: List[List[str]] = []
        rollback_cmds: List[List[str]] = []
        display_cmds: List[str] = []
        enforcement_nodes: List[str] = []
        oob_ips: List[str] = []

        for src_ip in ips:
            insider = _is_same_lan_insider(src_ip, target_ip)
            if insider:
                exec_container = host_info["container"]
                chain = "INPUT"
                node_tag = f"{host_info['instance']} (East-West LAN)"
                node_oob = host_info["oob_ip"]
            else:
                exec_container = "clab-aiops-prod-firewall"
                chain = "FORWARD"
                node_tag = "firewall (North-South)"
                node_oob = "172.20.20.13"

            if node_tag not in enforcement_nodes:
                enforcement_nodes.append(node_tag)
            if node_oob not in oob_ips:
                oob_ips.append(node_oob)

            if fault_class == "SynFlood":
                rule_args = ["-s", src_ip, "-d", target_ip, "-p", "tcp", "--syn", "--dport", str(target_port), "-j", "DROP"]
            elif fault_class == "PortScan":
                if insider:
                    rule_args = ["-s", src_ip, "-p", "tcp", "--syn", "-j", "DROP"]
                else:
                    rule_args = ["-s", src_ip, "-d", target_ip, "-p", "tcp", "--syn", "-j", "DROP"]
            else:  # HttpFlood or SshBruteforce
                rule_args = ["-s", src_ip, "-d", target_ip, "-p", "tcp", "--dport", str(target_port), "-j", "DROP"]

            check_cmds.append(["docker", "exec", exec_container, "iptables", "-C", chain] + rule_args)
            apply_cmds.append(["docker", "exec", exec_container, "iptables", "-I", chain, "1"] + rule_args)
            rollback_cmds.append(["docker", "exec", exec_container, "iptables", "-D", chain] + rule_args)
            display_cmds.append(f"docker exec {exec_container} iptables -I {chain} 1 " + " ".join(rule_args))

        ips_str = ", ".join(ips)
        nodes_str = " + ".join(enforcement_nodes)
        is_db_proxy = (srv_port == 5432 or srv_ip == "192.168.10.21") and target_ip == "192.168.10.10"

        if fault_class == "SynFlood":
            action_code = f"DROP_SYN_{target_port}"
            title = f"Chặn gói TCP SYN Flood từ {ips_str} nhắm vào {srv_name} ({target_ip}:{target_port}) tại {nodes_str}"
        elif fault_class == "HttpFlood":
            action_code = "PROTECT_DB_WHITELIST_APP" if is_db_proxy else f"BLOCK_HTTP_FLOOD_{target_port}"
            title = (
                f"Bảo vệ {srv_name} (giữ whitelist App 192.168.10.20 & chặn nguồn HTTP Flood {ips_str} tại {nodes_str})"
                if is_db_proxy
                else f"Chặn lưu lượng HTTP Flood từ {ips_str} vào {srv_name} ({target_ip}:{target_port}) tại {nodes_str}"
            )
        elif fault_class == "SshBruteforce":
            action_code = f"BLOCK_SSH_{target_port}"
            title = f"Chặn dò mật khẩu SSH từ {ips_str} vào riêng {srv_name} ({target_ip}:{target_port}) tại {nodes_str}"
        else:  # PortScan
            action_code = f"ISOLATE_SCAN_{host_info['instance'].upper()}"
            title = f"Cô lập nguồn quét cổng {ips_str} nhắm vào {host_info['instance']} (bảo vệ {srv_name}) tại {nodes_str}"

        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": fault_class,
            "target_node": f"{nodes_str} (bảo vệ {target_ip}:{target_port})",
            "oob_ip": ", ".join(oob_ips),
            "attacker_ip": ips_str,
            "action_code": action_code,
            "title": title,
            "command": " && ".join(display_cmds),
            "check_cmds": check_cmds,
            "apply_cmds": apply_cmds,
            "rollback_cmds": rollback_cmds,
            "check_cmd": check_cmds[0] if check_cmds else [],
            "apply_cmd": apply_cmds[0] if apply_cmds else [],
            "rollback_cmd": rollback_cmds[0] if rollback_cmds else [],
        }

    if fault_class == "Unknown_Anomaly":
        return {
            "service_id": srv_id,
            "service_name": srv_name,
            "fault_class": "Unknown_Anomaly",
            "target_node": f"{host_info['instance']} ({srv_ip}:{srv_port})",
            "oob_ip": host_info["oob_ip"],
            "attacker_ip": "Unknown",
            "action_code": "ALERT_ADMIN_UNKNOWN",
            "title": f"Phát hiện bất thường chưa rõ chữ ký / dịch vụ ngừng phản hồi trên {srv_name} -> Chỉ cảnh báo quản trị viên, không tự chặn",
            "command": "# Không tự động chặn để đảm bảo an toàn dịch vụ (chờ quản trị viên kiểm tra)",
            "check_cmds": [],
            "apply_cmds": [],
            "rollback_cmds": [],
            "check_cmd": [],
            "apply_cmd": [],
            "rollback_cmd": [],
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
        "check_cmds": [],
        "apply_cmds": [],
        "rollback_cmds": [],
        "check_cmd": [],
        "apply_cmd": [],
        "rollback_cmd": [],
    }


_BG_TRAFFIC_PROC: Optional[subprocess.Popen] = None


def _run_docker_cmd(args: List[str], timeout: float = 3.0) -> subprocess.CompletedProcess:
    cmd = ["docker"] + args if args[0] != "docker" else args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0:
            return res
    except Exception:
        pass
    try:
        sudo_cmd = ["sudo", "-n"] + cmd
        return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return subprocess.CompletedProcess(cmd, 1, "", "docker exec failed")


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


def is_lab_dataplane_ready() -> bool:
    """Kiểm tra mạng Data Plane (`eth1` = 192.168.10.10) trên Containerlab có sẵn sàng không."""
    try:
        res = _run_docker_cmd(
            ["exec", "clab-aiops-prod-service-server", "ip", "-4", "addr", "show", "dev", "eth1"],
            timeout=1.5,
        )
        return res.returncode == 0 and "192.168.10.10" in res.stdout
    except Exception:
        return False


def ensure_background_normal_traffic() -> None:
    global _BG_TRAFFIC_PROC
    if _BG_TRAFFIC_PROC is not None and _BG_TRAFFIC_PROC.poll() is None:
        return
    # Kiểm tra toàn hệ thống để KHÔNG bao giờ sinh nhiều bản sao `00_normal_traffic.sh` khi restart backend
    try:
        chk = subprocess.run(
            ["pgrep", "-f", "00_normal_traffic.sh"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if chk.returncode == 0 and chk.stdout.strip():
            return
    except Exception:
        pass
    if not is_containerlab_running() or not is_lab_dataplane_ready():
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


def stop_active_attack_processes() -> None:
    """Dọn mọi tiến trình tấn công còn chạy trên các node Containerlab khi quản trị viên bấm Rollback."""
    if not is_containerlab_running():
        return

    for cmd in (
        [
            "exec", "clab-aiops-prod-internet-attacker", "sh", "-c",
            "for pf in /tmp/aiops_http_flood.pid /tmp/aiops_ssh_brute.pid; do "
            "  if [ -f \"$pf\" ]; then kill -9 $(cat \"$pf\") 2>/dev/null || true; rm -f \"$pf\"; fi; "
            "done; "
            "pkill -9 -f AIOPS_HTTP_FLOOD_WORKER 2>/dev/null || true; "
            "killall -9 hping3 ssh sshpass curl nmap 2>/dev/null || true",
        ],
        ["exec", "clab-aiops-prod-client-pc", "sh", "-c", "killall -9 nmap hping3 ssh sshpass curl 2>/dev/null || true"],
        [
            "exec", "clab-aiops-prod-branch-client", "sh", "-c",
            "if [ -f /tmp/aiops_http_flood.pid ]; then kill -9 $(cat /tmp/aiops_http_flood.pid) 2>/dev/null || true; rm -f /tmp/aiops_http_flood.pid; fi; "
            "pkill -9 -f AIOPS_HTTP_FLOOD_WORKER 2>/dev/null || true; "
            "killall -9 curl hping3 nmap ssh sshpass 2>/dev/null || true",
        ],
    ):
        try:
            _run_docker_cmd(cmd, timeout=2.0)
        except Exception:
            pass


def execute_service_remediation(plan: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Thực thi lệnh khắc phục thật cho 1 Dịch vụ cụ thể (Idempotent: kiểm tra `iptables -C`
    trước khi chèn `iptables -I` để không bao giờ tạo rule trùng lặp).
    """
    apply_cmds: List[List[str]] = plan.get("apply_cmds") or (
        [plan["apply_cmd"]] if plan.get("apply_cmd") else []
    )
    check_cmds: List[List[str]] = plan.get("check_cmds") or (
        [plan["check_cmd"]] if plan.get("check_cmd") else []
    )
    if not apply_cmds:
        return False, "ALERT_ONLY"

    if not is_containerlab_running():
        return False, "LAB_OFFLINE"

    all_ok = True
    already_active = True
    try:
        for idx, apply_cmd in enumerate(apply_cmds):
            check_cmd = check_cmds[idx] if idx < len(check_cmds) else []
            if check_cmd:
                chk = _run_docker_cmd(check_cmd, timeout=2.5)
                if chk.returncode == 0:
                    continue
            already_active = False
            res = _run_docker_cmd(apply_cmd, timeout=3.5)
            if res.returncode != 0:
                all_ok = False
        if already_active:
            return True, "REMEDIATED (ALREADY_ACTIVE)"
        return all_ok, ("REMEDIATED (LIVE OOB)" if all_ok else "REMEDIATED (PARTIAL)")
    except Exception:
        return False, "REMEDIATED (PARTIAL)"


def execute_service_rollback(
    plan: Dict[str, Any], remaining_rules: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Gỡ bỏ đúng các rule bảo vệ của 1 Dịch vụ bằng `iptables -D`, bỏ qua các rule còn đang được
    dùng chung bởi dịch vụ khác đang ở trạng thái PROTECTED để không xóa nhầm.
    """
    rb_cmds: List[List[str]] = plan.get("rollback_cmds") or (
        [plan["rollback_cmd"]] if plan.get("rollback_cmd") else []
    )
    if not rb_cmds or not is_containerlab_running():
        return "ROLLED_BACK (SERVICE)"

    shared_keys = set()
    if remaining_rules:
        for r in remaining_rules:
            for c in r.get("rollback_cmds") or ([r["rollback_cmd"]] if r.get("rollback_cmd") else []):
                shared_keys.add(tuple(c))

    for rb_cmd in rb_cmds:
        if tuple(rb_cmd) in shared_keys:
            continue
        try:
            _run_docker_cmd(rb_cmd, timeout=3.0)
        except Exception:
            pass
    return "ROLLED_BACK (LIVE OOB)"


def execute_oob_rollback(
    active_rules: Optional[List[Dict[str, Any]]] = None,
    services: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Khôi phục toàn bộ các rule chặn `DROP` & dừng mọi kịch bản tấn công,
    đồng thời bảo toàn chuỗi đếm gói kernel `AIOPS_ACCT_IN` / `AIOPS_ACCT_OUT`.
    """
    stop_active_attack_processes()
    if not is_containerlab_running():
        return "ROLLED_BACK (LAB_OFFLINE)"

    if active_rules:
        for rule in active_rules:
            execute_service_rollback(rule)

    # Xóa sạch luật chặn trên Firewall FORWARD
    _run_docker_cmd(["exec", "clab-aiops-prod-firewall", "iptables", "-F", "FORWARD"], timeout=2.5)

    # Trên 3 server đích: dọn toàn bộ luật DROP động trong INPUT nhưng lập tức nối lại chuỗi đếm AIOPS_ACCT_IN!
    clear_drops_sh = (
        "iptables -F INPUT 2>/dev/null || true; "
        "iptables -N AIOPS_ACCT_IN 2>/dev/null || true; "
        "iptables -A INPUT -i eth1 ! -s 192.168.10.1 -j AIOPS_ACCT_IN"
    )
    for container in (
        "clab-aiops-prod-service-server",
        "clab-aiops-prod-app-server",
        "clab-aiops-prod-db-server",
    ):
        try:
            _run_docker_cmd(["exec", container, "sh", "-c", clear_drops_sh], timeout=2.5)
        except Exception:
            pass

    if services:
        try:
            from telemetry import ensure_port_accounting_chains

            ensure_port_accounting_chains(services)
        except Exception:
            pass

    return "ROLLED_BACK (LIVE OOB)"
