import glob
import os
import warnings
from typing import Dict, List, Optional, Tuple

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import RobustScaler

warnings.filterwarnings("ignore")


class ExtendedIsolationForest:
    """Extended Isolation Forest dùng siêu phẳng cắt nghiêng ngẫu nhiên (n . x <= p)."""

    def __init__(
        self,
        n_estimators: int = 360,
        n_rotations: int = 12,
        extension_level: Optional[int] = None,
        contamination: float = 0.06,
        random_state: int = 42,
    ) -> None:
        self.n_estimators = n_estimators
        self.n_rotations = n_rotations
        self.extension_level = extension_level
        self.contamination = contamination
        self.random_state = random_state
        self.rotations_: List[np.ndarray] = []
        self.forests_: List[IsolationForest] = []
        self.threshold_: float = 0.55

    def fit(self, X: np.ndarray) -> "ExtendedIsolationForest":
        rng = np.random.default_rng(self.random_state)
        n_samples, n_features = X.shape
        ext = self.extension_level if self.extension_level is not None else max(1, n_features - 1)
        ext = min(max(1, ext), n_features - 1)
        trees_per_rot = max(10, self.n_estimators // self.n_rotations)

        self.rotations_.clear()
        self.forests_.clear()

        for i in range(self.n_rotations):
            G = rng.normal(0.0, 1.0, size=(n_features, n_features))
            if ext < n_features - 1:
                for col in range(n_features):
                    zero_idx = rng.choice(n_features, size=n_features - 1 - ext, replace=False)
                    G[zero_idx, col] = 0.0
            Q, _ = np.linalg.qr(G)
            X_rot = X @ Q
            iso = IsolationForest(
                n_estimators=trees_per_rot,
                max_samples=min(256, n_samples),
                contamination=self.contamination,
                random_state=self.random_state + i * 17,
                n_jobs=-1,
            ).fit(X_rot)
            self.rotations_.append(Q)
            self.forests_.append(iso)

        scores = self.anomaly_score(X)
        self.threshold_ = float(np.percentile(scores, 100.0 * (1.0 - self.contamination)))
        return self

    def anomaly_score(self, X: np.ndarray) -> np.ndarray:
        raw_scores = np.zeros(X.shape[0], dtype=float)
        for Q, iso in zip(self.rotations_, self.forests_):
            raw_scores += -iso.score_samples(X @ Q)
        return raw_scores / max(1, len(self.forests_))

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.anomaly_score(X) >= self.threshold_).astype(int)

    def export_state(self) -> Dict:
        """Xuất trạng thái thuần (dict) để tránh lỗi __main__ khi load ở backend."""
        return {
            "n_estimators": self.n_estimators,
            "n_rotations": self.n_rotations,
            "extension_level": self.extension_level,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "rotations_": self.rotations_,
            "forests_": self.forests_,
            "threshold_": self.threshold_,
        }


def find_optimal_threshold(scores: np.ndarray, y_true: np.ndarray) -> Tuple[float, float]:
    """Tự động dò ngưỡng tối ưu F1 trên toàn dải phân vị [5%, 98%]."""
    best_th, best_f1 = float(np.median(scores)), -1.0
    for q in np.linspace(5, 98, 187):
        th = float(np.percentile(scores, q))
        f1_c = f1_score(y_true, (scores >= th).astype(int), zero_division=0)
        if f1_c > best_f1:
            best_f1, best_th = f1_c, th
    return best_th, best_f1


print("[✓] Đã khởi tạo xong thuật toán Extended Isolation Forest (EIF) cho Google Colab.")

CUSTOM_DATASET_PATH = "/content/aiops_network_dataset.csv"

# Đổi thành True nếu bạn để file dataset trong Google Drive
USE_GOOGLE_DRIVE = False

if USE_GOOGLE_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive")

SERVICE_CORE_KPIS = ["latency", "jitter", "packet_loss", "throughput", "response_time", "uptime"]


def resolve_colab_dataset_path(custom_path: str = "") -> str:
    # 1. Ưu tiên đường dẫn bạn tự dán vào
    if custom_path and custom_path.strip():
        clean_path = custom_path.strip()
        if os.path.exists(clean_path):
            return clean_path
        raise FileNotFoundError(f"Đường dẫn bạn nhập không tồn tại: {clean_path}")

    # 2. Tự động quét trong /content/ và Google Drive (nếu đã mount)
    candidates = (
        glob.glob("/content/**/aiops_network_dataset.csv", recursive=True)
        + glob.glob("/content/*.csv")
        + ["aiops_network_dataset.csv"]
    )
    for p in candidates:
        if os.path.exists(p):
            return p

    # 3. Nếu chưa có file nào trên Colab, tự động bật cửa sổ Upload từ máy tính
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

if "current_degraded" not in df.columns:
    raise KeyError(f"Thiếu cột nhãn 'current_degraded'! Các cột hiện có: {list(df.columns)}")

m1_features = []
for k in SERVICE_CORE_KPIS:
    for col in (k, f"{k}_roll_mean", f"{k}_sustained_ratio"):
        if col in df.columns:
            m1_features.append(col)

