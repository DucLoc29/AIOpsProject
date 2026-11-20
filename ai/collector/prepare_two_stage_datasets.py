#!/usr/bin/env python3
"""
Bộ Thu thập Thực nghiệm 100% Dữ liệu thật từ Containerlab + Prometheus + Kernel Accounting + Data-Plane Probe
& Xây dựng Dataset 6 Thông số SLA + Cửa sổ trượt 5 bước theo từng Dịch vụ.

KHÔNG dùng `rng.normal` hay công thức giả lập theo nhãn!
Mỗi dòng dữ liệu đều được đo trực tiếp mỗi 2 giây bằng đúng hàm `RealContainerlabTelemetryCollector`
(Single Source of Truth dùng chung cho cả quá trình thu thập Dataset lẫn quá trình chạy Live trên Web).

Quy trình thu thập (3 chu kỳ độc lập -> ~2.600+ mẫu thật, 43 cột):
  1. `00_normal_traffic.sh` chạy nền liên tục tạo tải người dùng LAN & Chi nhánh thật.
  2. Trong mỗi chu kỳ:
       - Pha `Normal` + 1 đợt `05_transient_spike.sh` thật (ghi nhận gai nhiễu thoáng qua).
       - Pha `SynFlood` (`01_internet_syn_flood.sh`) -> tác động thật lên `srv_web_portal (:80)`.
       - Pha `HttpFlood` (`04_server_http_flood.sh`) -> tác động thật lên `srv_web_portal`, `srv_flask_api`, `srv_postgres_db`.
       - Pha `SshBruteforce` (`03_ssh_bruteforce.sh`) -> tác động thật lên `srv_ssh_access (:22)`.
       - Pha `PortScan` (`02_insider_port_scan.sh`) -> quét nội bộ tác động thật lên cả 4 dịch vụ.
  3. Tính Cửa sổ trượt 5 bước ĐỘC LẬP trên chuỗi thời gian liên tục của từng `service_id`
     (`6 thông số SLA x 6 đại lượng = 36 đặc trưng cửa sổ trượt`) và xuất:
       - `ai/dataset/raw_service_6sla_timeseries.csv` (toàn bộ chuỗi đo thô)
       - `ai/dataset/aiops_network_dataset.csv`       (dataset huấn luyện hợp nhất)
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
DATASET_DIR = PROJECT_ROOT / "dataset"
UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"
RAW_SLA_CSV = DATASET_DIR / "raw_service_6sla_timeseries.csv"
SCENARIOS_DIR = REPO_ROOT / "scripts" / "scenarios"
BACKEND_DIR = REPO_ROOT / "monitoring" / "aiops-dashboard" / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SERVICE_CORE_KPIS: List[str] = [
    "latency",
    "jitter",
    "packet_loss",
    "throughput",
    "response_time",
    "uptime",
]

KEY_TIMESERIES_KPIS: List[str] = list(SERVICE_CORE_KPIS)

ROLLING_SUFFIXES = (
    "_roll_mean",
    "_roll_std",
    "_delta1",
    "_trend_slope",
    "_sustained_ratio",
)

# Ngưỡng sàn vật lý tối thiểu trên nền Normal để tính `sustained_ratio` ổn định (chống nhiễu cực nhỏ)
MIN_NORMAL_DELTA: Dict[str, float] = {
    "latency": 3.0,        # ms
    "jitter": 2.5,         # ms
    "packet_loss": 3.0,    # %
    "throughput": 25.0,    # KB/s
    "response_time": 25.0, # ms
    "uptime": 1.2,         # %
}

META_AND_TARGET_COLS = {
    "timestamp",
    "datetime",
    "service_id",
    "is_anomaly",
    "current_degraded",
    "severity_level",
    "forecast_level_5m",
    "label",
}

ACTIVE_CLASSES = ("Normal", "SynFlood", "HttpFlood", "SshBruteforce", "PortScan")


def compute_sustained_thresholds(
    clean_normal_df: pd.DataFrame,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Tính ngưỡng bất thường duy trì (`sustained_ratio`) từ các mẫu Normal sạch (không gộp TransientSpike).
    Dùng chung giữa bước tạo dataset và bước suy luận thời gian thực trong `app.py`!
    """
    medians: Dict[str, float] = {}
    thresholds: Dict[str, float] = {}
    for k in SERVICE_CORE_KPIS:
        if k in clean_normal_df.columns and not clean_normal_df.empty:
            vals = clean_normal_df[k].astype(float).values
            med = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med)))
            robust_std = max(1.4826 * mad, 1e-3)
        else:
            med = 99.9 if k == "uptime" else 1.0
            robust_std = 0.05 if k == "uptime" else 1.0
        medians[k] = med
        delta = max(2.5 * robust_std, MIN_NORMAL_DELTA.get(k, 1.0))
        if k == "uptime":
            thresholds[k] = round(med - delta, 4)
        else:
            thresholds[k] = round(med + delta, 4)
    return medians, thresholds


