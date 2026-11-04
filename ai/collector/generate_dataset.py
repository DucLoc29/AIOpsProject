#!/usr/bin/env python3
"""
Pipeline Thu thập & Gán nhãn Dataset Tự động (Giai đoạn 3 - Bước 3).

Chức năng:
  1. Duyệt qua kịch bản điều phối (Normal baseline đan xen với 6 kịch bản sự cố/tấn công)
     trong khi duy trì luồng lưu lượng nền (Normal traffic) như môi trường thực tế.
  2. Ghi nhận chính xác mốc thời gian (Unix timestamp) của từng giai đoạn.
  3. Truy vấn REST API của Prometheus (/api/v1/query_range) với bước nhảy 5s
     để trích xuất 30 đặc trưng (features) đa tầng:
       - Tầng Đường truyền WAN (WAN1 Primary, WAN2 Backup): thông lượng, gói tin, drops, queue
       - Tầng Biên & Firewall: thông lượng, gói tin, CPU
       - Tầng Mạng nội bộ & Host: lưu lượng LAN, TCP SYN/RST/Retrans, CPU
       - Tầng Ứng dụng (Nginx, Flask API, Database): connections, HTTP RPS, API latency, lỗi
  4. Gán nhãn nhị phân (is_anomaly: 0/1) và nhãn đa lớp (label: 7 lớp).
  5. Xuất dữ liệu ra dataset/aiops_network_dataset.csv.

Cách chạy:
  - Thu thập đầy đủ (~14 phút):
      python3 collector/generate_dataset.py
  - Thu thập nhanh để kiểm thử (~4.5 phút):
      python3 collector/generate_dataset.py --normal-duration 25 --attack-duration 25
  - Chỉ trích xuất lại CSV từ mốc thời gian đã chạy (nếu lưu file timeline):
      python3 collector/generate_dataset.py --from-timeline dataset/timeline.json
"""

import argparse
import csv
import json
import math
import os
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AI_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = AI_DIR.parent
SCENARIOS_DIR = PROJECT_ROOT / "scripts" / "scenarios"
DATASET_DIR = AI_DIR / "dataset"
DEFAULT_CSV_PATH = DATASET_DIR / "aiops_network_dataset.csv"
DEFAULT_TIMELINE_PATH = DATASET_DIR / "timeline.json"

PROMPT_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
PROMETHEUS_URL = PROMPT_URL
STEP_SECONDS = 1