print(f"[*] Đã nạp Dataset: {csv_path}")
print(f"[*] Kích thước: {df.shape[0]} dòng x {df.shape[1]} cột")
print(f"[*] Tỷ lệ mẫu sự cố (current_degraded=1): {df['current_degraded'].mean() * 100:.2f}% ({df['current_degraded'].sum()}/{len(df)} mẫu)")
print(f"[*] Số đặc trưng trích lọc được: {len(m1_features)}/18")
print("    Danh sách:", m1_features)

display(df[m1_features + ["current_degraded"]].head(5))
X = df[m1_features].values
y = df["current_degraded"].values.astype(int)

# Chia 3 phần: Train (60%) - Validation để chỉnh ngưỡng (20%) - Test để đánh giá độc lập (20%)
X_temp, X_te, y_temp, y_te = train_test_split(X, y, test_size=0.20, stratify=y, random_state=42)
X_tr, X_val, y_tr, y_val = train_test_split(X_temp, y_temp, test_size=0.25, stratify=y_temp, random_state=42)

# Chỉ fit RobustScaler trên tập mẫu bình thường (y_tr == 0)
scaler = RobustScaler().fit(X_tr[y_tr == 0])
X_tr_s = np.arcsinh(scaler.transform(X_tr))
X_val_s = np.arcsinh(scaler.transform(X_val))
X_te_s = np.arcsinh(scaler.transform(X_te))

print(f"[*] Tập Train : {len(X_tr)} mẫu (Normal: {(y_tr == 0).sum()}, Degraded: {(y_tr == 1).sum()})")
print(f"[*] Tập Val   : {len(X_val)} mẫu (Normal: {(y_val == 0).sum()}, Degraded: {(y_val == 1).sum()})")
print(f"[*] Tập Test  : {len(X_te)} mẫu (Normal: {(y_te == 0).sum()}, Degraded: {(y_te == 1).sum()})")
# 1. Standard Isolation Forest (Baseline)
std_iso = IsolationForest(n_estimators=360, contamination=0.06, random_state=42, n_jobs=-1).fit(X_tr_s[y_tr == 0])
std_val_scores = -std_iso.score_samples(X_val_s)
std_best_th, _ = find_optimal_threshold(std_val_scores, y_val)

std_te_scores = -std_iso.score_samples(X_te_s)
std_preds = (std_te_scores >= std_best_th).astype(int)

# 2. Extended Isolation Forest (EIF)
eif = ExtendedIsolationForest(n_estimators=360, n_rotations=12, contamination=0.06, random_state=42).fit(X_tr_s[y_tr == 0])
eif_val_scores = eif.anomaly_score(X_val_s)
best_th_val, _ = find_optimal_threshold(eif_val_scores, y_val)
eif.threshold_ = best_th_val

eif_te_scores = eif.anomaly_score(X_te_s)
eif_preds = eif.predict(X_te_s)

print("=" * 98)
print(
    f"{'Mô hình Phát hiện Suy giảm (Tập Test)':<36} | {'Acc':<7} | {'Prec':<7} | {'Recall':<7} | {'F1':<7} | {'ROC-AUC':<7} | {'PR-AUC':<7}"
)
print("-" * 98)
print(
    f"{'Standard Isolation Forest':<36} | {accuracy_score(y_te, std_preds):<7.4f} | "
    f"{precision_score(y_te, std_preds, zero_division=0):<7.4f} | {recall_score(y_te, std_preds, zero_division=0):<7.4f} | "
    f"{f1_score(y_te, std_preds, zero_division=0):<7.4f} | {roc_auc_score(y_te, std_te_scores):<7.4f} | "
    f"{average_precision_score(y_te, std_te_scores):<7.4f}"
)
print(
    f"{'Extended Isolation Forest (EIF)':<36} | {accuracy_score(y_te, eif_preds):<7.4f} | "
    f"{precision_score(y_te, eif_preds, zero_division=0):<7.4f} | {recall_score(y_te, eif_preds, zero_division=0):<7.4f} | "
    f"{f1_score(y_te, eif_preds, zero_division=0):<7.4f} | {roc_auc_score(y_te, eif_te_scores):<7.4f} | "
    f"{average_precision_score(y_te, eif_te_scores):<7.4f}"
)
print("=" * 98)

tn, fp, fn, tp = confusion_matrix(y_te, eif_preds).ravel()
print(f"[*] Confusion Matrix EIF (Test): TP={tp}, FP={fp}, TN={tn}, FN={fn} (False Positive Rate: {fp/(fp+tn)*100:.2f}%)")
# Hiệu chuẩn phân vị từ tập Validation để chống rò rỉ dữ liệu
val_normal_scores = eif_val_scores[y_val == 0]
calibrated_quantile = float(np.mean(val_normal_scores <= best_th_val) * 100.0)

final_scaler = RobustScaler().fit(X[y == 0])
X_all_s = np.arcsinh(final_scaler.transform(X))
final_eif = ExtendedIsolationForest(n_estimators=360, n_rotations=12, contamination=0.06, random_state=42).fit(X_all_s[y == 0])

