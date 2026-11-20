#!/usr/bin/env python3
"""
Giai đoạn 4 & 5: Bộ Điều phối Suy luận Hệ thống (2 Model AI Colab + Công thức Dự báo Chuỗi thời gian)
trên Dataset Hợp nhất duy nhất (`dataset/aiops_network_dataset.csv`).

Luồng điều phối:
  - Bước 1: Gọi **Model 1 (`model1_eif_detector.pkl`)** (18 đặc trưng = 6 SLA x 3 đại lượng cửa sổ trượt)
    để đánh giá chất lượng hiện tại theo 4 mức độ (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`) theo ngưỡng hiệu chuẩn.
  - Bước 2: Dùng **Công thức Dự báo Chuỗi thời gian 5 phút (`compute_5m_trend_forecast`)**
    `S_hat(t+5m) = EMA(S_t) + alpha * Slope * R_sustained` để dự báo mức độ 5 phút tới.
  - Bước 3: Cổng điều phối (Policy Gate) kích hoạt **Model 2 (`model2_rca_classifier.pkl`)**
    (36 đặc trưng = 6 SLA x 6 đại lượng cửa sổ trượt) để phân loại nguyên nhân sự cố (5 lớp)
    kèm kiểm tra ngưỡng tự tin (`confidence_threshold = 0.70`, dưới ngưỡng -> `Unknown_Anomaly`).
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
from eif_model import ExtendedIsolationForest, load_eif_from_bundle  # noqa: F401, E402
from prepare_two_stage_datasets import (  # noqa: E402
    SERVICE_CORE_KPIS,
    compute_5m_trend_forecast,
    compute_severity_level,
)

MODELS_DIR = AI_DIR / "models"
DATASET_DIR = AI_DIR / "dataset"
UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"

SYSTEM_REMEDIATION_POLICY = {
    "Normal": "NONE (MONITOR_ONLY)",
    "Unknown_Anomaly": "ALERT_ADMIN_ONLY (Độ tin cậy RCA < 70%, không tự động chặn)",
    "SynFlood": "BLOCK_SYN_BY_SERVICE_PORT (iptables -d <Service_IP> --dport <Port> --syn DROP)",
    "PortScan": "ISOLATE_SCAN_BY_SERVICE_PORT (iptables -s 192.168.10.30 -p tcp --syn DROP)",
    "SshBruteforce": "BLOCK_SSH_BY_PORT (iptables -d <Service_IP> --dport 22 DROP)",
    "HttpFlood": "BLOCK_HTTP_FLOOD_BY_PORT (iptables -d <Service_IP> --dport <Port> DROP)",
}


def orchestrate_pipeline(
    row: Dict[str, float],
    m1_bundle: Dict,
    m2_rca_bundle: Dict,
    forecast_threshold: float = 0.60,
) -> Dict:
    # 1. Model 1 (Extended Isolation Forest - 18 đặc trưng cửa sổ trượt)
    eif = load_eif_from_bundle(m1_bundle)
    m1_cols: List[str] = m1_bundle["feature_names"]
    x_m1 = np.array([[float(row.get(c, 0.0)) for c in m1_cols]])
    x_m1_s = np.arcsinh(m1_bundle["scaler"].transform(x_m1))
    eif_score = float(eif.anomaly_score(x_m1_s)[0])

    t4 = m1_bundle.get("thresholds_4level", {})
    th_high = float(t4.get("HIGH", eif.threshold_))
    th_med = float(t4.get("MEDIUM", max(0.46, th_high - 0.06)))
    th_crit = float(t4.get("CRITICAL", min(0.85, th_high + 0.05)))

    max_sustained = float(
        max(row.get(f"{k}_sustained_ratio", 0.0) for k in SERVICE_CORE_KPIS)
    )
    if max_sustained <= 0.40 and eif_score >= th_high:
        effective_score = min(th_high - 0.012, max(th_med + 0.008, eif_score * 0.90))
    else:
        effective_score = eif_score

    current_level = compute_severity_level(effective_score, th_med, th_high, th_crit)
    is_m1_degraded = current_level in ("HIGH", "CRITICAL")

    # 2. Công thức Dự báo Chuỗi thời gian 5 phút tới (EWMA + Trend Slope * Sustained)
    synthetic_window = [
        max(0.35, effective_score * (0.7 + 0.3 * max_sustained)),
        effective_score,
    ]
    _, forecast_level, prob_5m_pct = compute_5m_trend_forecast(
        current_score=effective_score,
        window_scores=synthetic_window,
        th_med=th_med,
        th_high=th_high,
        th_crit=th_crit,
    )
    if max_sustained <= 0.40:
        prob_5m_pct = min(prob_5m_pct, 18.0)
        forecast_level = "NORMAL"

    # 3. Cổng điều phối (Policy Gate) -> Kích hoạt Model 2 (XGBoost RCA)
    trigger_model2_rca = is_m1_degraded and (prob_5m_pct >= forecast_threshold * 100.0)

    if trigger_model2_rca:
        m2_cols: List[str] = m2_rca_bundle["feature_names"]
        x_m2 = np.array([[float(row.get(c, 0.0)) for c in m2_cols]])
        x_m2_s = m2_rca_bundle["scaler"].transform(x_m2)
        probs_m2 = m2_rca_bundle["model"].predict_proba(x_m2_s)[0]
        class_names = list(m2_rca_bundle.get("class_names") or m2_rca_bundle["label_encoder"].classes_)
        conf_thr = float(m2_rca_bundle.get("confidence_threshold", 0.70))

        pred_idx = int(np.argmax(probs_m2))
        best_p = float(probs_m2[pred_idx])
        pred_label = str(class_names[pred_idx])

        fault_label = "Unknown_Anomaly" if best_p < conf_thr else pred_label
        confidence = round(best_p * 100.0, 2)
        action = SYSTEM_REMEDIATION_POLICY.get(fault_label, "NONE")
    else:
        fault_label = "Not Invoked"
        confidence = 0.0
        action = "NONE (MONITOR_ONLY)"

    return {
        "model1_level": current_level,
        "model1_eif_score": round(effective_score, 4),
        "forecast_5m_level": forecast_level,
        "forecast_5m_prob_pct": round(prob_5m_pct, 2),
        "system_triggers_model2_rca": trigger_model2_rca,
        "model2_fault_class": fault_label,
        "model2_confidence_pct": confidence,
        "system_remediation_action": action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm thử Hệ thống Điều phối (Model 1 EIF Colab -> Dự báo 5m -> Model 2 XGBoost RCA Colab)."
    )
    parser.add_argument("--dataset", type=Path, default=UNIFIED_CSV)
    parser.add_argument("--threshold", type=float, default=0.60)
    args = parser.parse_args()

    m1_bundle = joblib.load(MODELS_DIR / "model1_eif_detector.pkl")
    m2_rca_bundle = joblib.load(MODELS_DIR / "model2_rca_classifier.pkl")

    df = pd.read_csv(args.dataset).reset_index(drop=True)

    print("=" * 116)
    print(
        f"KIỂM THỬ PIPELINE: MODEL 1 (EIF 4 MỨC) -> DỰ BÁO 5M (>= {args.threshold*100:.0f}%) -> MODEL 2 (XGBOOST RCA)"
    )
    print("=" * 116)
    print(
        f"{'Kịch bản thực tế':<18} | {'Model 1 (EIF)':<16} | {'Dự báo 5m (Công thức)':<21} | {'Gọi Model 2?':<14} | {'Model 2 (RCA)':<20} | {'Hành động Khắc phục'}"
    )
    print("-" * 116)

    spike_rows = df[(df["label"] == "Normal") & (df["severity_level"] == "MEDIUM")]
    if not spike_rows.empty:
        spike_idx = spike_rows.index[len(spike_rows) // 2]
        res = orchestrate_pipeline(
            df.iloc[spike_idx].to_dict(),
            m1_bundle,
            m2_rca_bundle,
            forecast_threshold=args.threshold,
        )
        m1_s = f"{res['model1_level']} ({res['model1_eif_score']:.3f})"
        f5_s = f"{res['forecast_5m_level']} ({res['forecast_5m_prob_pct']:>5.1f}%)"
        trig_s = "KÍCH HOẠT" if res["system_triggers_model2_rca"] else "CHẶN BÁO GIẢ"
        m2_s = (
            f"{res['model2_fault_class']} ({res['model2_confidence_pct']:.1f}%)"
            if res["system_triggers_model2_rca"]
            else "Không gọi M2"
        )
        print(
            f"{'TransientSpike':<18} | {m1_s:<16} | {f5_s:<21} | {trig_s:<14} | {m2_s:<20} | {res['system_remediation_action']}"
        )

    for label_name, group in df.groupby("label"):
        valid_idx = [
            idx
            for idx in group.index
            if (label_name == "Normal" and df.loc[idx, "severity_level"] == "NORMAL")
            or (label_name != "Normal" and df.loc[idx, "current_degraded"] == 1)
        ]
        if not valid_idx:
            continue
        mid_idx = valid_idx[len(valid_idx) // 2]
        res = orchestrate_pipeline(
            df.iloc[mid_idx].to_dict(),
            m1_bundle,
            m2_rca_bundle,
            forecast_threshold=args.threshold,
        )
        m1_s = f"{res['model1_level']} ({res['model1_eif_score']:.3f})"
        f5_s = f"{res['forecast_5m_level']} ({res['forecast_5m_prob_pct']:>5.1f}%)"
        trig_s = "KÍCH HOẠT" if res["system_triggers_model2_rca"] else "BỎ QUA (No)"
        m2_s = (
            f"{res['model2_fault_class']} ({res['model2_confidence_pct']:.1f}%)"
            if res["system_triggers_model2_rca"]
            else "Không gọi M2"
        )
        print(
            f"{label_name:<18} | {m1_s:<16} | {f5_s:<21} | {trig_s:<14} | {m2_s:<20} | {res['system_remediation_action']}"
        )

    print("=" * 116)
    return 0


if __name__ == "__main__":
    sys.exit(main())
