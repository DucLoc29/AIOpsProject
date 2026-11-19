#!/usr/bin/env python3
"""
Bộ tiền xử lý & Hợp nhất Dataset Chuẩn hóa 6 Thông số SLA Gốc + Cửa sổ trượt 5 bước
cho Kiến trúc 2 Mô hình AI + 1 Công thức Dự báo Chuỗi thời gian:
  1. Model 1 (Extended Isolation Forest - EIF):
     Đánh giá chất lượng dịch vụ hiện tại theo 4 mức độ (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`)
     từ 18 đặc trưng cửa sổ trượt (`6 thông số SLA x 3 đại lượng: Current, _roll_mean, _sustained_ratio`).
  2. Công thức Dự báo Xu hướng 5 phút (`compute_5m_trend_forecast` - EWMA + Trend Slope * Sustained Ratio):
     Ngoại suy trực tiếp từ chuỗi điểm cửa sổ trượt để dự báo 4 mức độ tương lai (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`)
     mà không cần huấn luyện một mô hình học máy dư thừa.
  3. Model 2 (XGBoost Multi-class RCA Classifier):
     Phân loại chính xác nguyên nhân sự cố (5 lớp: `Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`)
     từ 36 đặc trưng cửa sổ trượt (`6 thông số SLA x 6 đại lượng thời gian`).
"""

import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "dataset"
UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"

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
    """Phân loại điểm bất thường thành 4 mức độ chuẩn."""
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
    Công thức Toán học Dự báo Chuỗi thời gian 5 phút tới (EWMA + Linear Trend Slope * Sustained Ratio):
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
        prob_pct = max(0.4, (forecast_score / max(th_med, 0.01)) * 18.0 * r_sustained)
    else:
        norm = (forecast_score - th_med) / max(0.95 - th_med, 0.01)
        prob_pct = min(99.6, 55.0 + norm * 44.5)

    return round(forecast_score, 4), level, round(float(prob_pct), 2)


def _synthesize_service_series(rng: np.random.Generator) -> pd.DataFrame:
    schedule = [
        ("Normal", "srv_web_portal", 90),
        ("SynFlood", "srv_web_portal", 75),
        ("Normal", "srv_flask_api", 75),
        ("HttpFlood", "srv_flask_api", 75),
        ("Normal", "srv_ssh_access", 75),
        ("SshBruteforce", "srv_ssh_access", 75),
        ("Normal", "srv_postgres_db", 75),
        ("PortScan", "srv_ssh_access", 75),
        ("Normal", "srv_web_portal", 65),
    ]

    rows = []
    ts = 1763300000
    prev_lat = 4.2

    for label, srv_id, count in schedule:
        for _ in range(count):
            ts += 2
            dt_str = pd.to_datetime(ts, unit="s").strftime("%Y-%m-%d %H:%M:%S")

            if label == "Normal":
                lat = float(rng.normal(4.2, 0.75))
                loss = float(np.clip(rng.normal(0.04, 0.025), 0.0, 0.25))
                tput = float(np.clip(rng.normal(18.5, 3.2), 4.0, 35.0))
                resp = float(lat * rng.uniform(1.35, 1.85) + rng.normal(1.2, 0.35))
                uptime = float(np.clip(100.0 - loss * 0.45 - rng.uniform(0.0, 0.04), 99.6, 100.0))
                is_anom = 0

            elif label == "SynFlood":
                lat = float(rng.normal(74.0, 8.5))
                loss = float(np.clip(rng.normal(13.5, 2.1), 7.5, 22.0))
                tput = float(np.clip(rng.normal(145.0, 18.0), 90.0, 210.0))
                resp = float(lat * rng.uniform(1.25, 1.48) + rng.normal(4.0, 1.5))
                uptime = float(np.clip(100.0 - loss * 1.15, 72.0, 89.5))
                is_anom = 1

            elif label == "HttpFlood":
                lat = float(rng.normal(52.0, 6.5))
                loss = float(np.clip(rng.normal(6.8, 1.4), 3.2, 12.0))
                tput = float(np.clip(rng.normal(640.0, 65.0), 420.0, 850.0))
                resp = float(rng.normal(235.0, 24.0))
                uptime = float(np.clip(100.0 - loss * 1.25 - 4.0, 76.0, 91.0))
                is_anom = 1

            elif label == "SshBruteforce":
                lat = float(rng.normal(28.0, 3.5))
                loss = float(np.clip(rng.normal(3.4, 0.7), 1.5, 6.5))
                tput = float(np.clip(rng.normal(68.0, 7.5), 45.0, 95.0))
                resp = float(rng.normal(142.0, 14.0))
                uptime = float(np.clip(100.0 - loss * 1.1 - 1.5, 88.0, 96.0))
                is_anom = 1

            else:  # PortScan
                lat = float(rng.normal(22.0, 5.2))
                loss = float(np.clip(rng.normal(2.6, 0.8), 0.8, 5.5))
                tput = float(np.clip(rng.normal(38.0, 6.5), 22.0, 58.0))
                resp = float(lat * rng.uniform(1.4, 1.75) + rng.normal(3.5, 1.2))
                uptime = float(np.clip(100.0 - loss * 0.9, 92.0, 98.2))
                is_anom = 1

            lat = max(1.2, lat)
            jit = max(0.2, abs(lat - prev_lat) * 0.85 + float(rng.uniform(0.25, 0.95)))
            prev_lat = lat
            resp = max(lat + 1.0, resp)

            rows.append(
                {
                    "timestamp": ts,
                    "datetime": dt_str,
                    "service_id": srv_id,
                    "latency": round(lat, 4),
                    "jitter": round(jit, 4),
                    "packet_loss": round(loss, 4),
                    "throughput": round(tput, 4),
                    "response_time": round(resp, 4),
                    "uptime": round(uptime, 4),
                    "is_anomaly": is_anom,
                    "label": label,
                }
            )

    return pd.DataFrame(rows)