def get_model_feature_sets(df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    """
    Trích xuất tập đặc trưng thuần túy từ 6 thông số SLA gốc qua Cửa sổ trượt 5 bước
    cho 2 Mô hình AI:
      - Model 1 (EIF Current Health Detector): 18 đặc trưng (6 SLA x 3 đại lượng: Current, Mean, Sustained)
      - Model 2 (XGBoost Multi-class RCA Classifier): 36 đặc trưng (6 SLA x 6 đại lượng cửa sổ trượt)
    """
    m1_features = []
    for k in SERVICE_CORE_KPIS:
        if k in df.columns:
            m1_features.append(k)
        if f"{k}_roll_mean" in df.columns:
            m1_features.append(f"{k}_roll_mean")
        if f"{k}_sustained_ratio" in df.columns:
            m1_features.append(f"{k}_sustained_ratio")

    m2_rca_features = [k for k in SERVICE_CORE_KPIS if k in df.columns]
    for k in SERVICE_CORE_KPIS:
        for suf in ROLLING_SUFFIXES:
            col = f"{k}{suf}"
            if col in df.columns:
                m2_rca_features.append(col)

    return m1_features, m2_rca_features


def compute_severity_level(
    score: float, th_med: float = 0.48, th_high: float = 0.57, th_crit: float = 0.67
) -> str:
    if score >= th_crit:
        return "CRITICAL"
    if score >= th_high:
        return "HIGH"
    if score >= th_med:
        return "MEDIUM"
    return "NORMAL"


def compute_5m_trend_forecast(
    current_score: float,
    window_scores: List[float],
    th_med: float = 0.48,
    th_high: float = 0.57,
    th_crit: float = 0.67,
) -> Tuple[float, str, float]:
    """
    Công thức Toán học Dự báo Chuỗi thời gian 5 phút tới:
      S_hat(t + 5m) = EMA(S_t) + alpha * Slope(S_{t-W..t}) * R_sustained
    """
    arr = np.array(window_scores if window_scores else [current_score], dtype=float)
    w = len(arr)
    weights = np.exp(np.linspace(-1.0, 0.0, w))
    weights /= np.sum(weights)
    ema_score = float(np.sum(arr * weights))

    slope = float((arr[-1] - arr[0]) / max(1, w - 1)) if w > 1 else 0.0
    r_sustained = float(np.mean(arr >= th_med))

    if r_sustained <= 0.25 and slope >= 0:
        forecast_score = ema_score * 0.78
    else:
        forecast_score = ema_score + 1.35 * slope * max(0.3, r_sustained)

    forecast_score = float(np.clip(forecast_score, 0.20, 0.98))
    level = compute_severity_level(forecast_score, th_med, th_high, th_crit)

    if forecast_score < th_med:
        prob_pct = max(0.5, (forecast_score / max(th_med, 0.01)) * 22.0 * max(0.1, r_sustained))
    elif forecast_score < th_high:
        ratio = (forecast_score - th_med) / max(th_high - th_med, 0.005)
        prob_pct = 30.0 + ratio * 24.0
    elif forecast_score < th_crit:
        ratio = (forecast_score - th_high) / max(th_crit - th_high, 0.005)
        prob_pct = 66.0 + ratio * 16.0
    else:
        ratio = min(1.0, (forecast_score - th_crit) / max(0.18, th_crit - th_med))
        prob_pct = 84.0 + ratio * 15.5

    return round(forecast_score, 4), level, round(float(min(99.5, prob_pct)), 2)


def _stop_all_attack_tools() -> None:
    kill_http = (
        "if [ -f /tmp/aiops_http_flood.pid ]; then kill -9 $(cat /tmp/aiops_http_flood.pid) 2>/dev/null || true; rm -f /tmp/aiops_http_flood.pid; fi; "
        "pkill -9 -f AIOPS_HTTP_FLOOD_WORKER 2>/dev/null || true; "
        "killall -9 hping3 ssh sshpass curl 2>/dev/null || true"
    )
    for container, kill_cmd in (
        ("clab-aiops-prod-internet-attacker", kill_http),
        ("clab-aiops-prod-client-pc", "killall -9 nmap hping3 2>/dev/null || true"),
        ("clab-aiops-prod-branch-client", kill_http),
    ):
        subprocess.run(["docker", "exec", container, "sh", "-c", kill_cmd], capture_output=True)


def _clear_blocking_rules() -> None:
    """Đảm bảo không còn luật chặn DROP cũ nào từ các lần demo trước khi thu thập dataset."""
    subprocess.run(
        ["docker", "exec", "clab-aiops-prod-firewall", "iptables", "-F", "FORWARD"],
        capture_output=True,
    )
    for container in (
        "clab-aiops-prod-service-server",
        "clab-aiops-prod-app-server",
        "clab-aiops-prod-db-server",
    ):
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                "iptables -F INPUT 2>/dev/null || true; "
                "iptables -F AIOPS_ACCT_IN 2>/dev/null || true; "
                "iptables -F AIOPS_ACCT_OUT 2>/dev/null || true",
            ],
            capture_output=True,
        )


