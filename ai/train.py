#!/usr/bin/env python3
"""
Giai đoạn 4: Huấn luyện 2 Mô hình AI Độc lập từ 1 Dataset Hợp nhất 100% Thực nghiệm (`ai/dataset/aiops_network_dataset.csv`).
Đồng bộ 100% kiến trúc với 2 file huấn luyện Google Colab (`kaggle_train_model1_eif.py` & `kaggle_train_model2_rca.py`):

  - MODEL 1 (`ai/models/model1_eif_detector.pkl`):
    + Thuật toán: Extended Isolation Forest (EIF, 12 phép quay trực giao x 30 cây = 360 cây)
    + Đặc trưng: 18 đặc trưng cửa sổ trượt (`6 thông số SLA x 3 đại lượng: Current, _roll_mean, _sustained_ratio`)
    + Chia tập: Train 60% / Validation 20% (hiệu chuẩn ngưỡng) / Test 20% (đánh giá độc lập)
    + Đầu ra: `eif_state` (dict thuần tránh lỗi pickle) + `thresholds_4level` (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`).

  - CÔNG THỨC DỰ BÁO 5 PHÚT (`compute_5m_trend_forecast`):
    + `S_hat(t+5m) = EMA(S_t) + alpha * Slope(S_{t-W..t}) * R_sustained`

  - MODEL 2 (`ai/models/model2_rca_classifier.pkl`):
    + Thuật toán: XGBoost Multi-class Classifier (so sánh 4 thuật toán bằng Pipeline 5-Fold StratifiedKFold CV)
    + Đặc trưng: 36 đặc trưng cửa sổ trượt (`6 thông số SLA x 6 đại lượng thời gian`)
    + Đầu ra: 5 lớp (`Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`) + `confidence_threshold = 0.70`
      (dưới 70% đẩy về `Unknown_Anomaly` để cảnh báo quản trị viên, không tự chặn nhầm).
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
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
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


def find_optimal_threshold(scores: np.ndarray, y_true: np.ndarray) -> Tuple[float, float]:
    """Tự động dò ngưỡng tối ưu F1 trên toàn dải phân vị [5%, 98%]."""
    best_th, best_f1 = float(np.median(scores)), -1.0
    for q in np.linspace(5, 98, 187):
        th = float(np.percentile(scores, q))
        f1_c = f1_score(y_true, (scores >= th).astype(int), zero_division=0)
        if f1_c > best_f1:
            best_f1, best_th = f1_c, th
    return best_th, best_f1


def train_model1_eif(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 98)
    print("1. HUẤN LUYỆN MODEL 1: EXTENDED ISOLATION FOREST (ĐÁNH GIÁ HIỆN TẠI 4 MỨC ĐỘ)")
    print(f"   -> {len(feat_cols)} đặc trưng cửa sổ trượt (6 SLA x 3) | Target: current_degraded | Split 60/20/20")
    print("=" * 98)

    target_col = "current_degraded"
    X = df[feat_cols].values
    y = df[target_col].values.astype(int)

    X_temp, X_te, y_temp, y_te = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=random_state
    )
    X_tr, X_val, y_tr, y_val = train_test_split(
        X_temp, y_temp, test_size=0.25, stratify=y_temp, random_state=random_state
    )

    # Fit RobustScaler CHỈ trên tập Normal (y_tr == 0)
    scaler = RobustScaler().fit(X_tr[y_tr == 0])
    X_tr_s = np.arcsinh(scaler.transform(X_tr))
    X_val_s = np.arcsinh(scaler.transform(X_val))
    X_te_s = np.arcsinh(scaler.transform(X_te))

    print(
        f"{'Mô hình Phát hiện Suy giảm (Test)':<36} | {'Acc':<7} | {'Prec':<7} | {'Recall':<7} | {'F1':<7} | {'ROC-AUC':<7} | {'PR-AUC':<7}"
    )
    print("-" * 98)

    # Baseline: Standard Isolation Forest
    std_iso = IsolationForest(
        n_estimators=360, contamination=0.06, random_state=random_state, n_jobs=-1
    ).fit(X_tr_s[y_tr == 0])
    std_val_scores = -std_iso.score_samples(X_val_s)
    std_best_th, _ = find_optimal_threshold(std_val_scores, y_val)
    std_te_scores = -std_iso.score_samples(X_te_s)
    std_preds = (std_te_scores >= std_best_th).astype(int)

    print(
        f"{'Standard Isolation Forest':<36} | {accuracy_score(y_te, std_preds):<7.4f} | "
        f"{precision_score(y_te, std_preds, zero_division=0):<7.4f} | "
        f"{recall_score(y_te, std_preds, zero_division=0):<7.4f} | "
        f"{f1_score(y_te, std_preds, zero_division=0):<7.4f} | "
        f"{roc_auc_score(y_te, std_te_scores):<7.4f} | "
        f"{average_precision_score(y_te, std_te_scores):<7.4f}"
    )

    # Extended Isolation Forest (EIF)
    eif = ExtendedIsolationForest(
        n_estimators=360,
        n_rotations=12,
        extension_level=len(feat_cols) - 1,
        contamination=0.06,
        random_state=random_state,
    ).fit(X_tr_s[y_tr == 0])
    eif_val_scores = eif.anomaly_score(X_val_s)
    best_th_val, _ = find_optimal_threshold(eif_val_scores, y_val)
    eif.threshold_ = best_th_val

    eif_te_scores = eif.anomaly_score(X_te_s)
    eif_preds = eif.predict(X_te_s)

    eif_acc = accuracy_score(y_te, eif_preds)
    eif_prec = precision_score(y_te, eif_preds, zero_division=0)
    eif_rec = recall_score(y_te, eif_preds, zero_division=0)
    eif_f1 = f1_score(y_te, eif_preds, zero_division=0)
    eif_auc = roc_auc_score(y_te, eif_te_scores)
    eif_pr = average_precision_score(y_te, eif_te_scores)

    print(
        f"{'Extended Isolation Forest (EIF)':<36} | {eif_acc:<7.4f} | {eif_prec:<7.4f} | "
        f"{eif_rec:<7.4f} | {eif_f1:<7.4f} | {eif_auc:<7.4f} | {eif_pr:<7.4f}"
    )
    print("=" * 98)

    tn, fp, fn, tp = confusion_matrix(y_te, eif_preds).ravel()
    print(f"[*] Confusion Matrix EIF (Test): TP={tp}, FP={fp}, TN={tn}, FN={fn} (FPR={fp/(fp+tn)*100:.2f}%)")

    # Hiệu chuẩn 4 mức ngưỡng từ tập Validation -> chống data leakage
    val_normal_scores = eif_val_scores[y_val == 0]
    calibrated_quantile = float(np.mean(val_normal_scores <= best_th_val) * 100.0)

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
    normal_all_scores = all_scores[y == 0]
    anomaly_all_scores = all_scores[y == 1]

    th_high = round(float(np.percentile(normal_all_scores, np.clip(calibrated_quantile, 85.0, 99.5))), 4)
    # Nếu có khe hở giữa phân bố Normal và Anomaly, đặt th_high vào giữa khoảng an toàn để vừa chống FP vừa Recall cao
    p99_norm = float(np.percentile(normal_all_scores, 99.0))
    p05_anom = float(np.percentile(anomaly_all_scores, 5.0))
    if p05_anom > p99_norm:
        th_high = round(float(0.5 * (p99_norm + p05_anom)), 4)

    th_med = round(min(th_high - 0.015, float(np.percentile(normal_all_scores, 94.0))), 4)
    th_crit = round(max(th_high + 0.03, float(np.percentile(anomaly_all_scores, 60.0))), 4)
    final_eif.threshold_ = th_high

    print(
        f"[*] Ngưỡng 4 mức hiệu chuẩn: NORMAL < {th_med} | MEDIUM [{th_med}, {th_high}) | "
        f"HIGH [{th_high}, {th_crit}) | CRITICAL >= {th_crit}"
    )

    metrics = {
        "ExtendedIsolationForest": {
            "accuracy": round(float(eif_acc), 4),
            "precision": round(float(eif_prec), 4),
            "recall": round(float(eif_rec), 4),
            "f1_score": round(float(eif_f1), 4),
            "roc_auc": round(float(eif_auc), 4),
            "pr_auc": round(float(eif_pr), 4),
        },
        "StandardIsolationForest": {
            "accuracy": round(float(accuracy_score(y_te, std_preds)), 4),
            "f1_score": round(float(f1_score(y_te, std_preds, zero_division=0)), 4),
            "roc_auc": round(float(roc_auc_score(y_te, std_te_scores)), 4),
        },
        "thresholds_4level": {
            "NORMAL_MAX": th_med,
            "MEDIUM": th_med,
            "HIGH": th_high,
            "CRITICAL": th_crit,
        },
    }

    artifact = {
        "model_type": "ExtendedIsolationForest",
        "eif_state": final_eif.export_state(),
        "scaler": final_scaler,
        "feature_names": feat_cols,
        "target_name": target_col,
        "thresholds_4level": {
            "NORMAL_MAX": th_med,
            "MEDIUM": th_med,
            "HIGH": th_high,
            "CRITICAL": th_crit,
        },
        "metrics_test": {
            "accuracy": round(float(eif_acc), 4),
            "precision": round(float(eif_prec), 4),
            "recall": round(float(eif_rec), 4),
            "f1": round(float(eif_f1), 4),
            "roc_auc": round(float(eif_auc), 4),
            "pr_auc": round(float(eif_pr), 4),
        },
    }
    return artifact, metrics


def train_model2_rca_classifier(
    df: pd.DataFrame, feat_cols: List[str], random_state: int = 42
) -> Tuple[Dict, Dict]:
    print("\n" + "=" * 94)
    print("2. HUẤN LUYỆN MODEL 2: XGBOOST MULTI-CLASS ROOT-CAUSE FAULT CLASSIFIER (RCA)")
    print(f"   -> {len(feat_cols)} đặc trưng cửa sổ trượt (6 SLA x 6) | Target: label (5 lớp)")
    print("=" * 94)

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
        f"{'Thuật toán (Model 2 - RCA)':<26} | {'Accuracy':<10} | {'Macro-Prec':<10} | {'Macro-Rec':<10} | {'Macro-F1':<10} | {'5-Fold CV F1':<12}"
    )
    print("-" * 94)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    comparison = {}
    xgb_preds_te = None

    for name, clf in candidates.items():
        pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])
        cv_scores = cross_val_score(pipe, X, y, cv=cv, scoring="f1_macro", n_jobs=-1)

        clf.fit(X_tr_s, y_tr)
        preds = clf.predict(X_te_s)

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
            f"{name:<26} | {acc:<10.4f} | {prec:<10.4f} | {rec:<10.4f} | {f1:<10.4f} | {cv_mean:<12.4f}"
        )

        if name == "XGBoost_RCA":
            xgb_preds_te = preds

    print("=" * 94)
    print("\n[*] Báo cáo phân loại chi tiết từng lớp sự cố (XGBoost_RCA):")
    print(classification_report(y_te, xgb_preds_te, target_names=class_names, digits=4))

    deployed_name = "XGBoost_RCA"
    cm = confusion_matrix(y_te, xgb_preds_te)
    final_scaler = StandardScaler()
    X_all_scaled = final_scaler.fit_transform(X)
    final_clf = XGBClassifier(
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
    )
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
        "confidence_threshold": 0.70,
        "metrics_on_test": comparison[deployed_name],
        "all_candidates": comparison,
        "confusion_matrix": cm.tolist(),
    }
    return artifact, comparison


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Huấn luyện 2 Model AIOps (Model 1: EIF & Model 2: XGBoost RCA) từ Dataset 100% thực nghiệm."
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
    print(f"    Phân bố nhãn: {df['label'].value_counts().to_dict()}")

    m1_art, m1_met = train_model1_eif(df, m1_feats)
    m2_art, m2_met = train_model2_rca_classifier(df, m2_rca_feats)

    p1 = args.models_dir / "model1_eif_detector.pkl"
    p2 = args.models_dir / "model2_rca_classifier.pkl"

    joblib.dump(m1_art, p1)
    joblib.dump(m2_art, p2)

    for old_file in ("model2_xgboost_forecaster.pkl", "model3_rca_classifier.pkl", "fault_classifier.pkl"):
        old_p = args.models_dir / old_file
        if old_p.exists():
            old_p.unlink()

    summary = {
        "architecture": "Two-Model + Time-Series Trend Formula AIOps Pipeline (100% Real Containerlab 6-SLA Dataset)",
        "unified_dataset": str(args.dataset.relative_to(PROJECT_ROOT)),
        "dataset_rows": int(len(df)),
        "label_distribution": {str(k): int(v) for k, v in df["label"].value_counts().items()},
        "model1_eif_detector": m1_met,
        "forecast_5m_formula": "S_hat(t+5m) = EMA(S_t) + alpha * Slope(S_{t-W..t}) * R_sustained",
        "model2_rca_classifier": m2_met,
    }
    report_path = REPORTS_DIR / "training_metrics.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 94)
    print("[✓] ĐÃ HUẤN LUYỆN VÀ ĐÓNG GÓI 2 MODEL AI (+ 1 CÔNG THỨC DỰ BÁO 5M) TỪ DỮ LIỆU THẬT 100%:")
    print(f"  1. Model 1 (EIF Đánh giá Hiện tại 4 mức) : {p1}")
    print("  2. Dự báo 5 Phút Tới                     : Công thức EWMA + Trend Slope * Sustained Ratio")
    print(f"  3. Model 2 (XGBoost RCA Phân loại 5 lớp) : {p2}")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    sys.exit(main())