# =============================================================================
# ĐỊNH NGHĨA 38 ĐẶC TRƯNG (FEATURES) PROMQL ĐA TẦNG
# Sử dụng rate(...[15s]) với scrape_interval=5s (3 mẫu/cửa sổ) giúp phản ứng
# tức thời với các đợt tấn công và suy giảm hiệu năng mạng.
# =============================================================================
FEATURE_QUERIES: List[Tuple[str, str]] = [
    # --- 1. Tầng Đường truyền WAN (Branch Router <-> Core Router) ---
    (
        "wan1_rx_bytes_rate",
        'sum(rate(node_network_receive_bytes_total{instance="core-router",device="eth2"}[15s]))',
    ),
    (
        "wan1_tx_bytes_rate",
        'sum(rate(node_network_transmit_bytes_total{instance="branch-router",device="eth2"}[15s]))',
    ),
    (
        "wan1_rx_pkts_rate",
        'sum(rate(node_network_receive_packets_total{instance="core-router",device="eth2"}[15s]))',
    ),
    (
        "wan1_tx_pkts_rate",
        'sum(rate(node_network_transmit_packets_total{instance="branch-router",device="eth2"}[15s]))',
    ),
    (
        "wan1_drops_rate",
        'sum(rate(node_network_transmit_drop_total{instance=~"branch-router|core-router",device="eth2"}[15s]))'
        ' + sum(rate(node_network_receive_drop_total{instance=~"branch-router|core-router",device="eth2"}[15s]))',
    ),
    (
        "wan1_tx_queue_len",
        'sum(node_network_transmit_queue_length{instance="branch-router",device="eth2"})',
    ),
    (
        "wan2_rx_bytes_rate",
        'sum(rate(node_network_receive_bytes_total{instance="core-router",device="eth3"}[15s]))',
    ),
    (
        "wan2_tx_bytes_rate",
        'sum(rate(node_network_transmit_bytes_total{instance="branch-router",device="eth3"}[15s]))',
    ),
    (
        "branch_tcp_retrans_rate",
        'sum(rate(node_netstat_Tcp_RetransSegs{instance=~"branch-client|branch-router|service-server|app-server"}[15s]))',
    ),
    (
        "wan_tcp_syn_retrans_rate",
        'sum(rate(node_netstat_TcpExt_TCPSynRetrans{instance=~"branch-client|service-server|app-server"}[15s]))',
    ),
    (
        "wan_tcp_timeouts_rate",
        'sum(rate(node_netstat_TcpExt_TCPTimeouts{instance=~"branch-client|service-server|app-server"}[15s]))',
    ),
    (
        "wan_icmp_rate",
        'sum(rate(node_netstat_Icmp_InMsgs{instance=~"branch-router|core-router|service-server"}[15s]))',
    ),
    # --- 2. Tầng Biên Internet & Firewall ---
    (
        "internet_rx_pkts_rate",
        'sum(rate(node_network_receive_packets_total{instance="core-router",device="eth4"}[15s]))',
    ),
    (
        "internet_rx_bytes_rate",
        'sum(rate(node_network_receive_bytes_total{instance="core-router",device="eth4"}[15s]))',
    ),
    (
        "fw_rx_bytes_rate",
        'sum(rate(node_network_receive_bytes_total{instance="firewall",device="eth1"}[15s]))',
    ),
    (
        "fw_rx_pkts_rate",
        'sum(rate(node_network_receive_packets_total{instance="firewall",device="eth1"}[15s]))',
    ),
    (
        "fw_tx_pkts_rate",
        'sum(rate(node_network_transmit_packets_total{instance="firewall",device="eth2"}[15s]))',
    ),
    (
        "fw_drops_rate",
        'sum(rate(node_network_receive_drop_total{instance="firewall",device=~"eth1|eth2"}[15s]))'
        ' + sum(rate(node_network_transmit_drop_total{instance="firewall",device=~"eth1|eth2"}[15s]))',
    ),
    (
        "fw_cpu_usage",
        '100 - (avg(rate(node_cpu_seconds_total{instance="firewall",mode="idle"}[15s])) * 100)',
    ),
    # --- 3. Tầng Mạng Nội bộ LAN & Máy chủ Đích (East-West & Host Stack) ---
    (
        "client_lan_tx_pkts_rate",
        'sum(rate(node_network_transmit_packets_total{instance="client-pc",device="eth1"}[15s]))',
    ),
    (
        "client_lan_tx_bytes_rate",
        'sum(rate(node_network_transmit_bytes_total{instance="client-pc",device="eth1"}[15s]))',
    ),
    (
        "client_tcp_active_opens_rate",
        'sum(rate(node_netstat_Tcp_ActiveOpens{instance="client-pc"}[15s]))',
    ),
    (
        "srv_rx_bytes_rate",
        'sum(rate(node_network_receive_bytes_total{instance="service-server",device="eth1"}[15s]))',
    ),
    (
        "srv_rx_pkts_rate",
        'sum(rate(node_network_receive_packets_total{instance="service-server",device="eth1"}[15s]))',
    ),
    (
        "srv_tx_pkts_rate",
        'sum(rate(node_network_transmit_packets_total{instance="service-server",device="eth1"}[15s]))',
    ),
    (
        "srv_tcp_passive_opens_rate",
        'sum(rate(node_netstat_Tcp_PassiveOpens{instance="service-server"}[15s]))',
    ),
    (
        "srv_tcp_out_rsts_rate",
        'sum(rate(node_netstat_Tcp_OutRsts{instance=~"service-server|app-server|db-server|firewall"}[15s]))',
    ),
    (
        "srv_tcp_in_segs_rate",
        'sum(rate(node_netstat_Tcp_InSegs{instance="service-server"}[15s]))',
    ),
    (
        "srv_tcp_estab",
        'sum(node_netstat_Tcp_CurrEstab{instance="service-server"})',
    ),
    (
        "srv_tcp_tw",
        'sum(node_sockstat_TCP_tw{instance="service-server"})',
    ),
    (
        "srv_cpu_usage",
        '100 - (avg(rate(node_cpu_seconds_total{instance="service-server",mode="idle"}[15s])) * 100)',
    ),
    # --- 4. Tầng Ứng dụng (Nginx Web, Flask API, Database) ---
    (
        "nginx_active_conn",
        'sum(nginx_connections_active)',
    ),
    (
        "nginx_req_rate",
        'sum(rate(nginx_http_requests_total[15s]))',
    ),
    (
        "nginx_accepted_rate",
        'sum(rate(nginx_connections_accepted[15s]))',
    ),
    (
        "api_req_rate",
        'sum(rate(api_requests_total[15s]))',
    ),
    (
        "api_latency_avg",
        'sum(rate(api_request_duration_seconds_sum[15s])) / clamp_min(sum(rate(api_request_duration_seconds_count[15s])), 0.0001)',
    ),
    (
        "app_cpu_usage",
        '100 - (avg(rate(node_cpu_seconds_total{instance="app-server",mode="idle"}[15s])) * 100)',
    ),
    (
        "db_cpu_usage",
        '100 - (avg(rate(node_cpu_seconds_total{instance="db-server",mode="idle"}[15s])) * 100)',
    ),
]

