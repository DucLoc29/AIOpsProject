#!/usr/bin/env python3
"""
Giai đoạn 4 & 5: Bộ Điều phối Suy luận Hệ thống 3 Model Độc lập (Three-Stage Cascaded Pipeline)
trên Dataset Hợp nhất duy nhất (`dataset/aiops_network_dataset.csv`).

Luồng điều phối tại tầng Hệ thống:
  - Bước 1: Gọi **Model 1 (`model1_eif_detector.pkl`)** tự rút trích 14 đặc trưng tức thời để kiểm tra
    xem hiện tại hệ thống có đang suy giảm tức thời không (`model1_eif_degraded`).
  - Bước 2: Gọi **Model 2 (`model2_xgboost_forecaster.pkl`)** tự rút trích 84 đặc trưng cửa sổ trượt
    để dự báo xác suất 5 phút tới vẫn tiếp tục suy giảm (`model2_xgb_5m_prob_pct`).
  - Bước 3: Hệ thống kiểm tra điều kiện: nếu `model1_eif_degraded == True` VÀ xác suất dự báo của
    Model 2 >= Ngưỡng Hệ thống (`--threshold`, mặc định 60%), Hệ thống mới gọi
    **Model 3 (`model3_rca_classifier.pkl`)** (tự rút trích 42 đặc trưng đa tầng tức thời) để chẩn đoán
    nguyên nhân gốc rễ (RCA) và kích hoạt hành động tự động khắc phục!
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List

import joblib
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR / "collector"))
sys.path.insert(0, str(AI_DIR))

import eif_model  # noqa: F401, E402

MODELS_DIR = AI_DIR / "models"
DATASET_DIR = AI_DIR / "dataset"
UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"

SYSTEM_REMEDIATION_POLICY = {
    "Normal": "NONE (MONITOR_ONLY)",
    "WanCongestion": "REROUTE_OSPF_WAN2 (Cost 10 -> 100)",
    "WanLossLatency": "REROUTE_OSPF_WAN2 (Gray Failure Failover)",
    "SynFlood": "BLOCK_IP_FIREWALL (iptables DROP 203.0.113.10)",
    "PortScan": "BLOCK_INSIDER_HOST (iptables DROP 192.168.10.30)",
    "SshBruteforce": "BLOCK_SSH_ATTACKER (iptables DROP port 22)",
    "HttpFlood": "RATE_LIMIT_HTTP (hashlimit + IP Block)",
}


def orchestrate_three_models(
    row: Dict[str, float],
    m1_bundle: Dict,
    m2_bundle: Dict,
    m3_bundle: Dict,
    forecast_threshold: float = 0.60,
) -> Dict:
    # 1. Gọi Model 1 (Extended Isolation Forest - tự chọn 14 đặc trưng tức thời)
    m1_cols: List[str] = m1_bundle["feature_names"]
    x_m1 = np.array([[float(row.get(c, 0.0)) for c in m1_cols]])
    x_m1_s = np.arcsinh(m1_bundle["scaler"].transform(x_m1))
    eif_score = float(m1_bundle["model"].anomaly_score(x_m1_s)[0])
    is_m1_degraded = bool(m1_bundle["model"].predict(x_m1_s)[0] == 1)

    # 2. Gọi Model 2 (XGBoost Forecaster - tự chọn 84 đặc trưng cửa sổ trượt)
    m2_cols: List[str] = m2_bundle["feature_names"]
    x_m2 = np.array([[float(row.get(c, 0.0)) for c in m2_cols]])
    x_m2_s = m2_bundle["scaler"].transform(x_m2)
    prob_5m = float(m2_bundle["model"].predict_proba(x_m2_s)[0][1])

    # 3. Hệ thống (Policy Engine) kiểm tra điều kiện kích hoạt Model 3
    trigger_model3 = is_m1_degraded and (prob_5m >= forecast_threshold)

    if trigger_model3:
        m3_cols: List[str] = m3_bundle["feature_names"]
        x_m3 = np.array([[float(row.get(c, 0.0)) for c in m3_cols]])
        x_m3_s = m3_bundle["scaler"].transform(x_m3)
        probs_m3 = m3_bundle["model"].predict_proba(x_m3_s)[0]
        pred_idx = int(np.argmax(probs_m3))
        fault_label = m3_bundle["label_encoder"].inverse_transform([pred_idx])[0]
        confidence = round(float(probs_m3[pred_idx]) * 100.0, 2)
        action = SYSTEM_REMEDIATION_POLICY.get(fault_label, "NONE")
    else:
        fault_label = "Not Invoked"
        confidence = 0.0
        action = "NONE (MONITOR_ONLY)"

    return {
        "model1_eif_degraded": is_m1_degraded,
        "model1_eif_score": round(eif_score, 4),
        "model2_xgb_5m_prob_pct": round(prob_5m * 100.0, 2),
        "system_triggers_model3": trigger_model3,
        "model3_fault_class": fault_label,
        "model3_confidence_pct": confidence,
        "system_remediation_action": action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm thử Hệ thống Điều phối 3 Model Độc lập trên Dataset Hợp nhất."
    )
    parser.add_argument("--dataset", type=Path, default=UNIFIED_CSV)
    parser.add_argument("--threshold", type=float, default=0.60)
    args = parser.parse_args()

    m1_bundle = joblib.load(MODELS_DIR / "model1_eif_detector.pkl")
    m2_bundle = joblib.load(MODELS_DIR / "model2_xgboost_forecaster.pkl")
    m3_bundle = joblib.load(MODELS_DIR / "model3_rca_classifier.pkl")

    df = pd.read_csv(args.dataset).set_index("timestamp")

    print("=" * 112)
    print(
        f"KIỂM THỬ PIPELINE 3 MODEL TRÊN DATASET HỢP NHẤT: MODEL 1 (EIF) -> MODEL 2 (XGB 5M >= {args.threshold*100:.0f}%) -> MODEL 3 (RCA)"
    )
    print("=" * 112)
    print(
        f"{'Kịch bản thực tế':<18} | {'Model 1 (EIF)':<15} | {'Model 2 (XGB 5m)':<16} | {'Gọi Model 3?':<15} | {'Model 3 (RCA)':<19} | {'Hành động Khắc phục'}"
    )
    print("-" * 112)

    # 1. Kiểm thử kịch bản Gai nhiễu thoáng qua (TransientSpike: label == Normal nhưng current_degraded == 1)
    spike_rows = df[(df["label"] == "Normal") & (df["current_degraded"] == 1)]
    if not spike_rows.empty:
        spike_ts = spike_rows.index[len(spike_rows) // 2]
        res = orchestrate_three_models(
            df.loc[spike_ts].to_dict(),
            m1_bundle,
            m2_bundle,
            m3_bundle,
            forecast_threshold=args.threshold,
        )
        m1_s = "BẤT THƯỜNG (1)" if res["model1_eif_degraded"] else "BÌNH THƯỜNG(0)"
        m2_s = f"{res['model2_xgb_5m_prob_pct']:>6.2f}%"
        trig_s = "KÍCH HOẠT (Yes)" if res["system_triggers_model3"] else "BỎ QUA (No)"
        m3_s = (
            f"{res['model3_fault_class']} ({res['model3_confidence_pct']:.0f}%)"
            if res["system_triggers_model3"]
            else "Không gọi M3"
        )
        print(
            f"{'TransientSpike':<18} | {m1_s:<15} | {m2_s:<16} | {trig_s:<15} | {m3_s:<19} | {res['system_remediation_action']}"
        )

    # 2. Kiểm thử 7 kịch bản chuẩn
    for label_name, group in df.groupby("label"):
        valid_ts = [
            ts
            for ts in group.index
            if (label_name != "Normal" or df.loc[ts, "current_degraded"] == 0)
        ]
        if not valid_ts:
            continue
        mid_ts = valid_ts[len(valid_ts) // 2]
        res = orchestrate_three_models(
            df.loc[mid_ts].to_dict(),
            m1_bundle,
            m2_bundle,
            m3_bundle,
            forecast_threshold=args.threshold,
        )
        m1_s = "BẤT THƯỜNG (1)" if res["model1_eif_degraded"] else "BÌNH THƯỜNG(0)"
        m2_s = f"{res['model2_xgb_5m_prob_pct']:>6.2f}%"
        trig_s = "KÍCH HOẠT (Yes)" if res["system_triggers_model3"] else "BỎ QUA (No)"
        m3_s = (
            f"{res['model3_fault_class']} ({res['model3_confidence_pct']:.0f}%)"
            if res["system_triggers_model3"]
            else "Không gọi M3"
        )
        print(
            f"{label_name:<18} | {m1_s:<15} | {m2_s:<16} | {trig_s:<15} | {m3_s:<19} | {res['system_remediation_action']}"
        )

    print("=" * 112)
    return 0


if __name__ == "__main__":
    sys.exit(main())
