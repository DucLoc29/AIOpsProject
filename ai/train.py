#!/usr/bin/env python3
"""
Giai đoạn 4: Huấn luyện 3 Mô hình AI Độc lập từ 1 Dataset Hợp nhất duy nhất (`dataset/aiops_network_dataset.csv`).

Kiến trúc 3 Model riêng biệt (mỗi Model tự động chọn tập đặc trưng và cột nhãn mục tiêu của riêng mình):
  - MODEL 1 (`ai/models/model1_eif_detector.pkl`):
    + Thuật toán: Extended Isolation Forest (EIF)
    + Tự động chọn: 14 đặc trưng KPI cốt lõi tức thời (`KEY_TIMESERIES_KPIS`)
    + Cột mục tiêu: `current_degraded` (0 hoặc 1).

  - MODEL 2 (`ai/models/model2_xgboost_forecaster.pkl`):
    + Thuật toán: XGBoost Time-Series Forecaster
    + Tự động chọn: 84 đặc trưng chuỗi thời gian cửa sổ trượt (14 tức thời + 70 đặc trưng `_roll_mean`, `_roll_std`, `_delta1`, `_trend_slope`, `_sustained_ratio`)
    + Cột mục tiêu: `degraded_forecast_5m` (0 hoặc 1).

  - MODEL 3 (`ai/models/model3_rca_classifier.pkl`):
    + Thuật toán: XGBoost Multi-class Classifier (Phân loại Đa lớp Nguyên nhân Gốc rễ)
    + Tự động chọn: 42 đặc trưng đa tầng tức thời (Network + Firewall + Server/App/DB)
    + Cột mục tiêu: `label` (7 lớp nguyên nhân gốc rễ).
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
    print("1. HUẤN LUYỆN MODEL 1: EXTENDED ISOLATION FOREST (PHÁT HIỆN SUY GIẢM TỨC THỜI)")
    print(f"   -> Tự động trích xuất {len(feat_cols)} đặc trưng tức thời | Target: current_degraded")
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

    # Standard Isolation Forest
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

    # Extended Isolation Forest (EIF)
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

    # Fit trên toàn bộ dữ liệu
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


def train_model2_xgboost_forecaster(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 82)
    print("2. HUẤN LUYỆN MODEL 2: XGBOOST TIME-SERIES FORECASTER (DỰ BÁO SUY GIẢM 5 PHÚT)")
    print(f"   -> Tự động trích xuất {len(feat_cols)} đặc trưng cửa sổ trượt | Target: degraded_forecast_5m")
    print("=" * 82)

    target_col = "degraded_forecast_5m"
    X = df[feat_cols].values
    y = df[target_col].values.astype(int)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=random_state
    )

    scaler = RobustScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)
    X_all_s = RobustScaler().fit_transform(X)

    candidates = {
        "XGBoost_Forecaster": XGBClassifier(
            n_estimators=250,
            learning_rate=0.07,
            max_depth=5,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_alpha=0.1,
            reg_lambda=1.0,
            eval_metric="logloss",
            random_state=random_state,
            n_jobs=-1,
        ),
        "RandomForest_Forecaster": RandomForestClassifier(
            n_estimators=300, max_depth=12, class_weight="balanced", random_state=random_state, n_jobs=-1
        ),
        "ExtraTrees_Forecaster": ExtraTreesClassifier(
            n_estimators=300, max_depth=14, class_weight="balanced", random_state=random_state, n_jobs=-1
        ),
        "GradientBoosting_Forecaster": GradientBoostingClassifier(
            n_estimators=200, learning_rate=0.08, max_depth=4, random_state=random_state
        ),
    }

    print(
        f"{'Thuật toán (Model 2)':<32} | {'Accuracy':<10} | {'Precision':<10} | {'Recall':<10} | {'F1-Score':<10} | {'ROC-AUC':<10}"
    )
    print("-" * 88)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    comparison = {}

    for name, clf in candidates.items():
        clf.fit(X_tr_s, y_tr)
        preds = clf.predict(X_te_s)
        probs = clf.predict_proba(X_te_s)[:, 1]
        cv_scores = cross_val_score(clf, X_all_s, y, cv=cv, scoring="f1", n_jobs=-1)

        acc = accuracy_score(y_te, preds)
        prec = precision_score(y_te, preds, zero_division=0)
        rec = recall_score(y_te, preds, zero_division=0)
        f1 = f1_score(y_te, preds, zero_division=0)
        auc = roc_auc_score(y_te, probs)

        comparison[name] = {
            "accuracy": round(float(acc), 4),
            "precision": round(float(prec), 4),
            "recall": round(float(rec), 4),
            "f1_score": round(float(f1), 4),
            "roc_auc": round(float(auc), 4),
            "cv5_f1_mean": round(float(np.mean(cv_scores)), 4),
        }

        print(
            f"{name:<32} | {acc:<10.4f} | {prec:<10.4f} | {rec:<10.4f} | {f1:<10.4f} | {auc:<10.4f}"
        )

    final_scaler = RobustScaler()
    X_all_scaled = final_scaler.fit_transform(X)
    final_xgb = candidates["XGBoost_Forecaster"]
    final_xgb.fit(X_all_scaled, y)

    artifact = {
        "model_type": "XGBoost_5mForecaster",
        "model": final_xgb,
        "scaler": final_scaler,
        "feature_names": feat_cols,
        "window_size": 5,
        "target_name": target_col,
        "metrics": comparison,
    }
    return artifact, comparison


def train_model3_rca_classifier(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 82)
    print("3. HUẤN LUYỆN MODEL 3: XGBOOST MULTI-CLASS ROOT-CAUSE FAULT CLASSIFIER (RCA)")
    print(f"   -> Tự động trích xuất {len(feat_cols)} đặc trưng đa tầng tức thời | Target: label (7 lớp)")
    print("=" * 82)

    target_col = "label"
    # Lọc bỏ các dòng gai nhiễu nhân tạo (Normal nhưng current_degraded=1) để giữ đặc trưng chuẩn 7 lớp
    df3 = df[~((df["label"] == "Normal") & (df["current_degraded"] == 1))].copy()

    X = df3[feat_cols].values
    label_encoder = LabelEncoder()
    y = label_encoder.fit_transform(df3[target_col].values)
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
        f"{'Thuật toán (Model 3)':<32} | {'Accuracy':<10} | {'Macro-Prec':<10} | {'Macro-Rec':<10} | {'Macro-F1':<10} | {'CV-5 F1':<10}"
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

    # Triển khai cố định XGBoost_RCA làm mô hình chính thức cho Model 3
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
        description="Huấn luyện 3 Model AIOps độc lập từ 1 Dataset Hợp nhất duy nhất."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_UNIFIED_CSV)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    args = parser.parse_args()

    args.models_dir.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.dataset).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    m1_feats, m2_feats, m3_feats = get_model_feature_sets(df)

    print(f"[*] Đã nạp Dataset Hợp nhất: {args.dataset}")
    print(f"    Kích thước: {df.shape[0]} mẫu x {df.shape[1]} cột")

    m1_art, m1_met = train_model1_eif(df, m1_feats)
    m2_art, m2_met = train_model2_xgboost_forecaster(df, m2_feats)
    m3_art, m3_met = train_model3_rca_classifier(df, m3_feats)

    p1 = args.models_dir / "model1_eif_detector.pkl"
    p2 = args.models_dir / "model2_xgboost_forecaster.pkl"
    p3 = args.models_dir / "model3_rca_classifier.pkl"

    joblib.dump(m1_art, p1)
    joblib.dump(m2_art, p2)
    joblib.dump(m3_art, p3)

    # Giữ cả file alias để tương thích
    joblib.dump(m3_art, args.models_dir / "fault_classifier.pkl")

    summary = {
        "architecture": "Three-Stage Cascaded AIOps Pipeline (Unified Dataset: aiops_network_dataset.csv)",
        "unified_dataset": str(args.dataset.relative_to(PROJECT_ROOT)),
        "model1_eif_instant_detector": m1_met,
        "model2_xgboost_5m_forecaster": m2_met,
        "model3_rca_classifier": m3_met,
    }
    report_path = REPORTS_DIR / "training_metrics.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 82)
    print("[✓] ĐÃ HUẤN LUYỆN VÀ ĐÓNG GÓI 3 MODEL RIÊNG BIỆT TỪ 1 DATASET HỢP NHẤT:")
    print(f"  1. Model 1 (EIF Phát hiện Tức thời)     : {p1}")
    print(f"  2. Model 2 (XGBoost Dự báo 5 Phút Tới)  : {p2}")
    print(f"  3. Model 3 (RCA Phân loại Nguyên nhân)  : {p3}")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    sys.exit(main())
