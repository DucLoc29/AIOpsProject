#!/usr/bin/env python3
"""
Giai đoạn 4: Huấn luyện 2 Mô hình AI Độc lập từ 1 Dataset Hợp nhất duy nhất (`dataset/aiops_network_dataset.csv`).
(Bước Dự báo 5 phút sử dụng Công thức Toán học Chuỗi thời gian `EWMA + Trend Slope * Sustained Ratio` nên không cần train ML).

Kiến trúc 2 Model AI + 1 Công thức Dự báo:
  - MODEL 1 (`ai/models/model1_eif_detector.pkl`):
    + Thuật toán: Extended Isolation Forest (EIF)
    + Tự động chọn: 18 đặc trưng cửa sổ trượt (`6 thông số SLA x 3 đại lượng: Current, _roll_mean, _sustained_ratio`)
    + Đầu ra: Điểm bất thường EIF -> Phân loại 4 mức độ hiện tại (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`).

  - CÔNG THỨC DỰ BÁO 5 PHÚT (`compute_5m_trend_forecast`):
    + Công thức động học chuỗi thời gian: `S_hat(t+5m) = EMA(S_t) + alpha * Slope(S_{t-W..t}) * R_sustained`
    + Đầu ra: Dự báo 4 mức độ tương lai (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`) & lọc gai nhiễu thoáng qua.

  - MODEL 2 (`ai/models/model2_rca_classifier.pkl`):
    + Thuật toán: XGBoost Multi-class Classifier (Phân loại Đa lớp Nguyên nhân Sự cố - RCA)
    + Tự động chọn: 36 đặc trưng cửa sổ trượt (`6 thông số SLA x 6 đại lượng thời gian`)
    + Cột mục tiêu: `label` (5 lớp: `Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`).
"""

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    IsolationForest,
    RandomForestClassifier,
)
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder, RobustScaler, StandardScaler
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AI_DIR / "collector"))
sys.path.insert(0, str(AI_DIR))

from eif_model import ExtendedIsolationForest  # noqa: E402
from prepare_two_stage_datasets import get_model_feature_sets  # noqa: E402

DATASET_DIR = AI_DIR / "dataset"
DEFAULT_UNIFIED_CSV = DATASET_DIR / "aiops_network_dataset.csv"
MODELS_DIR = AI_DIR / "models"
REPORTS_DIR = AI_DIR / "reports"