def collect_live_scenarios_from_containerlab(
    cycles: int = 3,
    sample_interval: float = 2.0,
) -> pd.DataFrame:
    """
    Thu thập 100% DỮ LIỆU THẬT theo thời gian thực từ Containerlab + Prometheus + Kernel Accounting
    + Data-Plane Active Probe trên 4 dịch vụ doanh nghiệp qua `cycles` chu kỳ độc lập.
    """
    from database import DEFAULT_SERVICES
    from telemetry import RealContainerlabTelemetryCollector

    services = [dict(s) for s in DEFAULT_SERVICES]
    _stop_all_attack_tools()
    _clear_blocking_rules()

    collector = RealContainerlabTelemetryCollector(prom_url="http://127.0.0.1:9090")
    collector.ensure_services_registered(services)

    print("=" * 92)
    print(f"[*] BẮT ĐẦU THU THẬP 100% DỮ LIỆU THẬT TỪ CONTAINERLAB ({cycles} CHU KỲ ĐỘC LẬP, BƯỚC {sample_interval:.1f}s)")
    print("    (Tuyệt đối KHÔNG dùng rng.normal hay số liệu giả lập!)")
    print("=" * 92)

    bg_proc = subprocess.Popen(
        ["bash", str(SCENARIOS_DIR / "00_normal_traffic.sh"), "3600"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    rows: List[Dict[str, Any]] = []

    def _record_ticks(
        n_ticks: int,
        phase_label: str,
        affected_services: set,
        phase_tag: str = "steady",
        is_transient: bool = False,
    ) -> None:
        for tick_idx in range(n_ticks):
            t_start = time.monotonic()
            _, _, sla_map = collector.sample_all_services(services)
            now_epoch = int(time.time())
            dt_str = datetime.fromtimestamp(now_epoch).strftime("%Y-%m-%d %H:%M:%S")

            for srv in services:
                sid = srv["service_id"]
                m = sla_map[sid]
                is_aff = sid in affected_services
                row_label = phase_label if (is_aff and not is_transient) else "Normal"
                is_anom = 1 if (is_aff and phase_label != "Normal" and not is_transient) else 0
                rows.append(
                    {
                        "timestamp": now_epoch,
                        "datetime": dt_str,
                        "service_id": sid,
                        "latency": m["latency"],
                        "jitter": m["jitter"],
                        "packet_loss": m["packet_loss"],
                        "throughput": m["throughput"],
                        "response_time": m["response_time"],
                        "uptime": m["uptime"],
                        "is_anomaly": is_anom,
                        "is_transient": 1 if (is_transient and is_aff) else 0,
                        "phase": phase_tag,
                        "label": row_label,
                    }
                )
            elapsed = time.monotonic() - t_start
            if tick_idx < n_ticks - 1 and elapsed < sample_interval:
                time.sleep(sample_interval - elapsed)
            elif elapsed < sample_interval:
                time.sleep(sample_interval - elapsed)

    def _run_attack_phase(
        label: str,
        script_name: str,
        affected: set,
        steady_ticks: int,
        warmup_ticks: int = 2,
        cooldown_ticks: int = 5,
    ) -> None:
        script_path = SCENARIOS_DIR / script_name
        run_sec = int((warmup_ticks + steady_ticks) * max(sample_interval, 3.2)) + 30
        print(
            f"     [+] Chạy kịch bản thật: {script_name} ({run_sec}s) -> thu {steady_ticks} ticks "
            f"nhãn '{label}' trên {sorted(affected)}",
            flush=True,
        )
        atk_proc = subprocess.Popen(
            ["bash", str(script_path), str(run_sec)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            # 1. Pha khởi động (warmup 4s) — bỏ qua khỏi tập huấn luyện để không nhiễu nhãn biên
            _record_ticks(warmup_ticks, label, affected, phase_tag="warmup")
            # 2. Pha tấn công ổn định (100% nhãn sạch)
            _record_ticks(steady_ticks, label, affected, phase_tag="steady")
        finally:
            try:
                os.killpg(os.getpgid(atk_proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    atk_proc.terminate()
                except Exception:
                    pass
            try:
                atk_proc.wait(timeout=5)
            except Exception:
                pass
            _stop_all_attack_tools()

        # 3. Pha hồi phục tự nhiên về Normal (10s) để cửa sổ trượt sạch hoàn toàn trước kịch bản kế tiếp
        _record_ticks(cooldown_ticks, "Normal", set(), phase_tag="cooldown")

    try:
        # Warmup 3 ticks để ổn định bộ đếm kernel và Prometheus [6s]
        print("[*] Khởi động bộ đếm kernel & tải nền Normal (6s)...", flush=True)
        for _ in range(3):
            collector.sample_all_services(services)
            time.sleep(sample_interval)

        for cycle in range(1, cycles + 1):
            print(f"\n[*] === CHU KỲ THU THẬP THỰC NGHIỆM {cycle}/{cycles} ===", flush=True)

            # 1. Pha Normal chuẩn (18 ticks = 36s -> 72 mẫu thật)
            print("     [+] Thu thập pha Normal baseline (36s)...", flush=True)
            _record_ticks(18, "Normal", set(), phase_tag="steady")

            # 2. Pha Gai nhiễu thoáng qua THẬT (05_transient_spike.sh — 2s rồi tự dừng)
            print("     [+] Chạy kịch bản thật: 05_transient_spike.sh (2s) -> ghi nhận TransientSpike thật", flush=True)
            sp_proc = subprocess.Popen(
                ["bash", str(SCENARIOS_DIR / "05_transient_spike.sh"), "2"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _record_ticks(
                2,
                "Normal",
                {"srv_web_portal", "srv_flask_api"},
                phase_tag="transient_spike",
                is_transient=True,
            )
            try:
                sp_proc.wait(timeout=8)
            except Exception:
                pass
            # Hồi phục sau gai nhiễu
            _record_ticks(10, "Normal", set(), phase_tag="steady")

            # 3. SynFlood trên Web Portal (:80) — 50 ticks/chu kỳ x 3 = 150 mẫu SynFlood thật
            _run_attack_phase(
                label="SynFlood",
                script_name="01_internet_syn_flood.sh",
                affected={"srv_web_portal"},
                steady_ticks=50,
                warmup_ticks=3,
                cooldown_ticks=6,
            )

            # 4. HttpFlood trên 3 tầng Web (:80) -> API (:5000) -> DB (:5432) — 28 ticks x 3 srv x 3 = 252 mẫu HttpFlood thật
            _run_attack_phase(
                label="HttpFlood",
                script_name="04_server_http_flood.sh",
                affected={"srv_web_portal", "srv_flask_api", "srv_postgres_db"},
                steady_ticks=28,
                warmup_ticks=3,
                cooldown_ticks=6,
            )

            # 5. SshBruteforce trên SSH (:22) — 50 ticks/chu kỳ x 3 = 150 mẫu SshBruteforce thật
            _run_attack_phase(
                label="SshBruteforce",
                script_name="03_ssh_bruteforce.sh",
                affected={"srv_ssh_access"},
                steady_ticks=50,
                warmup_ticks=3,
                cooldown_ticks=6,
            )

            # 6. PortScan nội bộ LAN quét cả 4 dịch vụ — 24 ticks x 4 srv x 3 = 288 mẫu PortScan thật
            _run_attack_phase(
                label="PortScan",
                script_name="02_insider_port_scan.sh",
                affected={"srv_web_portal", "srv_flask_api", "srv_postgres_db", "srv_ssh_access"},
                steady_ticks=24,
                warmup_ticks=3,
                cooldown_ticks=6,
            )

            print(f"     [✓] Hoàn tất Chu kỳ {cycle}/{cycles} (tích lũy {len(rows)} dòng thô)", flush=True)

        # Kết thúc bằng 10 ticks Normal sạch
        _record_ticks(10, "Normal", set(), phase_tag="steady")

    finally:
        try:
            os.killpg(os.getpgid(bg_proc.pid), signal.SIGTERM)
        except Exception:
            try:
                bg_proc.terminate()
            except Exception:
                pass
        _stop_all_attack_tools()

    df_raw = pd.DataFrame(rows)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    df_raw.to_csv(RAW_SLA_CSV, index=False)
    print(f"\n[✓] Đã lưu toàn bộ chuỗi thời gian 6 thông số SLA gốc ({len(df_raw)} mẫu thật) vào: {RAW_SLA_CSV}")
    return df_raw


def build_unified_dataset_from_raw(
    df_raw: pd.DataFrame,
    window_size: int = 5,
    forecast_horizon: int = 5,
) -> pd.DataFrame:
    """
    Tính Cửa sổ trượt 5 bước ĐỘC LẬP trên từng chuỗi thời gian của mỗi `service_id`
    (khớp 100% cơ chế `self.service_windows[sid]` của backend thời gian thực).
    """
    # Tính ngưỡng baseline Normal sạch (loại trừ warmup, cooldown và transient_spike)
    clean_normal = df_raw[
        (df_raw["label"] == "Normal")
        & (df_raw["phase"] == "steady")
        & (df_raw["is_transient"] == 0)
    ]
    _, sustained_thrs = compute_sustained_thresholds(clean_normal)

    records: List[Dict[str, Any]] = []

    for sid, grp in df_raw.groupby("service_id", sort=False):
        sub = grp.reset_index(drop=True)
        n = len(sub)
        for t in range(window_size - 1, n - forecast_horizon):
            phase = str(sub.loc[t, "phase"])
            # Bỏ các dòng biên chuyển tiếp (warmup / cooldown) để bộ dữ liệu huấn luyện sạch nhãn
            if phase in ("warmup", "cooldown"):
                continue

            row_out: Dict[str, Any] = {
                "timestamp": int(sub.loc[t, "timestamp"]),
                "datetime": str(sub.loc[t, "datetime"]),
                "service_id": str(sid),
            }

            for col in SERVICE_CORE_KPIS:
                row_out[col] = round(float(sub.loc[t, col]), 4)

            for kpi in SERVICE_CORE_KPIS:
                series_win = sub.loc[t - window_size + 1 : t, kpi].values.astype(float)
                curr_val = series_win[-1]
                prev_val = series_win[-2] if len(series_win) > 1 else curr_val
                first_val = series_win[0]

                roll_mean = float(np.mean(series_win))
                roll_std = float(np.std(series_win))
                delta_1 = curr_val - prev_val
                trend_slope = (curr_val - first_val) / max(1, window_size - 1)

                thr = sustained_thrs[kpi]
                if kpi == "uptime":
                    sustained_ratio = float(np.mean(series_win < thr))
                else:
                    sustained_ratio = float(np.mean(series_win > thr))

                row_out[f"{kpi}_roll_mean"] = round(roll_mean, 4)
                row_out[f"{kpi}_roll_std"] = round(roll_std, 4)
                row_out[f"{kpi}_delta1"] = round(delta_1, 4)
                row_out[f"{kpi}_trend_slope"] = round(trend_slope, 4)
                row_out[f"{kpi}_sustained_ratio"] = round(sustained_ratio, 4)

            win_anom = sub.loc[t - window_size + 1 : t, "is_anomaly"].values
            is_anom_now = int(sub.loc[t, "is_anomaly"])
            is_trans_now = int(sub.loc[t, "is_transient"])
            current_degraded = 1 if (is_anom_now == 1 and float(np.mean(win_anom)) >= 0.4) else 0

            future_win = sub.loc[t + 1 : t + forecast_horizon, "is_anomaly"].values
            degraded_5m = 1 if (float(np.mean(future_win)) >= 0.5 and is_trans_now == 0) else 0

            if current_degraded == 1:
                sev = (
                    "CRITICAL"
                    if (
                        float(row_out["packet_loss_roll_mean"]) >= 18.0
                        or float(row_out["response_time_roll_mean"]) >= 100.0
                        or float(row_out["throughput_roll_mean"]) >= 150.0
                    )
                    else "HIGH"
                )
            elif is_trans_now == 1:
                sev = "MEDIUM"
            else:
                sev = "NORMAL"

            row_out["current_degraded"] = current_degraded
            row_out["severity_level"] = sev
            row_out["forecast_level_5m"] = "CRITICAL" if degraded_5m == 1 else "NORMAL"
            row_out["label"] = str(sub.loc[t, "label"])
            records.append(row_out)

    df_out = pd.DataFrame(records)
    # Sắp xếp lại theo thời gian + dịch vụ cho dễ đọc & kiểm tra
    if not df_out.empty:
        df_out = df_out.sort_values(["timestamp", "service_id"]).reset_index(drop=True)
    return df_out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Thu thập 100% dữ liệu thật từ Containerlab & xây dựng Dataset 6-SLA cửa sổ trượt."
    )
    parser.add_argument("--cycles", type=int, default=3, help="Số chu kỳ thu thập thực nghiệm độc lập (mặc định: 3)")
    parser.add_argument("--interval", type=float, default=2.0, help="Chu kỳ lấy mẫu mỗi bước (giây, mặc định: 2.0)")
    parser.add_argument(
        "--from-raw",
        action="store_true",
        help="Xây dựng lại aiops_network_dataset.csv từ raw_service_6sla_timeseries.csv có sẵn mà không chạy lại lab",
    )
    args = parser.parse_args()

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_raw and RAW_SLA_CSV.exists():
        print(f"[*] Nạp dữ liệu thô thật từ {RAW_SLA_CSV}...")
        df_raw = pd.read_csv(RAW_SLA_CSV)
    else:
        df_raw = collect_live_scenarios_from_containerlab(
            cycles=args.cycles, sample_interval=args.interval
        )

    df_unified = build_unified_dataset_from_raw(df_raw, window_size=5, forecast_horizon=5)
    df_unified.to_csv(UNIFIED_CSV, index=False)

    m1_f, m2_f = get_model_feature_sets(df_unified)

    print("\n" + "=" * 92)
    print("HOÀN TẤT THU THẬP 100% DỮ LIỆU THẬT TỪ CONTAINERLAB & XÂY DỰNG DATASET 6-SLA CỬA SỔ TRƯỢT")
    print("=" * 92)
    print(f"File chuỗi thô thật   : {RAW_SLA_CSV} ({len(df_raw)} mẫu)")
    print(f"File Dataset hợp nhất : {UNIFIED_CSV}")
    print(f"Tổng kích thước       : {df_unified.shape[0]} mẫu x {df_unified.shape[1]} cột")
    print("Phân bố số mẫu theo lớp sự cố:")
    for lbl, cnt in df_unified["label"].value_counts().items():
        pct = cnt / max(1, len(df_unified)) * 100.0
        print(f"  - {lbl:<16}: {cnt:4d} mẫu ({pct:5.1f}%)")
    print("Phân bố theo mức độ (severity_level):")
    for sev, cnt in df_unified["severity_level"].value_counts().items():
        print(f"  - {sev:<16}: {cnt:4d} mẫu")
    print("-" * 92)
    print(f"  1. Model 1 (EIF Sliding-Window)      -> {len(m1_f):2d} đặc trưng (6 SLA x 3) | Target: current_degraded")
    print("  2. Dự báo 5m (EWMA + Trend Formula)  -> Công thức Toán học Chuỗi thời gian")
    print(f"  3. Model 2 (XGBoost RCA Classifier)  -> {len(m2_f):2d} đặc trưng (6 SLA x 6) | Target: label (5 lớp)")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