# Danh sách các kịch bản sự cố và nhãn tương ứng
ATTACK_SCENARIOS = [
    ("01_wan_congestion.sh", "WanCongestion", 1),
    ("02_wan_loss_latency.sh", "WanLossLatency", 1),
    ("03_internet_syn_flood.sh", "SynFlood", 1),
    ("04_insider_port_scan.sh", "PortScan", 1),
    ("05_ssh_bruteforce.sh", "SshBruteforce", 1),
    ("06_server_http_flood.sh", "HttpFlood", 1),
]


def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def check_prometheus_ready() -> bool:
    url = f"{PROMETHEUS_URL}/-/ready"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception as exc:
        log(f"[ERROR] Không thể kết nối Prometheus tại {PROMETHEUS_URL}: {exc}")
        return False


def query_prometheus_range(
    promql: str, start_ts: int, end_ts: int, step: int = STEP_SECONDS
) -> Dict[int, float]:
    """Truy vấn /api/v1/query_range và trả về dict {timestamp: float_value}."""
    params = urllib.parse.urlencode(
        {
            "query": promql,
            "start": start_ts,
            "end": end_ts,
            "step": f"{step}s",
        }
    )
    url = f"{PROMETHEUS_URL}/api/v1/query_range?{params}"
    result_map: Dict[int, float] = {}
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("status") != "success":
            log(f"[WARN] Query không thành công cho '{promql}': {payload}")
            return result_map
        results = payload.get("data", {}).get("result", [])
        if not results:
            return result_map
        # Lấy chuỗi thời gian đầu tiên (vì hầu hết các query đã dùng hàm sum/avg)
        for ts_raw, val_str in results[0].get("values", []):
            ts = int(float(ts_raw))
            try:
                val = float(val_str)
                if math.isnan(val) or math.isinf(val):
                    val = 0.0
            except ValueError:
                val = 0.0
            result_map[ts] = round(val, 4)
    except Exception as exc:
        log(f"[WARN] Lỗi khi truy vấn PromQL '{promql}': {exc}")
    return result_map


def run_scenario_script(script_name: str, duration: int) -> None:
    """Chạy một kịch bản bash trong scripts/scenarios/ và chờ hoàn tất."""
    script_path = SCENARIOS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Không tìm thấy kịch bản: {script_path}")

    cmd = ["bash", str(script_path), str(duration)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        preexec_fn=os.setsid,
    )
    try:
        if proc.stdout:
            for line in proc.stdout:
                print(f"    | {line.rstrip()}", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        log(f"[CLEANUP] Đang dừng kịch bản {script_name}...")
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()
        raise


def start_background_normal_traffic(total_duration: int) -> subprocess.Popen:
    """Chạy lưu lượng nền 00_normal_traffic.sh xuyên suốt quá trình thu thập."""
    script_path = SCENARIOS_DIR / "00_normal_traffic.sh"
    cmd = ["bash", str(script_path), str(total_duration)]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )


def stop_background_process(proc: Optional[subprocess.Popen]) -> None:
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            pass


def align_timestamp(ts: float, step: int = STEP_SECONDS) -> int:
    """Làm tròn timestamp về bội số của step."""
    return int(round(ts / step) * step)


