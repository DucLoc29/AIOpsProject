#!/usr/bin/env python3
"""
Bộ tiền xử lý & Hợp nhất Dataset Duy nhất cho cả 3 Model AIOps (Unified Feature Store).

Từ file thông số Prometheus gốc (dataset/raw_prometheus_timeseries.csv),
script này tạo ra 1 file Dataset hợp nhất duy nhất:
  -> dataset/aiops_network_dataset.csv (700 mẫu x 117 cột)

Cấu trúc của file hợp nhất `dataset/aiops_network_dataset.csv`:
  1. Cột định danh thời gian (2 cột):
     - `timestamp`, `datetime`
  2. Nhóm 42 Đặc trưng Tức thời Đa tầng (42 Instantaneous Multi-layer Features):
     - Đường truyền WAN1/WAN2, Firewall, LAN/TCP Stack, Nginx/Flask API/PostgreSQL.
     - Trong đó có 14 chỉ số KPIs cốt lõi (`KEY_TIMESERIES_KPIS`).
  3. Nhóm 70 Đặc trưng Cửa sổ trượt Chuỗi thời gian (70 Sliding-Window Time-Series Features):
     - Với mỗi KPI trong 14 KPIs cốt lõi, tính trên cửa sổ [t-W+1 ... t]:
       `*_roll_mean`, `*_roll_std`, `*_delta1`, `*_trend_slope`, `*_sustained_ratio`.
  4. Nhóm 3 Cột Nhãn Mục tiêu (3 Targets cho 3 Model riêng biệt):
     - `current_degraded` (0/1)     -> Target cho Model 1 (Extended Isolation Forest - Phát hiện tức thời)
     - `degraded_forecast_5m` (0/1) -> Target cho Model 2 (XGBoost Forecaster - Dự báo suy giảm 5 phút)
     - `label` (7 lớp)              -> Target cho Model 3 (RCA Classifier - Phân loại nguyên nhân gốc rễ)

Khi huấn luyện trên Kaggle hoặc Local, cả 3 Notebook / 3 Model đều đọc chung file
`aiops_network_dataset.csv` và tự động trích chọn đúng tập đặc trưng (features) + nhãn (target) của mình!
"""

import shutil
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "dataset"
UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"
RAW_BACKUP_CSV = DATASET_DIR / "raw_prometheus_timeseries.csv"

META_AND_TARGET_COLS = {
    "timestamp",
    "datetime",
    "is_anomaly",
    "current_degraded",
    "degraded_forecast_5m",
    "label",
}

ROLLING_SUFFIXES = (
    "_roll_mean",
    "_roll_std",
    "_delta1",
    "_trend_slope",
    "_sustained_ratio",
)

KEY_TIMESERIES_KPIS: List[str] = [
    "wan1_rx_bytes_rate",
    "wan1_tx_bytes_rate",
    "branch_tcp_retrans_rate",
    "wan_tcp_syn_retrans_rate",
    "wan_tcp_timeouts_rate",
    "fw_rx_pkts_rate",
    "fw_avg_pkt_size",
    "client_lan_tx_pkts_rate",
    "srv_tcp_out_rsts_rate",
    "srv_tcp_passive_opens_rate",
    "srv_cpu_usage",
    "nginx_req_rate",
    "api_req_rate",
    "api_latency_avg",
]


def get_model_feature_sets(df: pd.DataFrame):
    """
    Hàm tiện ích để mỗi Model tự chọn đúng tập đặc trưng riêng từ Unified Dataset:
      - Model 1 (EIF Instant Detector): 14 KPIs tức thời (`KEY_TIMESERIES_KPIS`)
      - Model 2 (XGBoost 5m Forecaster): 84 đặc trưng chuỗi thời gian (14 KPIs + 70 cột cửa sổ trượt)
      - Model 3 (RCA Multi-class Classifier): 42 đặc trưng trạng thái tức thời đa tầng
    """
    instant_42_cols = [
        c
        for c in df.columns
        if c not in META_AND_TARGET_COLS and not c.endswith(ROLLING_SUFFIXES)
    ]
    rolling_70_cols = [c for c in df.columns if c.endswith(ROLLING_SUFFIXES)]

    model1_features = [c for c in KEY_TIMESERIES_KPIS if c in df.columns]
    model2_features = model1_features + rolling_70_cols
    model3_features = instant_42_cols

    return model1_features, model2_features, model3_features


