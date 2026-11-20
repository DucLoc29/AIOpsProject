#!/usr/bin/env python3
"""
Extended Isolation Forest (EIF) - Hariri et al. (IEEE TKDE 2021).

Cài đặt chuẩn toán học của Extended Isolation Forest bằng phép chiếu siêu phẳng
nghiêng ngẫu nhiên (Random Oblique Hyperplane Cuts: n · x <= p, với n ~ N(0, I))
kết hợp cây cô lập.

Tương thích 100% với file model xuất từ Google Colab / Kaggle (`eif_state` dict
chứa `rotations_`, `forests_`, `threshold_`) cũng như model lưu trực tiếp.
"""

from typing import Any, Dict, List, Optional
import numpy as np
from sklearn.ensemble import IsolationForest


class ExtendedIsolationForest:
    """
    Extended Isolation Forest (EIF) sử dụng tập hợp các phép quay/chiếu siêu phẳng
    ngẫu nhiên trên không gian con có bậc mở rộng `extension_level`.
    """

    def __init__(
        self,
        n_estimators: int = 360,
        n_rotations: int = 12,
        extension_level: Optional[int] = None,
        contamination: float = 0.06,
        random_state: int = 42,
    ):
        self.n_estimators = n_estimators
        self.n_rotations = n_rotations
        self.extension_level = extension_level
        self.contamination = contamination
        self.random_state = random_state

        self.rotations_: List[np.ndarray] = []
        self.projection_matrices_: List[np.ndarray] = []
        self.forests_: List[IsolationForest] = []
        self.threshold_: float = 0.55

    def fit(self, X: np.ndarray, y: Optional[np.ndarray] = None) -> "ExtendedIsolationForest":
        X = np.asarray(X, dtype=float)
        n_samples, n_features = X.shape
        rng = np.random.default_rng(self.random_state)
        ext = (
            self.extension_level
            if self.extension_level is not None
            else max(1, n_features - 1)
        )
        ext = min(max(1, ext), n_features - 1)
        trees_per_rot = max(10, self.n_estimators // self.n_rotations)

        self.rotations_ = []
        self.projection_matrices_ = []
        self.forests_ = []

        for i in range(self.n_rotations):
            G = rng.normal(0.0, 1.0, size=(n_features, n_features))
            if ext < n_features - 1:
                for col in range(n_features):
                    zero_idx = rng.choice(
                        n_features, size=n_features - 1 - ext, replace=False
                    )
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
            self.projection_matrices_.append(Q)
            self.forests_.append(iso)

        scores = self.anomaly_score(X)
        self.threshold_ = float(
            np.percentile(scores, 100.0 * (1.0 - self.contamination))
        )
        return self

    def anomaly_score(self, X: np.ndarray) -> np.ndarray:
        """Trả về điểm bất thường EIF (càng cao càng bất thường)."""
        X = np.asarray(X, dtype=float)
        mats = self.rotations_ if getattr(self, "rotations_", None) else self.projection_matrices_
        total_scores = np.zeros(X.shape[0], dtype=float)
        for Q, iso in zip(mats, self.forests_):
            total_scores += -iso.score_samples(X @ Q)
        return total_scores / max(1, len(self.forests_))

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        """Tương thích chuẩn scikit-learn: dương là inlier, âm là outlier."""
        return self.threshold_ - self.anomaly_score(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Trả về 1 (bất thường / suy giảm) hoặc 0 (bình thường)."""
        return (self.anomaly_score(X) >= self.threshold_).astype(int)

    def export_state(self) -> Dict[str, Any]:
        """Xuất trạng thái thuần (dict) để tránh lỗi __main__ khi load ở backend."""
        mats = self.rotations_ if getattr(self, "rotations_", None) else self.projection_matrices_
        return {
            "n_estimators": self.n_estimators,
            "n_rotations": self.n_rotations,
            "extension_level": self.extension_level,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "rotations_": mats,
            "forests_": self.forests_,
            "threshold_": self.threshold_,
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "ExtendedIsolationForest":
        """Khôi phục mô hình EIF từ `eif_state` xuất bởi Google Colab / Kaggle."""
        obj = cls(
            n_estimators=int(state.get("n_estimators", 360)),
            n_rotations=int(state.get("n_rotations", 12)),
            extension_level=state.get("extension_level"),
            contamination=float(state.get("contamination", 0.06)),
            random_state=int(state.get("random_state", 42)),
        )
        mats = list(state.get("rotations_") or state.get("projection_matrices_") or [])
        obj.rotations_ = mats
        obj.projection_matrices_ = mats
        obj.forests_ = list(state.get("forests_") or [])
        obj.threshold_ = float(state.get("threshold_", 0.55))
        return obj


def load_eif_from_bundle(bundle: Dict[str, Any]) -> ExtendedIsolationForest:
    """
    Nạp đối tượng ExtendedIsolationForest từ bundle `.pkl` (hỗ trợ cả định dạng
    `eif_state` mới từ Colab lẫn định dạng `model` cũ).
    """
    if "eif_state" in bundle and isinstance(bundle["eif_state"], dict):
        return ExtendedIsolationForest.from_state(bundle["eif_state"])
    if "model" in bundle:
        return bundle["model"]
    raise KeyError("Bundle Model 1 không có 'eif_state' hoặc 'model'!")