def orchestrate_scenarios(
    normal_duration: int,
    attack_duration: int,
    cooldown_seconds: int,
    cycles: int,
) -> List[Dict]:
    """
    Điều phối chạy đan xen Normal baseline và 6 kịch bản sự cố.
    Trả về danh sách các đoạn thời gian (segments) đã gán nhãn.
    """
    segments: List[Dict] = []

    estimated_total = (
        cycles
        * (
            normal_duration
            + len(ATTACK_SCENARIOS) * (attack_duration + cooldown_seconds + normal_duration // 2)
        )
        + 120
    )

    log(f"Khởi chạy lưu lượng nền (00_normal_traffic.sh) xuyên suốt phiên thu thập...")
    bg_proc = start_background_normal_traffic(estimated_total)

    try:
        # Khởi động làm nóng (warm-up) 15s để bộ đếm rate()[15s] ổn định
        log("Warm-up 15s để ổn định cửa sổ rate()[15s] trên Prometheus...")
        time.sleep(15)

        for cycle in range(1, cycles + 1):
            log(f"================ CHU KỲ {cycle}/{cycles} ================")

            # 1. Pha Baseline Normal đầu chu kỳ
            log(f"[Phase] Thu thập mẫu Normal Baseline ({normal_duration}s)...")
            start_ts = align_timestamp(time.time())
            time.sleep(normal_duration)
            end_ts = align_timestamp(time.time())
            segments.append(
                {
                    "label": "Normal",
                    "is_anomaly": 0,
                    "start": start_ts,
                    "end": end_ts,
                    "cycle": cycle,
                }
            )

            # 2. Lần lượt chạy từng kịch bản sự cố/tấn công
            for idx, (script_name, label_name, is_anom) in enumerate(ATTACK_SCENARIOS, start=1):
                log(
                    f"[Phase {idx}/{len(ATTACK_SCENARIOS)}] Chạy kịch bản '{label_name}' ({script_name}) trong {attack_duration}s..."
                )
                atk_start_raw = time.time()
                run_scenario_script(script_name, attack_duration)
                atk_end_raw = time.time()

                # Bỏ 10s đầu tiên của đợt tấn công (transition ramp-up cho cửa sổ rate[15s]) để mẫu đặc trưng sắc nét
                seg_start = align_timestamp(atk_start_raw + 10)
                seg_end = align_timestamp(atk_end_raw)
                if seg_end <= seg_start:
                    seg_start = align_timestamp(atk_start_raw + 5)
                    seg_end = seg_start + STEP_SECONDS

                segments.append(
                    {
                        "label": label_name,
                        "is_anomaly": is_anom,
                        "start": seg_start,
                        "end": seg_end,
                        "cycle": cycle,
                    }
                )

                # Cooldown tối thiểu 20s sau khi dọn dẹp sự cố để cửa sổ rate()[15s] hoàn toàn sạch dư âm
                effective_cooldown = max(20, cooldown_seconds)
                log(f"  -> Cooldown {effective_cooldown}s để cửa sổ rate()[15s] về trạng thái Normal sạch...")
                time.sleep(effective_cooldown)

                # Thu thập thêm một đoạn Normal ngắn xen kẽ giữa các sự cố
                inter_normal = max(20, normal_duration // 2)
                log(f"[Phase] Thu thập mẫu Normal xen kẽ ({inter_normal}s)...")
                n_start = align_timestamp(time.time())
                time.sleep(inter_normal)
                n_end = align_timestamp(time.time())
                segments.append(
                    {
                        "label": "Normal",
                        "is_anomaly": 0,
                        "start": n_start,
                        "end": n_end,
                        "cycle": cycle,
                    }
                )

    finally:
        log("Dừng tiến trình sinh lưu lượng nền...")
        stop_background_process(bg_proc)

    return segments


def build_dataset_from_segments(
    segments: List[Dict], output_csv: Path
) -> Tuple[int, Dict[str, int]]:
    """
    Truy vấn Prometheus cho toàn bộ khoảng thời gian trong segments,
    tính thêm các đặc trưng phái sinh (derived ratios) và ghi ra file CSV.
    """
    if not segments:
        raise ValueError("Danh sách segments trống!")

    global_start = min(seg["start"] for seg in segments)
    global_end = max(seg["end"] for seg in segments)

    log(
        f"Đang trích xuất {len(FEATURE_QUERIES)} chuỗi thời gian từ Prometheus "
        f"(start={global_start}, end={global_end}, step={STEP_SECONDS}s)..."
    )

    # Truy vấn toàn bộ features
    raw_series: Dict[str, Dict[int, float]] = {}
    for feat_name, promql in FEATURE_QUERIES:
        raw_series[feat_name] = query_prometheus_range(
            promql, global_start, global_end, STEP_SECONDS
        )

    base_feature_names = [f[0] for f in FEATURE_QUERIES]
    derived_feature_names = [
        "wan1_avg_pkt_size",
        "fw_avg_pkt_size",
        "srv_avg_pkt_size",
        "client_lan_avg_pkt_size",
    ]
    all_feature_names = base_feature_names + derived_feature_names

    fieldnames = [
        "timestamp",
        "datetime",
        *all_feature_names,
        "is_anomaly",
        "label",
    ]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    label_counts: Dict[str, int] = {}
    total_rows = 0
    seen_timestamps = set()

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for seg in segments:
            label = seg["label"]
            is_anomaly = seg["is_anomaly"]
            start_ts = seg["start"]
            end_ts = seg["end"]

            for ts in range(start_ts, end_ts + 1, STEP_SECONDS):
                if ts in seen_timestamps:
                    continue
                seen_timestamps.add(ts)

                row: Dict[str, object] = {
                    "timestamp": ts,
                    "datetime": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                }

                for feat_name in base_feature_names:
                    val = raw_series[feat_name].get(ts, 0.0)
                    # Đảm bảo giá trị không âm cho các chỉ số CPU / rate
                    if val < 0.0:
                        val = 0.0
                    row[feat_name] = round(val, 4)

                # Tính 4 đặc trưng phái sinh: Kích thước gói trung bình (Bytes / Packet)
                # Cực kỳ hữu ích để phân biệt SYN Flood / Port Scan (gói nhỏ ~60B) với WAN Congestion (gói lớn ~1500B)
                row["wan1_avg_pkt_size"] = round(
                    float(row["wan1_rx_bytes_rate"])
                    / max(float(row["wan1_rx_pkts_rate"]), 1.0),
                    2,
                )
                row["fw_avg_pkt_size"] = round(
                    float(row["fw_rx_bytes_rate"])
                    / max(float(row["fw_rx_pkts_rate"]), 1.0),
                    2,
                )
                row["srv_avg_pkt_size"] = round(
                    float(row["srv_rx_bytes_rate"])
                    / max(float(row["srv_rx_pkts_rate"]), 1.0),
                    2,
                )
                row["client_lan_avg_pkt_size"] = round(
                    float(row["client_lan_tx_bytes_rate"])
                    / max(float(row["client_lan_tx_pkts_rate"]), 1.0),
                    2,
                )

                row["is_anomaly"] = is_anomaly
                row["label"] = label

                writer.writerow(row)
                total_rows += 1
                label_counts[label] = label_counts.get(label, 0) + 1

    return total_rows, label_counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestrator thu thập & gán nhãn dataset mạng AIOps từ Prometheus."
    )
    parser.add_argument(
        "--normal-duration",
        type=int,
        default=60,
        help="Thời lượng pha Normal đầu chu kỳ (giây, mặc định: 60).",
    )
    parser.add_argument(
        "--attack-duration",
        type=int,
        default=60,
        help="Thời lượng mỗi kịch bản sự cố/tấn công (giây, mặc định: 60).",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=15,
        help="Thời gian nghỉ làm sạch trạng thái giữa các kịch bản (giây, mặc định: 15).",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Số chu kỳ lặp lại toàn bộ 6 kịch bản (mặc định: 1).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Đường dẫn file CSV xuất ra (mặc định: {DEFAULT_CSV_PATH}).",
    )
    parser.add_argument(
        "--from-timeline",
        type=Path,
        default=None,
        help="Trích xuất lại CSV từ file timeline.json có sẵn (không chạy lại kịch bản).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not check_prometheus_ready():
        return 1

    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    if args.from_timeline:
        log(f"Đọc mốc thời gian từ {args.from_timeline}...")
        with args.from_timeline.open("r", encoding="utf-8") as f:
            segments = json.load(f)
    else:
        log("Bắt đầu quy trình điều phối kịch bản & thu thập dữ liệu AIOps...")
        log(
            f"Cấu hình: cycles={args.cycles}, normal={args.normal_duration}s, "
            f"attack={args.attack_duration}s, cooldown={args.cooldown}s"
        )
        segments = orchestrate_scenarios(
            normal_duration=args.normal_duration,
            attack_duration=args.attack_duration,
            cooldown_seconds=args.cooldown,
            cycles=args.cycles,
        )
        # Lưu lại timeline.json để có thể truy vấn lại bất cứ lúc nào
        with DEFAULT_TIMELINE_PATH.open("w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2, ensure_ascii=False)
        log(f"Đã lưu mốc thời gian kịch bản vào: {DEFAULT_TIMELINE_PATH}")

        # Chờ thêm 5s để Prometheus hoàn tất lần scrape cuối cùng
        time.sleep(5)

    total_rows, label_counts = build_dataset_from_segments(segments, args.output)

    print("\n" + "=" * 68)
    log(f"HOÀN TẤT THU THẬP & GÁN NHÃN DATASET!")
    log(f"File xuất ra : {args.output}")
    log(f"Tổng số mẫu  : {total_rows} dòng (bước mẫu {STEP_SECONDS}s, {len(FEATURE_QUERIES) + 4} đặc trưng)")
    print("-" * 68)
    print(f"  {'Nhãn (Label)':<22} | {'Loại':<12} | {'Số mẫu (Samples)':<15}")
    print("-" * 68)
    for lbl, cnt in label_counts.items():
        kind = "Normal (0)" if lbl == "Normal" else "Anomaly (1)"
        print(f"  {lbl:<22} | {kind:<12} | {cnt:<15}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())

