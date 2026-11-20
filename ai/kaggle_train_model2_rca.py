import glob
import os
import warnings

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")
print("[✓] Đã nạp đầy đủ thư viện cho Model 2 (XGBoost RCA Classifier).")

CUSTOM_DATASET_PATH = "/content/aiops_network_dataset.csv"
USE_GOOGLE_DRIVE = False

if USE_GOOGLE_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive")

SERVICE_CORE_KPIS = ["latency", "jitter", "packet_loss", "throughput", "response_time", "uptime"]
ROLLING_SUFFIXES = ("_roll_mean", "_roll_std", "_delta1", "_trend_slope", "_sustained_ratio")


def resolve_colab_dataset_path(custom_path: str = "") -> str:
    if custom_path and custom_path.strip():
        clean_path = custom_path.strip()
        if os.path.exists(clean_path):
            return clean_path
        raise FileNotFoundError(f"Đường dẫn bạn nhập không tồn tại: {clean_path}")

    candidates = (
        glob.glob("/content/**/aiops_network_dataset.csv", recursive=True)
        + glob.glob("/content/*.csv")
        + ["aiops_network_dataset.csv"]
    )
    for p in candidates:
        if os.path.exists(p):
            return p

    try:
        from google.colab import files
        print("[!] Không tìm thấy file CSV trên Colab. Vui lòng chọn file từ máy tính để upload:")
        uploaded = files.upload()
        if uploaded:
            return list(uploaded.keys())[0]
    except ImportError:
        pass

    raise FileNotFoundError("Không tìm thấy file CSV nào! Hãy upload file hoặc dán đường dẫn vào CUSTOM_DATASET_PATH.")


csv_path = resolve_colab_dataset_path(CUSTOM_DATASET_PATH)
df = pd.read_csv(csv_path).replace([np.inf, -np.inf], np.nan).fillna(0.0)

if "label" not in df.columns:
    raise KeyError(f"Thiếu cột nhãn 'label'! Các cột hiện có: {list(df.columns)}")

# Tự động lọc 36 đặc trưng cửa sổ trượt (6 thông số SLA gốc x 6 đại lượng)
m2_features = [k for k in SERVICE_CORE_KPIS if k in df.columns]
for k in SERVICE_CORE_KPIS:
    for suf in ROLLING_SUFFIXES:
        col = f"{k}{suf}"
        if col in df.columns:
            m2_features.append(col)

# Loại bỏ mẫu nhiễu mâu thuẫn nhãn (Normal nhưng bị đánh dấu degraded=1)
df_clean = df[~((df["label"] == "Normal") & (df["current_degraded"] == 1))].copy()

print(f"[*] Đã nạp Dataset: {csv_path}")
print(f"[*] Số mẫu sạch đưa vào huấn luyện: {len(df_clean)}/{len(df)} mẫu")
print(f"[*] Model 2 (RCA) trích lọc được {len(m2_features)}/36 đặc trưng cửa sổ trượt")
print("\n[*] Phân bố số lượng mẫu theo từng lớp sự cố:")
print(df_clean["label"].value_counts())
X = df_clean[m2_features].values
label_encoder = LabelEncoder()
y = label_encoder.fit_transform(df_clean["label"].values)
class_names = list(label_encoder.classes_)

X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, stratify=y, random_state=42)

scaler = StandardScaler()
X_tr_s = scaler.fit_transform(X_tr)
X_te_s = scaler.transform(X_te)

print(f"[*] Các lớp phân loại ({len(class_names)} lớp): {class_names}")
print(f"[*] Tập Train: {len(X_tr)} mẫu | Tập Test độc lập: {len(X_te)} mẫu")
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
        random_state=42,
        n_jobs=-1,
    ),
    "RandomForest_RCA": RandomForestClassifier(
        n_estimators=300, max_depth=12, class_weight="balanced", random_state=42, n_jobs=-1
    ),
    "ExtraTrees_RCA": ExtraTreesClassifier(
        n_estimators=300, max_depth=14, class_weight="balanced", random_state=42, n_jobs=-1
    ),
    "GradientBoosting_RCA": GradientBoostingClassifier(
        n_estimators=150, learning_rate=0.08, max_depth=4, random_state=42
    ),
}

print("=" * 94)
print(f"{'Thuật toán (Model 2 - RCA)':<26} | {'Accuracy':<10} | {'Macro-Prec':<10} | {'Macro-Rec':<10} | {'Macro-F1':<10} | {'5-Fold CV F1':<12}")
print("-" * 94)

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
comparison = {}
xgb_preds = None
xgb_proba = None