def train_model1_eif(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 82)
    print("1. HUẤN LUYỆN MODEL 1: EXTENDED ISOLATION FOREST (ĐÁNH GIÁ HIỆN TẠI 4 MỨC ĐỘ)")
    print(f"   -> Tự động trích xuất {len(feat_cols)} đặc trưng cửa sổ trượt (6 SLA x 3) | Target: current_degraded")
    print("=" * 82)

    target_col = "current_degraded"
    X = df[feat_cols].values
    y = df[target_col].values.astype(int)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=random_state
    )

    scaler = RobustScaler().fit(X_tr[y_tr == 0])
    X_tr_s = np.arcsinh(scaler.transform(X_tr))
    X_te_s = np.arcsinh(scaler.transform(X_te))

    print(
        f"{'Thuật toán (Model 1)':<32} | {'Accuracy':<10} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10} | {'ROC-AUC':<10}"
    )
    print("-" * 88)

    std_iso = IsolationForest(
        n_estimators=300, contamination=0.05, random_state=random_state, n_jobs=-1
    ).fit(X_tr_s[y_tr == 0])
    std_preds = np.where(std_iso.predict(X_te_s) == -1, 1, 0)
    std_scores = -std_iso.score_samples(X_te_s)
    std_f1 = f1_score(y_te, std_preds, zero_division=0)
    std_auc = roc_auc_score(y_te, std_scores)
    print(
        f"{'Standard Isolation Forest':<32} | {accuracy_score(y_te, std_preds):<10.4f} | "
        f"{precision_score(y_te, std_preds, zero_division=0):<10.4f} | "
        f"{recall_score(y_te, std_preds, zero_division=0):<10.4f} | {std_f1:<10.4f} | {std_auc:<10.4f}"
    )

    eif = ExtendedIsolationForest(
        n_estimators=360,
        n_rotations=12,
        extension_level=len(feat_cols) - 1,
        contamination=0.06,
        random_state=random_state,
    ).fit(X_tr_s[y_tr == 0])
    tr_scores = eif.anomaly_score(X_tr_s)
    best_th, best_f1 = eif.threshold_, -1.0
    for q in np.linspace(30, 65, 71):
        th = float(np.percentile(tr_scores, q))
        f1_c = f1_score(y_tr, (tr_scores >= th).astype(int), zero_division=0)
        if f1_c > best_f1:
            best_f1, best_th = f1_c, th
    eif.threshold_ = best_th

    eif_preds = eif.predict(X_te_s)
    eif_scores = eif.anomaly_score(X_te_s)
    eif_acc = accuracy_score(y_te, eif_preds)
    eif_prec = precision_score(y_te, eif_preds, zero_division=0)
    eif_rec = recall_score(y_te, eif_preds, zero_division=0)
    eif_f1 = f1_score(y_te, eif_preds, zero_division=0)
    eif_auc = roc_auc_score(y_te, eif_scores)
    print(
        f"{'Extended Isolation Forest (EIF)':<32} | {eif_acc:<10.4f} | {eif_prec:<10.4f} | "
        f"{eif_rec:<10.4f} | {eif_f1:<10.4f} | {eif_auc:<10.4f}"
    )

    final_scaler = RobustScaler().fit(X[y == 0])
    X_all_s = np.arcsinh(final_scaler.transform(X))
    final_eif = ExtendedIsolationForest(
        n_estimators=360,
        n_rotations=12,
        extension_level=len(feat_cols) - 1,
        contamination=0.06,
        random_state=random_state,
    ).fit(X_all_s[y == 0])
    all_scores = final_eif.anomaly_score(X_all_s)
    best_th_all, best_f1_all = final_eif.threshold_, -1.0
    for q in np.linspace(30, 65, 71):
        th = float(np.percentile(all_scores, q))
        f1_c = f1_score(y, (all_scores >= th).astype(int), zero_division=0)
        if f1_c > best_f1_all:
            best_f1_all, best_th_all = f1_c, th
    final_eif.threshold_ = best_th_all

    metrics = {
        "ExtendedIsolationForest": {
            "accuracy": round(float(eif_acc), 4),
            "precision": round(float(eif_prec), 4),
            "recall": round(float(eif_rec), 4),
            "f1_score": round(float(eif_f1), 4),
            "roc_auc": round(float(eif_auc), 4),
        },
        "StandardIsolationForest": {
            "f1_score": round(float(std_f1), 4),
            "roc_auc": round(float(std_auc), 4),
        },
    }

    artifact = {
        "model_type": "ExtendedIsolationForest",
        "model": final_eif,
        "scaler": final_scaler,
        "feature_names": feat_cols,
        "target_name": target_col,
        "metrics": metrics,
    }
    return artifact, metrics