all_scores = final_eif.anomaly_score(X_all_s)
normal_all_scores = all_scores[y == 0]
anomaly_all_scores = all_scores[y == 1]

th_high = round(float(np.percentile(normal_all_scores, np.clip(calibrated_quantile, 80.0, 99.5))), 4)
th_med = round(min(th_high - 0.015, float(np.percentile(normal_all_scores, 92.0))), 4)
th_crit = round(max(th_high + 0.03, float(np.percentile(anomaly_all_scores, 65.0))), 4)

final_eif.threshold_ = th_high

print("[*] Ngưỡng 4 mức độ đã hiệu chuẩn (Model 1 - EIF):")
print(f"    - NORMAL   : score < {th_med}")
print(f"    - MEDIUM   : {th_med} <= score < {th_high}  (Cảnh báo sớm)")
print(f"    - HIGH     : {th_high} <= score < {th_crit}  (Kích hoạt Model 2 phân loại)")
print(f"    - CRITICAL : score >= {th_crit}  (Sự cố nghiêm trọng / Khẩn cấp)")

artifact = {
    "model_type": "ExtendedIsolationForest",
    "eif_state": final_eif.export_state(),
    "scaler": final_scaler,
    "feature_names": m1_features,
    "target_name": "current_degraded",
    "thresholds_4level": {
        "NORMAL_MAX": th_med,
        "MEDIUM": th_med,
        "HIGH": th_high,
        "CRITICAL": th_crit,
    },
    "metrics_test": {
        "accuracy": round(float(accuracy_score(y_te, eif_preds)), 4),
        "precision": round(float(precision_score(y_te, eif_preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_te, eif_preds, zero_division=0)), 4),
        "f1": round(float(f1_score(y_te, eif_preds, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_te, eif_te_scores)), 4),
        "pr_auc": round(float(average_precision_score(y_te, eif_te_scores)), 4),
    },
}

out_file = "/content/model1_eif_detector.pkl"
joblib.dump(artifact, out_file)
print(f"\n[✓] Đã xuất file mô hình thành công: {out_file} ({os.path.getsize(out_file) / 1024:.1f} KB)")
fig, axes = plt.subplots(1, 2, figsize=(14, 4.8))

# Biểu đồ 1: Phân bố Anomaly Score
axes[0].hist(normal_all_scores, bins=35, alpha=0.75, label="Bình thường (Normal)", color="#28cd41", density=True)
axes[0].hist(anomaly_all_scores, bins=35, alpha=0.75, label="Suy giảm / Sự cố (Degraded)", color="#ff3b30", density=True)
axes[0].axvline(th_med, color="#eab308", linestyle="--", linewidth=2, label=f"MEDIUM ({th_med})")
axes[0].axvline(th_high, color="#f97316", linestyle="--", linewidth=2, label=f"HIGH ({th_high})")
axes[0].axvline(th_crit, color="#b91c1c", linestyle="--", linewidth=2, label=f"CRITICAL ({th_crit})")
axes[0].set_title("Phân bố Điểm Bất thường EIF (18 Đặc trưng Cửa sổ trượt)")
axes[0].set_xlabel("EIF Anomaly Score")
axes[0].set_ylabel("Mật độ phân bố")
axes[0].legend(loc="upper right")

# Biểu đồ 2: So sánh Metrics Standard IF vs EIF trên tập Test
metric_names = ["Precision", "Recall", "F1-Score", "ROC-AUC", "PR-AUC"]
std_vals = [
    precision_score(y_te, std_preds, zero_division=0),
    recall_score(y_te, std_preds, zero_division=0),
    f1_score(y_te, std_preds, zero_division=0),
    roc_auc_score(y_te, std_te_scores),
    average_precision_score(y_te, std_te_scores),
]
eif_vals = [
    precision_score(y_te, eif_preds, zero_division=0),
    recall_score(y_te, eif_preds, zero_division=0),
    f1_score(y_te, eif_preds, zero_division=0),
    roc_auc_score(y_te, eif_te_scores),
    average_precision_score(y_te, eif_te_scores),
]

x_idx = np.arange(len(metric_names))
width = 0.35
axes[1].bar(x_idx - width / 2, std_vals, width, label="Standard Isolation Forest", color="#94a3b8")
axes[1].bar(x_idx + width / 2, eif_vals, width, label="Extended Isolation Forest (EIF)", color="#2563eb")
axes[1].set_ylim(0, 1.12)
axes[1].set_xticks(x_idx)
axes[1].set_xticklabels(metric_names)
axes[1].set_title("So sánh Hiệu năng trên Tập Test Độc Lập")
axes[1].legend(loc="lower right")

for i, v in enumerate(eif_vals):
    axes[1].text(x_idx[i] + width / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=8.5, fontweight="bold")

plot_file = "/content/model1_eif_evaluation.png"
plt.tight_layout()
plt.savefig(plot_file, dpi=150)
plt.show()
print(f"[✓] Đã lưu biểu đồ đánh giá: {plot_file}")

# Tự động tải file model và ảnh biểu đồ về máy trên Google Colab
try:
    from google.colab import files
    files.download("/content/model1_eif_detector.pkl")
    files.download(plot_file)
except ImportError:
    pass