for name, clf in candidates.items():
    # Dùng Pipeline trong Cross-Validation để tránh data leakage khi chuẩn hóa dữ liệu
    pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])
    cv_scores = cross_val_score(pipe, X, y, cv=cv, scoring="f1_macro", n_jobs=-1)

    # Huấn luyện trên tập Train và chấm điểm trên tập Test độc lập
    clf.fit(X_tr_s, y_tr)
    preds = clf.predict(X_te_s)

    acc = accuracy_score(y_te, preds)
    prec = precision_score(y_te, preds, average="macro", zero_division=0)
    rec = recall_score(y_te, preds, average="macro", zero_division=0)
    f1 = f1_score(y_te, preds, average="macro", zero_division=0)
    cv_m = float(np.mean(cv_scores))

    comparison[name] = {
        "accuracy": round(float(acc), 4),
        "macro_precision": round(float(prec), 4),
        "macro_recall": round(float(rec), 4),
        "macro_f1": round(float(f1), 4),
        "cv5_f1_mean": round(cv_m, 4),
    }
    print(f"{name:<26} | {acc:<10.4f} | {prec:<10.4f} | {rec:<10.4f} | {f1:<10.4f} | {cv_m:<12.4f}")

    if name == "XGBoost_RCA":
        xgb_preds = preds
        xgb_proba = clf.predict_proba(X_te_s)

print("=" * 94)
print("\n[*] Báo cáo phân loại chi tiết từng lớp sự cố (XGBoost_RCA):")
print(classification_report(y_te, xgb_preds, target_names=class_names, digits=4))

# Kiểm tra độ tự tin (predict_proba) trên các ca đoán đúng để gợi ý ngưỡng cho Decision Engine
max_probs = np.max(xgb_proba, axis=1)
correct_mask = (xgb_preds == y_te)
print(f"[*] Độ tin cậy trung bình (predict_proba) khi đoán đúng: {max_probs[correct_mask].mean()*100:.2f}%")
print(f"[*] Phân vị 5% độ tin cậy thấp nhất trên mẫu đúng: {np.percentile(max_probs[correct_mask], 5)*100:.2f}% (Gợi ý ngưỡng Unknown_Anomaly: 0.70)")
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
    random_state=42,
    n_jobs=-1,
)
final_clf.fit(X_all_scaled, y)

importances = getattr(final_clf, "feature_importances_", np.zeros(len(m2_features)))
feat_imp_pairs = sorted(
    zip(m2_features, [round(float(x), 4) for x in importances]),
    key=lambda item: item[1],
    reverse=True,
)

artifact = {
    "model_type": "XGBoost_RCA",
    "model": final_clf,
    "scaler": final_scaler,
    "label_encoder": label_encoder,
    "class_names": class_names,
    "feature_names": m2_features,
    "feature_importances": dict(feat_imp_pairs),
    "confidence_threshold": 0.70,  # Ngưỡng gợi ý cho Decision Engine (dưới 0.70 -> Unknown_Anomaly)
    "metrics_on_test": comparison["XGBoost_RCA"],
    "all_candidates": comparison,
    "confusion_matrix": confusion_matrix(y_te, xgb_preds).tolist(),
}

out_file = "/content/model2_rca_classifier.pkl"
joblib.dump(artifact, out_file)
print(f"[✓] Đã xuất file mô hình thành công: {out_file} ({os.path.getsize(out_file) / 1024:.1f} KB)")
fig, axes = plt.subplots(1, 2, figsize=(15, 5.2))

# Biểu đồ 1: Ma trận nhầm lẫn (Confusion Matrix) trên tập Test
cm = confusion_matrix(y_te, xgb_preds)
disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=class_names)
disp.plot(ax=axes[0], cmap="Blues", colorbar=False, values_format="d")
axes[0].set_title("Ma trận Nhầm lẫn Phân loại Sự cố (XGBoost RCA)")
axes[0].tick_params(axis="x", rotation=20)

# Biểu đồ 2: Top 12 Đặc trưng Cửa sổ trượt quan trọng nhất
top_feats = feat_imp_pairs[:12][::-1]
axes[1].barh([f[0] for f in top_feats], [f[1] for f in top_feats], color="#2563eb")
axes[1].set_title("Top 12 Đặc trưng Cửa sổ trượt Quan trọng nhất")
axes[1].set_xlabel("Mức độ đóng góp (Feature Importance)")

for i, (_, val) in enumerate(top_feats):
    axes[1].text(val + 0.002, i, f"{val:.3f}", va="center", fontsize=8.5)

plot_file = "/content/model2_rca_evaluation.png"
plt.tight_layout()
plt.savefig(plot_file, dpi=150)
plt.show()
print(f"[✓] Đã lưu biểu đồ đánh giá: {plot_file}")

# Tự động tải file model và ảnh biểu đồ về máy trên Google Colab
try:
    from google.colab import files
    files.download("/content/model2_rca_classifier.pkl")
    files.download(plot_file)
except ImportError:
    pass