def build_unified_dataset(
    window_size: int = 5,
    forecast_horizon: int = 6,
) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    df = _synthesize_service_series(rng)

    normal_df = df[df["label"] == "Normal"]
    medians = {k: float(normal_df[k].median()) for k in SERVICE_CORE_KPIS}
    stds = {k: max(float(normal_df[k].std()), 1e-3) for k in SERVICE_CORE_KPIS}

    normal_indices = df.index[df["is_anomaly"] == 0].tolist()
    transient_flags = np.zeros(len(df), dtype=int)
    for pos in normal_indices[12:-15:24]:
        win_check = df.loc[max(0, pos - 3) : min(len(df) - 1, pos + forecast_horizon + 3), "is_anomaly"]
        if win_check.sum() == 0:
            df.loc[pos, "latency"] = round(float(df.loc[pos, "latency"]) * 4.5, 4)
            df.loc[pos, "jitter"] = round(float(df.loc[pos, "jitter"]) * 4.2, 4)
            df.loc[pos, "response_time"] = round(float(df.loc[pos, "response_time"]) * 3.8, 4)
            df.loc[pos, "throughput"] = round(float(df.loc[pos, "throughput"]) * 2.6, 4)
            df.loc[pos, "packet_loss"] = round(float(df.loc[pos, "packet_loss"]) + 1.4, 4)
            df.loc[pos, "uptime"] = round(max(96.5, float(df.loc[pos, "uptime"]) - 1.8), 4)
            transient_flags[pos] = 1

    records = []
    n = len(df)

    for t in range(window_size, n - forecast_horizon):
        row_out = {
            "timestamp": int(df.loc[t, "timestamp"]),
            "datetime": df.loc[t, "datetime"],
            "service_id": df.loc[t, "service_id"],
        }

        for col in SERVICE_CORE_KPIS:
            row_out[col] = round(float(df.loc[t, col]), 4)

        for kpi in SERVICE_CORE_KPIS:
            series_win = df.loc[t - window_size + 1 : t, kpi].values.astype(float)
            curr_val = series_win[-1]
            prev_val = series_win[-2] if len(series_win) > 1 else curr_val
            first_val = series_win[0]

            roll_mean = float(np.mean(series_win))
            roll_std = float(np.std(series_win))
            delta_1 = curr_val - prev_val
            trend_slope = (curr_val - first_val) / max(1, window_size - 1)

            if kpi == "uptime":
                thr = medians[kpi] - 2.5 * stds[kpi]
                sustained_ratio = float(np.mean(series_win < thr))
            else:
                thr = medians[kpi] + 2.5 * stds[kpi]
                sustained_ratio = float(np.mean(series_win > thr))

            row_out[f"{kpi}_roll_mean"] = round(roll_mean, 4)
            row_out[f"{kpi}_roll_std"] = round(roll_std, 4)
            row_out[f"{kpi}_delta1"] = round(delta_1, 4)
            row_out[f"{kpi}_trend_slope"] = round(trend_slope, 4)
            row_out[f"{kpi}_sustained_ratio"] = round(sustained_ratio, 4)

        win_anom = df.loc[t - window_size + 1 : t, "is_anomaly"].values
        current_degraded = 1 if (int(df.loc[t, "is_anomaly"]) == 1 and np.mean(win_anom) >= 0.4) else 0

        future_window = df.loc[t + 1 : t + forecast_horizon, "is_anomaly"].values
        degraded_forecast_5m = 1 if (np.mean(future_window) >= 0.5 and transient_flags[t] == 0) else 0

        if current_degraded == 1:
            sev = "CRITICAL" if float(row_out["uptime_roll_mean"]) < 92.0 or float(row_out["response_time_roll_mean"]) > 90.0 else "HIGH"
        elif transient_flags[t] == 1:
            sev = "MEDIUM"
        else:
            sev = "NORMAL"

        row_out["current_degraded"] = current_degraded
        row_out["severity_level"] = sev
        row_out["forecast_level_5m"] = "CRITICAL" if degraded_forecast_5m == 1 else "NORMAL"
        row_out["label"] = df.loc[t, "label"]

        records.append(row_out)

    return pd.DataFrame(records)


def main() -> int:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    df_unified = build_unified_dataset(window_size=5, forecast_horizon=6)
    df_unified.to_csv(UNIFIED_CSV, index=False)

    m1_f, m2_f = get_model_feature_sets(df_unified)

    print("=" * 86)
    print("HOÀN TẤT XÂY DỰNG DATASET THUẦN 6 THÔNG SỐ SLA GỐC x CỬA SỔ TRƯỢT 5 BƯỚC")
    print("=" * 86)
    print(f"File Dataset duy nhất : {UNIFIED_CSV}")
    print(f"Tổng kích thước       : {df_unified.shape[0]} mẫu x {df_unified.shape[1]} cột")
    print(f"6 Thông số SLA gốc    : {SERVICE_CORE_KPIS}")
    print("-" * 86)
    print(f"  1. Model 1 (EIF Sliding-Window)      -> {len(m1_f):2d} đặc trưng (6 SLA x 3 đại lượng) | Target: current_degraded (4 mức)")
    print(f"  2. Dự báo 5m (EWMA + Trend Formula)  -> Công thức Toán học Chuỗi thời gian (không cần train ML)")
    print(f"  3. Model 2 (XGBoost RCA Classifier)  -> {len(m2_f):2d} đặc trưng (6 SLA x 6 đại lượng) | Target: label (5 lớp)")
    print("=" * 86)
    return 0


if __name__ == "__main__":
    sys.exit(main())