def train_model2_rca_classifier(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 82)
    print("2. HUẤN LUYỆN MODEL 2: XGBOOST MULTI-CLASS ROOT-CAUSE FAULT CLASSIFIER (RCA)")
    print(f"   -> Tự động trích xuất {len(feat_cols)} đặc trưng cửa sổ trượt (6 SLA x 6) | Target: label (5 lớp)")
    print("=" * 82)

    target_col = "label"
    df2 = df[~((df["label"] == "Normal") & (df["current_degraded"] == 1))].copy()

    X = df2[feat_cols].values
    label_encoder = LabelEncoder()
    y = label_encoder.fit_transform(df2[target_col].values)
    class_names = list(label_encoder.classes_)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=random_state
    )

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)
    X_all_s = StandardScaler().fit_transform(X)

    candidates = {
        "XGBoost_RCA": XGBClassifier(
            n_estimators=300,
            learning_rate=0.06,
            max_depth=4,
            subsample=0.95,
            colsample_bytree=0.95,
            reg_alpha=0.05,
            reg_lambda=1.0,
            eval_metric="mlogloss",
            random_state=random_state,
            n_jobs=-1,
        ),
        "RandomForest_RCA": RandomForestClassifier(
            n_estimators=300, max_depth=12, class_weight="balanced", random_state=random_state, n_jobs=-1
        ),
        "ExtraTrees_RCA": ExtraTreesClassifier(
            n_estimators=300, max_depth=14, class_weight="balanced", random_state=random_state, n_jobs=-1
        ),
        "GradientBoosting_RCA": GradientBoostingClassifier(
            n_estimators=150, learning_rate=0.08, max_depth=4, random_state=random_state
        ),
    }

    print(
        f"{'Thuật toán (Model 2 - RCA)':<32} | {'Accuracy':<10} | {'Macro-Prec':<10} | {'Macro-Rec':<10} | {'Macro-F1':<10} | {'CV-5 F1':<10}"
    )
    print("-" * 88)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    comparison = {}
    xgb_preds_te = None

    for name, clf in candidates.items():
        clf.fit(X_tr_s, y_tr)
        preds = clf.predict(X_te_s)
        cv_scores = cross_val_score(clf, X_all_s, y, cv=cv, scoring="f1_macro", n_jobs=-1)

        acc = accuracy_score(y_te, preds)
        prec = precision_score(y_te, preds, average="macro", zero_division=0)
        rec = recall_score(y_te, preds, average="macro", zero_division=0)
        f1 = f1_score(y_te, preds, average="macro", zero_division=0)
        cv_mean = float(np.mean(cv_scores))

        comparison[name] = {
            "accuracy": round(float(acc), 4),
            "macro_precision": round(float(prec), 4),
            "macro_recall": round(float(rec), 4),
            "macro_f1": round(float(f1), 4),
            "cv5_f1_mean": round(cv_mean, 4),
        }

        print(
            f"{name:<32} | {acc:<10.4f} | {prec:<10.4f} | {rec:<10.4f} | {f1:<10.4f} | {cv_mean:<10.4f}"
        )

        if name == "XGBoost_RCA":
            xgb_preds_te = preds

    deployed_name = "XGBoost_RCA"
    cm = confusion_matrix(y_te, xgb_preds_te)
    final_scaler = StandardScaler()
    X_all_scaled = final_scaler.fit_transform(X)
    final_clf = candidates[deployed_name]
    final_clf.fit(X_all_scaled, y)

    importances = getattr(final_clf, "feature_importances_", np.zeros(len(feat_cols)))
    feat_imp_pairs = sorted(
        zip(feat_cols, [round(float(x), 4) for x in importances]),
        key=lambda item: item[1],
        reverse=True,
    )

    artifact = {
        "model_type": deployed_name,
        "model": final_clf,
        "scaler": final_scaler,
        "label_encoder": label_encoder,
        "class_names": class_names,
        "feature_names": feat_cols,
        "feature_importances": dict(feat_imp_pairs),
        "metrics_on_test": comparison[deployed_name],
        "all_candidates": comparison,
        "confusion_matrix": cm.tolist(),
    }
    return artifact, comparison


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Huấn luyện 2 Model AIOps (Model 1: EIF & Model 2: XGBoost RCA) từ 1 Dataset Hợp nhất."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_UNIFIED_CSV)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    args = parser.parse_args()

    args.models_dir.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.dataset).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    m1_feats, m2_rca_feats = get_model_feature_sets(df)

    print(f"[*] Đã nạp Dataset Hợp nhất: {args.dataset}")
    print(f"    Kích thước: {df.shape[0]} mẫu x {df.shape[1]} cột")

    m1_art, m1_met = train_model1_eif(df, m1_feats)
    m2_art, m2_met = train_model2_rca_classifier(df, m2_rca_feats)

    p1 = args.models_dir / "model1_eif_detector.pkl"
    p2 = args.models_dir / "model2_rca_classifier.pkl"

    joblib.dump(m1_art, p1)
    joblib.dump(m2_art, p2)

    # Xóa các file model cũ (model2_xgboost_forecaster.pkl, model3_rca_classifier.pkl, fault_classifier.pkl) để không còn tàn dư
    for old_file in ("model2_xgboost_forecaster.pkl", "model3_rca_classifier.pkl", "fault_classifier.pkl"):
        old_p = args.models_dir / old_file
        if old_p.exists():
            old_p.unlink()

    summary = {
        "architecture": "Two-Model + Time-Series Trend Formula AIOps Pipeline (Unified 6-SLA Dataset)",
        "unified_dataset": str(args.dataset.relative_to(PROJECT_ROOT)),
        "model1_eif_detector": m1_met,
        "forecast_5m_formula": "S_hat(t+5m) = EMA(S_t) + alpha * Slope(S_{t-W..t}) * R_sustained",
        "model2_rca_classifier": m2_met,
    }
    report_path = REPORTS_DIR / "training_metrics.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 82)
    print("[✓] ĐÃ HUẤN LUYỆN VÀ ĐÓNG GÓI 2 MODEL AI (+ 1 CÔNG THỨC DỰ BÁO 5M):")
    print(f"  1. Model 1 (EIF Đánh giá Hiện tại 4 mức) : {p1}")
    print("  2. Dự báo 5 Phút Tới                     : Công thức EWMA + Trend Slope * Sustained Ratio")
    print(f"  3. Model 2 (XGBoost RCA Phân loại 5 lớp) : {p2}")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    sys.exit(main())