def build_unified_dataset(
    df_raw: pd.DataFrame,
    window_size: int = 5,
    forecast_horizon: int = 8,
) -> pd.DataFrame:
    df = df_raw.copy().sort_values("timestamp").reset_index(drop=True)

    instant_42_cols = [
        c
        for c in df.columns
        if c not in META_AND_TARGET_COLS and not c.endswith(ROLLING_SUFFIXES)
    ]

    # Tạo một số gai nhiễu ngắn hạn (Transient Micro-bursts) trên vùng Normal
    # để Model 1 phát hiện có gai bất thường tức thời (current_degraded=1)
    # nhưng Model 2 học được xu hướng tự phục hồi sau vài giây (degraded_forecast_5m=0)
    rng = np.random.default_rng(42)
    anomaly_rows = df[df["is_anomaly"] == 1].reset_index(drop=True)
    normal_indices = df.index[df["is_anomaly"] == 0].tolist()

    transient_flags = np.zeros(len(df), dtype=int)
    spike_positions = [idx for idx in normal_indices[10:-15:22]]
    for pos in spike_positions:
        window_slice = df.loc[
            max(0, pos - 2) : min(len(df) - 1, pos + forecast_horizon + 2), "is_anomaly"
        ]
        if window_slice.sum() == 0:
            rand_anom = anomaly_rows.iloc[rng.integers(0, len(anomaly_rows))]
            for col in KEY_TIMESERIES_KPIS:
                df.loc[pos, col] = round(
                    float(df.loc[pos, col]) * 0.4 + float(rand_anom[col]) * 0.45, 4
                )
                if pos + 1 < len(df):
                    df.loc[pos + 1, col] = round(
                        float(df.loc[pos + 1, col]) * 0.7
                        + float(rand_anom[col]) * 0.15,
                        4,
                    )
            df.loc[pos, "is_anomaly"] = 1
            transient_flags[pos] = 1

    records = []
    n = len(df)

    for t in range(window_size, n - forecast_horizon):
        row_out = {
            "timestamp": int(df.loc[t, "timestamp"]),
            "datetime": df.loc[t, "datetime"],
        }

        # 1. Lưu toàn bộ 42 đặc trưng tức thời tại thời điểm t
        for col in instant_42_cols:
            row_out[col] = round(float(df.loc[t, col]), 4)

        # 2. Tính 70 đặc trưng cửa sổ trượt chuỗi thời gian [t - window_size + 1 ... t] cho 14 KPIs
        for kpi in KEY_TIMESERIES_KPIS:
            series_win = df.loc[t - window_size + 1 : t, kpi].values.astype(float)
            curr_val = series_win[-1]
            prev_val = series_win[-2] if len(series_win) > 1 else curr_val
            first_val = series_win[0]

            roll_mean = float(np.mean(series_win))
            roll_std = float(np.std(series_win))
            delta_1 = curr_val - prev_val
            trend_slope = (curr_val - first_val) / max(1, window_size - 1)
            sustained_ratio = curr_val / max(roll_mean, 0.0001)

            row_out[f"{kpi}_roll_mean"] = round(roll_mean, 4)
            row_out[f"{kpi}_roll_std"] = round(roll_std, 4)
            row_out[f"{kpi}_delta1"] = round(delta_1, 4)
            row_out[f"{kpi}_trend_slope"] = round(trend_slope, 4)
            row_out[f"{kpi}_sustained_ratio"] = round(sustained_ratio, 4)

        # 3. Ba cột nhãn mục tiêu cho 3 Model
        current_degraded = int(df.loc[t, "is_anomaly"])
        future_window = df.loc[t + 1 : t + forecast_horizon, "is_anomaly"].values
        degraded_forecast_5m = (
            1 if (np.mean(future_window) >= 0.5 and transient_flags[t] == 0) else 0
        )

        row_out["current_degraded"] = current_degraded
        row_out["degraded_forecast_5m"] = degraded_forecast_5m
        row_out["label"] = df.loc[t, "label"]

        records.append(row_out)

    return pd.DataFrame(records)


def main() -> int:
    # Nếu chưa có bản sao raw_prometheus_timeseries.csv và aiops_network_dataset.csv là bản gốc 46 cột -> sao lưu
    if not RAW_BACKUP_CSV.exists() and UNIFIED_CSV.exists():
        df_check = pd.read_csv(UNIFIED_CSV, nrows=2)
        if "is_anomaly" in df_check.columns and "degraded_forecast_5m" not in df_check.columns:
            shutil.copyfile(UNIFIED_CSV, RAW_BACKUP_CSV)

    source_csv = RAW_BACKUP_CSV if RAW_BACKUP_CSV.exists() else UNIFIED_CSV
    if not source_csv.exists():
        print(f"[!] Không tìm thấy dữ liệu gốc tại: {source_csv}")
        return 1

    df_raw = pd.read_csv(source_csv).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    df_unified = build_unified_dataset(df_raw, window_size=5, forecast_horizon=8)
    df_unified.to_csv(UNIFIED_CSV, index=False)

    # Dọn dẹp các file CSV tách lẻ dư thừa để thư mục dataset/ chỉ còn 1 Dataset hợp nhất chuẩn
    for extra in [
        "aiops_unified_dataset.csv",
        "dataset_model1_eif_instant.csv",
        "dataset_model2_xgboost_forecast.csv",
        "dataset_model3_rca_classifier.csv",
        "dataset_model1_timeseries.csv",
        "dataset_model2_rca.csv",
    ]:
        p = DATASET_DIR / extra
        if p.exists():
            p.unlink()

    m1_f, m2_f, m3_f = get_model_feature_sets(df_unified)

    print("=" * 84)
    print("HOÀN TẤT HỢP NHẤT 1 FILE DATASET DUY NHẤT CHO CẢ 3 MODEL (UNIFIED DATASET)")
    print("=" * 84)
    print(f"File Dataset duy nhất : {UNIFIED_CSV}")
    print(f"Tổng kích thước       : {df_unified.shape[0]} mẫu x {df_unified.shape[1]} cột")
    print("-" * 84)
    print(f"  1. Model 1 (EIF Instant Detector)     -> Tự chọn {len(m1_f):2d} features tức thời  | Target: current_degraded")
    print(f"  2. Model 2 (XGBoost 5m Forecaster)    -> Tự chọn {len(m2_f):2d} features trượt     | Target: degraded_forecast_5m")
    print(f"  3. Model 3 (RCA Fault Classifier)     -> Tự chọn {len(m3_f):2d} features đa tầng   | Target: label (7 lớp)")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
