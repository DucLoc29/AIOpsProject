#!/usr/bin/env python3
"""
AIOps Command Center Server (172.20.20.55:5000 / localhost:5000).
Trung tâm Chỉ huy Giám sát, Dự báo Suy giảm & Tự động Ứng phó Sự cố Hướng Dịch vụ — 100% DỮ LIỆU THẬT.

Kiến trúc chuẩn hóa:
  1. Thu thập 100% Thời gian thực qua `RealContainerlabTelemetryCollector` (Single Source of Truth):
     - Đo trực tiếp 6 Thông số SLA (`latency`, `jitter`, `packet_loss`, `throughput`, `response_time`, `uptime`)
       từ Data-Plane Active Probe (`firewall` -> `192.168.10.x:port`) + Kernel Per-Port Accounting + Prometheus (2s).
     - Hoàn toàn 100% thời gian thực — không có chế độ giả lập hay nút kích hoạt kịch bản trên giao diện.
  2. Nạp trực tiếp 2 Mô hình AI huấn luyện từ dataset 100% thực nghiệm:
     - Bước 1 — Model 1 (`ai/models/model1_eif_detector.pkl`, EIF 18 đặc trưng trượt + `thresholds_4level`):
       Đánh giá sức khỏe hiện tại theo 4 mức độ (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`).
     - Bước 2 — Công thức Chuỗi thời gian 5 phút (`compute_5m_trend_forecast`):
       Dự báo 4 mức độ tương lai & lọc gai nhiễu thoáng qua (TransientSpike).
     - Bước 3 — Model 2 (`ai/models/model2_rca_classifier.pkl`, XGBoost 36 đặc trưng trượt + `confidence_threshold=0.70`):
       Phân loại nguyên nhân gốc 5 lớp (`Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`)
       hoặc `Unknown_Anomaly` nếu độ tự tin < 70% / dịch vụ sập không có lưu lượng tấn công.
  3. Vòng lặp khép kín Tự động Ứng phó (Closed-Loop Real Auto-Remediation):
     - Tự động nhận diện kể cả khi chạy tấn công từ Terminal bên ngoài!
     - Thực thi lệnh `iptables` thật (Firewall cho North-South & Host cho East-West PortScan).
     - Hồi phục tự nhiên về `Bình thường (NORMAL)` sau vài giây khi gói tin tấn công bị chặn thật.
"""

import json
import mimetypes
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
sys.path.insert(0, str(PROJECT_ROOT / "ai" / "collector"))
sys.path.insert(0, str(PROJECT_ROOT / "ai"))
sys.path.insert(0, str(BACKEND_DIR))

import eif_model  # noqa: F401, E402
from eif_model import ExtendedIsolationForest, load_eif_from_bundle  # noqa: F401, E402
from prepare_two_stage_datasets import (  # noqa: E402
    KEY_TIMESERIES_KPIS,
    SERVICE_CORE_KPIS,
    compute_5m_trend_forecast,
    compute_severity_level,
    compute_sustained_thresholds,
)
from database import AIOpsDatabase  # noqa: E402
from auth import (  # noqa: E402
    create_token,
    extract_bearer_token,
    revoke_token,
    verify_password,
    verify_token,
)

from remediation import (  # noqa: E402
    build_service_remediation_plan,
    ensure_background_normal_traffic,
    execute_oob_rollback,
    execute_service_remediation,
    execute_service_rollback,
    is_containerlab_running,
    is_lab_dataplane_ready,
    stop_active_attack_processes,
)
from telemetry import RealContainerlabTelemetryCollector  # noqa: E402

DIST_DIR = FRONTEND_DIR / "dist"
MODELS_DIR = PROJECT_ROOT / "ai" / "models"
DATASET_PATH = PROJECT_ROOT / "ai" / "dataset" / "aiops_network_dataset.csv"

SEVERITY_LABELS_VI = {
    "NORMAL": "Bình thường",
    "MEDIUM": "Trung bình",
    "HIGH": "Cao",
    "CRITICAL": "Nghiêm trọng",
}


class AIOpsStateEngine:
    def __init__(self) -> None:
        print("[*] Đang nạp 2 Mô hình AI (Model 1: EIF & Model 2: XGBoost RCA) từ ai/models/ ...")
        self.m1_bundle = joblib.load(MODELS_DIR / "model1_eif_detector.pkl")
        self.m1_model: ExtendedIsolationForest = load_eif_from_bundle(self.m1_bundle)
        self.m2_rca_bundle = joblib.load(MODELS_DIR / "model2_rca_classifier.pkl")
        # Tối ưu suy luận thời gian thực trên mẫu nhỏ: tắt đa luồng overhead của XGBoost (nhanh hơn ~10x)
        try:
            self.m2_rca_bundle["model"].set_params(n_jobs=1)
        except Exception:
            pass

        t4 = self.m1_bundle.get("thresholds_4level", {})
        self.m1_th_high = float(t4.get("HIGH", self.m1_model.threshold_))
        self.m1_th_med = float(t4.get("MEDIUM", max(0.46, self.m1_th_high - 0.06)))
        self.m1_th_crit = float(t4.get("CRITICAL", min(0.85, self.m1_th_high + 0.05)))
        self.m2_conf_threshold = float(self.m2_rca_bundle.get("confidence_threshold", 0.70))

        print(
            f"[✓] Model 1 EIF sẵn sàng ({len(self.m1_bundle['feature_names'])} features | "
            f"Ngưỡng: MED={self.m1_th_med}, HIGH={self.m1_th_high}, CRIT={self.m1_th_crit})"
        )
        print(
            f"[✓] Model 2 XGBoost RCA sẵn sàng ({len(self.m2_rca_bundle['feature_names'])} features | "
            f"Lớp: {list(self.m2_rca_bundle.get('class_names', []))} | Ngưỡng tự tin: {self.m2_conf_threshold:.2f})"
        )

        # Nạp ngưỡng `sustained_ratio` từ ĐÚNG các mẫu Normal sạch (loại bỏ TransientSpike)
        # dùng cùng hàm `compute_sustained_thresholds` với bộ tạo dataset để đồng nhất 100%!
        self.df = pd.read_csv(DATASET_PATH).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if "severity_level" in self.df.columns:
            clean_normal = self.df[(self.df["label"] == "Normal") & (self.df["severity_level"] == "NORMAL")]
        else:
            clean_normal = self.df[self.df["label"] == "Normal"]
        self.baseline_medians, self.sustained_thresholds = compute_sustained_thresholds(clean_normal)
        print(f"[✓] Ngưỡng sustained_ratio đồng bộ: {self.sustained_thresholds}")

        self.db = AIOpsDatabase()
        policy_cfg = self.db.get_policy_config()

        self.prom_url = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
        self.collector = RealContainerlabTelemetryCollector(prom_url=self.prom_url)

        self.lab_running = is_containerlab_running()
        self.dataplane_ready = is_lab_dataplane_ready() if self.lab_running else False
        if self.lab_running and self.dataplane_ready:
            ensure_background_normal_traffic()
            self.collector.ensure_services_registered(list(self.db.services_cache))

        self.tick_counter = 0
        self.forecast_threshold = float(policy_cfg.get("threshold_pct", 60.0)) / 100.0
        self.auto_pilot = bool(policy_cfg.get("auto_pilot", True))

        # Cửa sổ trượt 5 bước riêng cho từng dịch vụ
        self.service_windows: Dict[str, List[Dict[str, float]]] = {}
        self.service_score_windows: Dict[str, List[float]] = {}

        # Quản lý luật bảo vệ, chờ phê duyệt & cooldown chống ghi log trùng lặp
        self.active_service_rules: Dict[str, Dict[str, Any]] = {}
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}
        self.incident_cooldown: Dict[str, float] = {}
        self.spike_log_cooldown: Dict[str, float] = {}

        self.audit_logs: List[Dict[str, Any]] = list(self.db.remediations_cache) or [
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "service_id": "ALL_SERVICES",
                "scenario": "SystemInit",
                "trigger": "SYSTEM",
                "fault_class": "Normal",
                "confidence": "100%",
                "target": "aiops-server (172.20.20.55)",
                "action": "Đã nạp 2 Model AI (EIF + XGBoost RCA) và kích hoạt thu thập 100% dữ liệu thật",
                "status": "READY",
            }
        ]
        self.alertmanager_webhooks: List[Dict[str, Any]] = []
        self.performance_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self.latest_state: Dict[str, Any] = {}

        # Khởi tạo lịch sử ban đầu bằng mẫu đo thực tế ngay lúc khởi động
        now_dt = datetime.now()
        init_services = list(self.db.services_cache)
        if self.lab_running and self.dataplane_ready:
            # 2 bước warmup nhẹ để có delta bộ đếm kernel thực tế ngay lúc khởi động
            self.collector.sample_all_services(init_services)
            time.sleep(0.5)
            init_sample = self.collector.sample_all_services(init_services)
        else:
            init_sample = (False, {}, {})

        for i in range(6):
            t_str = (now_dt - timedelta(seconds=(5 - i) * 2)).strftime("%H:%M:%S")
            self.latest_state = self._step_all_services_unlocked(
                custom_time=t_str,
                prefetched_sample=init_sample,
                allow_remediation=False,
            )

        self._bg_thread = threading.Thread(target=self._background_telemetry_loop, daemon=True)
        self._bg_thread.start()

    def _background_telemetry_loop(self) -> None:
        loop_idx = 0
        while True:
            t0 = time.monotonic()
            loop_idx += 1
            try:
                # Chỉ kiểm tra `docker inspect/ip` mỗi 5 chu kỳ (10s) để tránh spawn thêm `docker exec` liên tục
                if loop_idx % 5 == 1 or not self.dataplane_ready:
                    lab_up = is_containerlab_running()
                    dp_ready = is_lab_dataplane_ready() if lab_up else False
                else:
                    lab_up = self.lab_running
                    dp_ready = self.dataplane_ready

                with self._lock:
                    services_snapshot = [dict(s) for s in self.db.services_cache]

                if lab_up and dp_ready:
                    ensure_background_normal_traffic()
                    sample = self.collector.sample_all_services(services_snapshot)
                else:
                    sample = (False, {}, {})

                # Tính toàn bộ Cửa sổ trượt + Batch EIF + XGBoost NGOÀI self._lock -> `/api/state` luôn < 3ms!
                precomputed = self._compute_evaluations_no_lock(
                    services_snapshot, sample, lab_up, dp_ready
                )

                actions_to_run: List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]] = []
                with self._lock:
                    self.lab_running = lab_up
                    self.dataplane_ready = dp_ready
                    self.latest_state, actions_to_run = self._commit_step_unlocked(
                        services_snapshot=services_snapshot,
                        sample=sample,
                        service_evaluations=precomputed,
                        allow_remediation=True,
                    )

                # Thực thi `docker exec iptables` NGOÀI lock để tuyệt đối không làm đứng Web UI!
                for srv_doc, plan, conf_str, mode, result_id in actions_to_run:
                    self._run_remediation_async(srv_doc, plan, conf_str, mode, result_id)
            except Exception as exc:
                print(f"[!] Lỗi vòng lặp thu thập dữ liệu thật: {exc}")

            elapsed = time.monotonic() - t0
            if elapsed < 2.0:
                time.sleep(2.0 - elapsed)

    def _build_sliding_window_row_for_service(
        self, sid: str, instant_kpis: Dict[str, float], window_size: int = 5
    ) -> Dict[str, float]:
        """
        Cập nhật buffer cửa sổ trượt 5 bước của dịch vụ `sid` và tính đủ 36 đặc trưng trượt
        (6 SLA KPIs x 6 đại lượng: current, roll_mean, roll_std, delta1, trend_slope, sustained_ratio).
        """
        win = self.service_windows.setdefault(sid, [])
        win.append(dict(instant_kpis))
        if len(win) > window_size:
            win[:] = win[-window_size:]

        full_row = dict(instant_kpis)
        for kpi in KEY_TIMESERIES_KPIS:
            series = [float(item.get(kpi, 0.0)) for item in win]
            while len(series) < window_size:
                series.insert(0, series[0] if series else 0.0)
            arr = np.array(series, dtype=float)

            roll_mean = float(np.mean(arr))
            roll_std = float(np.std(arr))
            delta_1 = float(arr[-1] - arr[-2])
            trend_slope = float((arr[-1] - arr[0]) / max(1, window_size - 1))

            thr = self.sustained_thresholds.get(kpi, 0.0)
            if kpi == "uptime":
                sustained_ratio = float(np.mean(arr < thr))
            else:
                sustained_ratio = float(np.mean(arr > thr))

            full_row[f"{kpi}_roll_mean"] = round(roll_mean, 4)
            full_row[f"{kpi}_roll_std"] = round(roll_std, 4)
            full_row[f"{kpi}_delta1"] = round(delta_1, 4)
            full_row[f"{kpi}_trend_slope"] = round(trend_slope, 4)
            full_row[f"{kpi}_sustained_ratio"] = round(sustained_ratio, 4)

        return full_row

    def _evaluate_single_service(
        self,
        service: Dict[str, Any],
        feat_row: Dict[str, float],
        precomputed_eif_score: float | None = None,
    ) -> Dict[str, Any]:
        """
        Chạy thật 2 Mô hình AI + Công thức Dự báo 5m trên vector đặc trưng cửa sổ trượt 36 chiều đo thật 100%.
        """
        sid = service["service_id"]

        # --- BƯỚC 1: MODEL 1 (Extended Isolation Forest - 18 features trượt) ---
        m1_cols: List[str] = self.m1_bundle["feature_names"]
        if precomputed_eif_score is not None:
            eif_score = float(precomputed_eif_score)
        else:
            x_m1 = np.array([[float(feat_row.get(c, 0.0)) for c in m1_cols]])
            x_m1_s = np.arcsinh(self.m1_bundle["scaler"].transform(x_m1))
            eif_score = float(self.m1_model.anomaly_score(x_m1_s)[0])

        th_med = self.m1_th_med
        th_high = self.m1_th_high
        th_crit = self.m1_th_crit

        # Độ duy trì trên cửa sổ trượt 5 bước:
        #   - 1-2 bước (max_sustained <= 0.40, ~2-4 giây gai nhiễu thoáng qua như 05_transient_spike.sh)
        #     -> giữ mức MEDIUM để chống báo động giả!
        #   - Từ 3 bước trở lên (max_sustained >= 0.60, >= 6 giây duy trì) -> đánh giá đúng mức HIGH/CRITICAL.
        max_sustained = float(
            max(feat_row.get(f"{k}_sustained_ratio", 0.0) for k in SERVICE_CORE_KPIS)
        )
        if max_sustained <= 0.40 and eif_score >= th_high:
            effective_score = min(th_high - 0.012, max(th_med + 0.008, eif_score * 0.90))
        else:
            effective_score = eif_score

        severity_level = compute_severity_level(effective_score, th_med, th_high, th_crit)
        m1_degraded = severity_level in ("HIGH", "CRITICAL")

        # Lưu lịch sử điểm Model 1 của dịch vụ để tính Công thức Dự báo Xu hướng 5 phút
        score_win = self.service_score_windows.setdefault(sid, [])
        score_win.append(effective_score)
        if len(score_win) > 5:
            score_win[:] = score_win[-5:]

        # --- BƯỚC 2: CÔNG THỨC TOÁN HỌC DỰ BÁO CHUỖI THỜI GIAN 5 PHÚT ---
        forecast_score, forecast_level, formula_prob_pct = compute_5m_trend_forecast(
            current_score=effective_score,
            window_scores=score_win,
            th_med=th_med,
            th_high=th_high,
            th_crit=th_crit,
        )

        if max_sustained <= 0.40:
            prob_5m_pct = min(formula_prob_pct, 18.0)
            forecast_level = "NORMAL"
        else:
            prob_5m_pct = round(formula_prob_pct, 2)
            if prob_5m_pct >= 80.0:
                forecast_level = "CRITICAL"
            elif prob_5m_pct >= round(self.forecast_threshold * 100.0, 0):
                forecast_level = "HIGH"

        thr_pct = round(self.forecast_threshold * 100.0, 0)
        is_sustained = bool(prob_5m_pct >= thr_pct and forecast_level in ("HIGH", "CRITICAL"))

        # --- CỔNG ĐIỀU PHỐI (Policy Gate) ---
        trigger_m3 = bool(m1_degraded and is_sustained)

        # --- BƯỚC 3: MODEL 2 (XGBoost Multi-class RCA - 36 features trượt) ---
        m2_rca_cols: List[str] = self.m2_rca_bundle["feature_names"]
        class_names = list(
            self.m2_rca_bundle.get("class_names") or self.m2_rca_bundle["label_encoder"].classes_
        )

        if trigger_m3:
            x_m2_rca = np.array([[float(feat_row.get(c, 0.0)) for c in m2_rca_cols]])
            x_m2_rca_s = self.m2_rca_bundle["scaler"].transform(x_m2_rca)
            probs_m2_rca = self.m2_rca_bundle["model"].predict_proba(x_m2_rca_s)[0]
            class_probs = {cls: round(float(p) * 100.0, 2) for cls, p in zip(class_names, probs_m2_rca)}
            # Nếu dịch vụ thật sự ngừng phản hồi hoàn toàn (uptime ~ 0%) mà không có lưu lượng tấn công,
            # không chặn nhầm bằng iptables -> báo Unknown_Anomaly để quản trị viên kiểm tra tiến trình dịch vụ.
            if float(feat_row.get("uptime_roll_mean", 100.0)) <= 5.0 and float(feat_row.get("throughput_roll_mean", 0.0)) < 2.0:
                fault_class = "Unknown_Anomaly"
                confidence = 0.0
            else:
                pred_idx = int(np.argmax(probs_m2_rca))
                best_p = float(probs_m2_rca[pred_idx])
                best_cls = str(class_names[pred_idx])

                if best_cls == "Normal":
                    non_normal = [
                        (i, float(p))
                        for i, (c, p) in enumerate(zip(class_names, probs_m2_rca))
                        if c != "Normal"
                    ]
                    if non_normal:
                        pred_idx, best_p = max(non_normal, key=lambda x: x[1])
                        best_cls = str(class_names[pred_idx])

                if best_p < self.m2_conf_threshold:
                    fault_class = "Unknown_Anomaly"
                else:
                    fault_class = best_cls
                confidence = round(best_p * 100.0, 2)
            # Truy vết ĐỘNG IP tấn công thực tế từ bộ đếm kernel `AIOPS_ACCT_IN` (hỗ trợ cả 3 phía Internet/Branch/LAN)
            if fault_class in ("SynFlood", "HttpFlood", "SshBruteforce", "PortScan"):
                det_ips, target_ov = self.collector.detect_attacker_ips(fault_class, service)
            else:
                det_ips, target_ov = None, None
            remediation_plan = build_service_remediation_plan(
                fault_class, service, attacker_ips=det_ips, target_override=target_ov
            )
        else:
            class_probs = {cls: (100.0 if cls == "Normal" else 0.0) for cls in class_names}
            fault_class = "Normal (Standby)"
            confidence = 0.0
            remediation_plan = build_service_remediation_plan("Normal", service)
            if severity_level == "MEDIUM":
                remediation_plan["action_code"] = "SUPPRESSED_TRANSIENT_SPIKE"
                remediation_plan["title"] = (
                    f"Cửa sổ trượt phát hiện dao động thoáng qua mức Trung bình, Dự báo 5m tự hồi phục về Bình thường ({prob_5m_pct:.1f}%) -> Không chặn!"
                )

        metrics_6 = {k: round(float(feat_row.get(k, 0.0)), 2) for k in SERVICE_CORE_KPIS}
        sliding_matrix = {
            k: {
                "current": round(float(feat_row.get(k, 0.0)), 3),
                "roll_mean": round(float(feat_row.get(f"{k}_roll_mean", 0.0)), 3),
                "roll_std": round(float(feat_row.get(f"{k}_roll_std", 0.0)), 3),
                "delta1": round(float(feat_row.get(f"{k}_delta1", 0.0)), 3),
                "trend_slope": round(float(feat_row.get(f"{k}_trend_slope", 0.0)), 3),
                "sustained_ratio": round(float(feat_row.get(f"{k}_sustained_ratio", 0.0)), 3),
            }
            for k in SERVICE_CORE_KPIS
        }
        active_rule = self.active_service_rules.get(sid)
        pending_rule = self.pending_approvals.get(sid)

        return {
            "service_id": sid,
            "service_name": service["service_name"],
            "service_type": service.get("service_type", "HTTP"),
            "host_node": service.get("host_node", "service-server"),
            "zone": service.get("zone", "HQ_SERVER_FARM"),
            "address": service.get("address", "192.168.10.10"),
            "oob_ip": service.get("oob_ip", "172.20.20.21"),
            "port": int(service.get("port", 80)),
            "check_interval": service.get("check_interval", "2s"),
            "description": service.get("description", ""),
            "status": severity_level,
            "severity_level": severity_level,
            "severity_label_vi": SEVERITY_LABELS_VI.get(severity_level, severity_level),
            "protection_status": (
                "PROTECTED" if active_rule else ("PENDING_APPROVAL" if pending_rule else "MONITORING")
            ),
            "active_rule": active_rule,
            "pending_rule": pending_rule,
            "impact_description": remediation_plan["title"],
            "metrics": metrics_6,
            "sliding_window_matrix": sliding_matrix,
            "sliding_window_summary": {
                "latency_roll_mean": feat_row.get("latency_roll_mean", 0.0),
                "response_time_roll_mean": feat_row.get("response_time_roll_mean", 0.0),
                "throughput_roll_mean": feat_row.get("throughput_roll_mean", 0.0),
                "uptime_roll_mean": feat_row.get("uptime_roll_mean", 100.0),
            },
            "model1": {
                "algorithm": "Extended Isolation Forest (18 features trượt · Dữ liệu thật)",
                "feature_count": len(m1_cols),
                "anomaly_score": round(effective_score, 4),
                "threshold": round(th_high, 4),
                "thresholds_4level": {
                    "MEDIUM": round(th_med, 4),
                    "HIGH": round(th_high, 4),
                    "CRITICAL": round(th_crit, 4),
                },
                "severity_level": severity_level,
                "severity_label_vi": SEVERITY_LABELS_VI.get(severity_level, severity_level),
                "is_degraded": m1_degraded,
            },
            "model2": {
                "algorithm": "Công thức Chuỗi thời gian: S_hat = EMA(S_t) + alpha * Slope * R_sustained",
                "feature_count": 5,
                "forecast_score": forecast_score,
                "forecast_level": forecast_level,
                "forecast_label_vi": SEVERITY_LABELS_VI.get(forecast_level, forecast_level),
                "prob_5m_pct": round(prob_5m_pct, 2),
                "threshold_pct": thr_pct,
                "is_sustained": is_sustained,
            },
            "policy_gate": {
                "trigger_model3": trigger_m3,
                "auto_pilot": self.auto_pilot,
                "reason": (
                    f"KÍCH HOẠT MODEL 2 RCA (Hiện tại: {SEVERITY_LABELS_VI.get(severity_level)} · Dự báo 5m: {SEVERITY_LABELS_VI.get(forecast_level)} {prob_5m_pct:.1f}%)"
                    if trigger_m3
                    else (
                        f"CHẶN BÁO ĐỘNG GIẢ (Hiện tại mức {SEVERITY_LABELS_VI.get(severity_level)} nhưng Dự báo 5m về Bình thường {prob_5m_pct:.1f}%)"
                        if severity_level == "MEDIUM"
                        else "BÌNH THƯỜNG (6 thông số SLA của dịch vụ đạt chuẩn)"
                    )
                ),
            },
            "model3": {
                "algorithm": "Model 2 · XGBoost Multi-class RCA (36 features trượt · Dữ liệu thật)",
                "feature_count": len(m2_rca_cols),
                "invoked": trigger_m3,
                "fault_class": fault_class,
                "confidence_pct": confidence,
                "confidence_threshold_pct": round(self.m2_conf_threshold * 100.0, 0),
                "class_probabilities": class_probs,
                "remediation": {
                    "target_node": remediation_plan["target_node"],
                    "oob_ip": remediation_plan["oob_ip"],
                    "attacker_ip": remediation_plan.get("attacker_ip", "-"),
                    "action_code": remediation_plan["action_code"],
                    "title": remediation_plan["title"],
                    "command": remediation_plan["command"],
                },
                "_full_plan": remediation_plan,
            },
        }

    def _process_realtime_policy_actions_unlocked(
        self, service_evaluations: Dict[str, Dict[str, Any]]
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]]:
        """
        Tự động điều phối ứng phó ngay trong vòng lặp thời gian thực (cho cả nút Web lẫn lệnh từ Terminal).
        Trả về danh sách hành động `iptables` cần thực thi NGOÀI lock để không chặn HTTP `/api/state`.
        """
        now_ts = time.time()
        actions_to_run: List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]] = []
        for sid, ev in service_evaluations.items():
            srv_doc = next((s for s in self.db.services_cache if s["service_id"] == sid), ev)

            if ev["severity_level"] == "MEDIUM" and not ev["policy_gate"]["trigger_model3"]:
                if now_ts - self.spike_log_cooldown.get(sid, 0.0) >= 18.0:
                    self.spike_log_cooldown[sid] = now_ts
                    result_id = self.db.record_ai_inference_and_incident(
                        service_id=sid,
                        scenario="TransientSpike",
                        m1_score=ev["model1"]["anomaly_score"],
                        m1_severity=ev["severity_level"],
                        m2_prob_pct=ev["model2"]["prob_5m_pct"],
                        m2_forecast_level=ev["model2"]["forecast_level"],
                        trigger_m3=False,
                        m3_prediction="Normal (Standby)",
                        m3_confidence=0.0,
                        telemetry_snapshot=ev["metrics"],
                        recommendation=ev["impact_description"],
                    )
                    entry = {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "service_id": sid,
                        "scenario": "TransientSpike",
                        "trigger": "SLIDING_WINDOW_FILTER",
                        "fault_class": "TransientSpike",
                        "confidence": f"Hiện tại: {ev['severity_label_vi']} | Dự báo 5m: {ev['model2']['forecast_label_vi']} ({ev['model2']['prob_5m_pct']}%)",
                        "target": f"{ev['service_name']} ({ev['address']}:{ev['port']})",
                        "action": "Cửa sổ trượt phát hiện dao động thoáng qua & Dự báo 5m tự hồi phục -> Chặn báo động giả!",
                        "status": "SUPPRESSED",
                    }
                    self.audit_logs.insert(0, entry)
                    self.audit_logs = self.audit_logs[:30]
                    self.db.record_remediation_action(
                        result_id=result_id,
                        service_id=sid,
                        scenario="TransientSpike",
                        trigger_mode="SLIDING_WINDOW_FILTER",
                        fault_class="TransientSpike",
                        confidence=entry["confidence"],
                        target_device=entry["target"],
                        action_text=entry["action"],
                        status="SUPPRESSED",
                    )
                continue

            if not ev["policy_gate"]["trigger_model3"]:
                continue

            fault_cls = ev["model3"]["fault_class"]
            plan = ev["model3"].get("_full_plan") or build_service_remediation_plan(fault_cls, srv_doc)
            existing_rule = self.active_service_rules.get(sid)
            if (
                existing_rule
                and existing_rule.get("fault_class") == fault_cls
                and all(
                    ip.strip() in str(existing_rule.get("attacker_ip", ""))
                    for ip in str(plan.get("attacker_ip", "")).split(",")
                    if ip.strip()
                )
            ):
                continue
            if (
                sid in self.pending_approvals
                and self.pending_approvals[sid].get("fault_class") == fault_cls
                and self.pending_approvals[sid].get("remediation", {}).get("attacker_ip") == plan.get("attacker_ip")
            ):
                continue
            if now_ts - self.incident_cooldown.get(sid, 0.0) < 12.0:
                continue

            self.incident_cooldown[sid] = now_ts
            conf_str = f"{ev['model3']['confidence_pct']:.1f}%"

            result_id = self.db.record_ai_inference_and_incident(
                service_id=sid,
                scenario=fault_cls,
                m1_score=ev["model1"]["anomaly_score"],
                m1_severity=ev["severity_level"],
                m2_prob_pct=ev["model2"]["prob_5m_pct"],
                m2_forecast_level=ev["model2"]["forecast_level"],
                trigger_m3=True,
                m3_prediction=fault_cls,
                m3_confidence=ev["model3"]["confidence_pct"],
                telemetry_snapshot=ev["metrics"],
                recommendation=plan["title"],
            )

            if fault_cls == "Unknown_Anomaly" or not plan.get("apply_cmds"):
                entry = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "service_id": sid,
                    "scenario": fault_cls,
                    "trigger": "ALERT_ONLY",
                    "fault_class": fault_cls,
                    "confidence": conf_str,
                    "target": f"{srv_doc['service_name']} ({srv_doc['address']}:{srv_doc['port']})",
                    "action": plan["title"],
                    "status": "ALERTED",
                }
                self.audit_logs.insert(0, entry)
                self.audit_logs = self.audit_logs[:30]
                self.db.record_remediation_action(
                    result_id=result_id,
                    service_id=sid,
                    scenario=fault_cls,
                    trigger_mode="ALERT_ONLY",
                    fault_class=fault_cls,
                    confidence=conf_str,
                    target_device=entry["target"],
                    action_text=entry["action"],
                    status="ALERTED",
                )
            elif self.auto_pilot:
                self._register_service_rule_unlocked(
                    service=srv_doc,
                    plan=plan,
                    conf_str=conf_str,
                    mode="AUTO-PILOT",
                    result_id=result_id,
                )
                actions_to_run.append((dict(srv_doc), plan, conf_str, "AUTO-PILOT", result_id))
            else:
                pending_item = {
                    "result_id": result_id,
                    "service_id": sid,
                    "service_name": srv_doc["service_name"],
                    "address": srv_doc["address"],
                    "port": srv_doc["port"],
                    "fault_class": fault_cls,
                    "confidence": conf_str,
                    "remediation": plan,
                }
                self.pending_approvals[sid] = pending_item
                entry = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "service_id": sid,
                    "scenario": fault_cls,
                    "trigger": "MANUAL_WAIT",
                    "fault_class": fault_cls,
                    "confidence": conf_str,
                    "target": f"{srv_doc['service_name']} ({srv_doc['address']}:{srv_doc['port']})",
                    "action": f"[CHỜ PHÊ DUYỆT] {plan['title']}",
                    "status": "PENDING",
                }
                self.audit_logs.insert(0, entry)
                self.audit_logs = self.audit_logs[:30]
                self.db.record_remediation_action(
                    result_id=result_id,
                    service_id=sid,
                    scenario=fault_cls,
                    trigger_mode="MANUAL_WAIT",
                    fault_class=fault_cls,
                    confidence=conf_str,
                    target_device=entry["target"],
                    action_text=entry["action"],
                    status="PENDING",
                )
        return actions_to_run

    def _compute_evaluations_no_lock(
        self,
        services_list: List[Dict[str, Any]],
        sample: Tuple[bool, Dict[str, float], Dict[str, Dict[str, float]]],
        lab_up: bool,
        dp_ready: bool,
    ) -> Dict[str, Dict[str, Any]]:
        """Tính Cửa sổ trượt + Batch Model 1 EIF + Model 2 RCA NGOÀI self._lock để HTTP không bị trễ."""
        _, _, sla_map = sample
        default_offline_kpis = {
            "latency": 2.0,
            "jitter": 0.5,
            "packet_loss": 0.0,
            "throughput": 0.0,
            "response_time": 5.0,
            "uptime": 100.0,
        }
        feat_rows: List[Tuple[Dict[str, Any], Dict[str, float]]] = []
        for srv in services_list:
            sid = srv["service_id"]
            inst_kpis = sla_map.get(sid, default_offline_kpis)
            feat_row = self._build_sliding_window_row_for_service(sid, inst_kpis)
            feat_rows.append((srv, feat_row))

        # Gọi EIF 1 LẦN DUY NHẤT theo batch (nhanh gấp 4x so với gọi lẻ từng dịch vụ!)
        m1_cols: List[str] = self.m1_bundle["feature_names"]
        if feat_rows:
            x_m1_batch = np.array([[float(fr.get(c, 0.0)) for c in m1_cols] for _, fr in feat_rows])
            x_m1_batch_s = np.arcsinh(self.m1_bundle["scaler"].transform(x_m1_batch))
            eif_scores = self.m1_model.anomaly_score(x_m1_batch_s)
        else:
            eif_scores = []

        service_evaluations: Dict[str, Dict[str, Any]] = {}
        for idx, (srv, feat_row) in enumerate(feat_rows):
            ev = self._evaluate_single_service(srv, feat_row, precomputed_eif_score=float(eif_scores[idx]))
            service_evaluations[srv["service_id"]] = ev
        return service_evaluations

    def _commit_step_unlocked(
        self,
        services_snapshot: List[Dict[str, Any]],
        sample: Tuple[bool, Dict[str, float], Dict[str, Dict[str, float]]],
        service_evaluations: Dict[str, Dict[str, Any]],
        custom_time: str | None = None,
        allow_remediation: bool = True,
    ) -> Tuple[Dict[str, Any], List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]]]:
        self.tick_counter += 1
        now_t = custom_time or datetime.now().strftime("%H:%M:%S")
        _, host_tele, _ = sample

        actions_to_run: List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]] = []
        if allow_remediation and custom_time is None and self.lab_running and self.dataplane_ready:
            actions_to_run = self._process_realtime_policy_actions_unlocked(service_evaluations)

        services_perf: Dict[str, Dict[str, float]] = {}
        for sid, ev in service_evaluations.items():
            active_rule = self.active_service_rules.get(sid)
            pending_rule = self.pending_approvals.get(sid)
            ev["active_rule"] = active_rule
            ev["pending_rule"] = pending_rule
            ev["protection_status"] = (
                "PROTECTED" if active_rule else ("PENDING_APPROVAL" if pending_rule else "MONITORING")
            )
            ev["policy_gate"]["auto_pilot"] = self.auto_pilot
            services_perf[sid] = ev["metrics"]

        self.db.update_services_live_state(service_evaluations)

        sev_order = {"NORMAL": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        primary_ev = max(
            service_evaluations.values(),
            key=lambda e: (sev_order.get(e["severity_level"], 0), e["model1"]["anomaly_score"]),
        ) if service_evaluations else None

        point = {
            "time": now_t,
            "eif_score": primary_ev["model1"]["anomaly_score"] if primary_ev else 0.38,
            "prob_5m_pct": primary_ev["model2"]["prob_5m_pct"] if primary_ev else 1.0,
            "services": services_perf,
        }
        self.performance_history.append(point)
        if len(self.performance_history) > 16:
            self.performance_history = self.performance_history[-16:]

        blocked_targets = [
            f"{r['attacker_ip']} -> {r['service_name']} ({r['address']}:{r['port']})"
            for r in self.active_service_rules.values()
        ]
        first_pending = next(iter(self.pending_approvals.values()), None)

        state = {
            "timestamp": now_t,
            "lab_running": self.lab_running,
            "dataplane_ready": self.dataplane_ready,
            "performance_history": list(self.performance_history),
            "service_evaluations": service_evaluations,
            "model1": primary_ev["model1"] if primary_ev else {},
            "model2": primary_ev["model2"] if primary_ev else {},
            "policy_gate": primary_ev["policy_gate"] if primary_ev else {"auto_pilot": self.auto_pilot},
            "model3": primary_ev["model3"] if primary_ev else {},
            "telemetry": {
                "active_services_count": len(services_snapshot),
                "protected_services_count": len(self.active_service_rules),
                "pending_approvals_count": len(self.pending_approvals),
                "fw_rx_pkts_rate": round(float(host_tele.get("fw_rx_pkts", 0.0)), 1),
                "fw_out_pkts_rate": round(float(host_tele.get("fw_out_pkts", 0.0)), 1),
                "srv_rx_pkts_rate": round(float(host_tele.get("srv_rx_pkts", 0.0)), 1),
                "srv_tcp_rst_rate": round(float(host_tele.get("tcp_rst_rate", 0.0)), 1),
                "srv_cpu_usage": round(float(host_tele.get("srv_cpu", 0.0)), 1),
                "nginx_req_rate": round(float(host_tele.get("nginx_rps", 0.0)), 1),
                "api_req_rate": round(float(host_tele.get("api_rps", 0.0)), 1),
                "api_latency_ms": round(float(host_tele.get("api_lat_s", 0.0)) * 1000.0, 1),
            },
            "network_state": {
                "blocked_ips": blocked_targets,
                "active_service_rules": list(self.active_service_rules.values()),
                "pending_approvals": list(self.pending_approvals.values()),
                "pending_approval": first_pending,
            },
            "db_stats": self.db.get_collection_stats(),
            "audit_logs": self.audit_logs[:15],
        }
        return state, actions_to_run

    def _patch_latest_state_unlocked(self) -> Dict[str, Any]:
        """Cập nhật tức thì (< 0.5ms) trạng thái hiển thị sau các thao tác bấm nút mà không gọi docker exec."""
        if not self.latest_state:
            return self.latest_state
        st = dict(self.latest_state)
        st["audit_logs"] = self.audit_logs[:15]
        st["db_stats"] = self.db.get_collection_stats()
        if isinstance(st.get("policy_gate"), dict):
            pg = dict(st["policy_gate"])
            pg["auto_pilot"] = self.auto_pilot
            st["policy_gate"] = pg
        evals = dict(st.get("service_evaluations") or {})
        valid_ids = {s["service_id"] for s in self.db.services_cache}
        for sid in list(evals.keys()):
            if sid not in valid_ids:
                evals.pop(sid, None)
        for sid, ev in evals.items():
            ev_c = dict(ev)
            active_rule = self.active_service_rules.get(sid)
            pending_rule = self.pending_approvals.get(sid)
            ev_c["active_rule"] = active_rule
            ev_c["pending_rule"] = pending_rule
            ev_c["protection_status"] = (
                "PROTECTED" if active_rule else ("PENDING_APPROVAL" if pending_rule else "MONITORING")
            )
            if isinstance(ev_c.get("policy_gate"), dict):
                ev_c["policy_gate"] = {**ev_c["policy_gate"], "auto_pilot": self.auto_pilot}
            evals[sid] = ev_c
        st["service_evaluations"] = evals
        blocked_targets = [
            f"{r['attacker_ip']} -> {r['service_name']} ({r['address']}:{r['port']})"
            for r in self.active_service_rules.values()
        ]
        st["network_state"] = {
            "blocked_ips": blocked_targets,
            "active_service_rules": list(self.active_service_rules.values()),
            "pending_approvals": list(self.pending_approvals.values()),
            "pending_approval": next(iter(self.pending_approvals.values()), None),
        }
        self.latest_state = st
        return st

    def _step_all_services_unlocked(
        self,
        custom_time: str | None = None,
        prefetched_sample: Tuple[bool, Dict[str, float], Dict[str, Dict[str, float]]] | None = None,
        allow_remediation: bool = True,
    ) -> Dict[str, Any]:
        services_list = [dict(s) for s in self.db.services_cache]
        sample = prefetched_sample if prefetched_sample is not None else (False, {}, {})
        evals = self._compute_evaluations_no_lock(services_list, sample, self.lab_running, self.dataplane_ready)
        state, _ = self._commit_step_unlocked(
            services_snapshot=services_list,
            sample=sample,
            service_evaluations=evals,
            custom_time=custom_time,
            allow_remediation=allow_remediation,
        )
        return state

    def get_latest_state(self) -> Dict[str, Any]:
        with self._lock:
            return self.latest_state

    def _register_service_rule_unlocked(
        self,
        service: Dict[str, Any],
        plan: Dict[str, Any],
        conf_str: str,
        mode: str,
        result_id: str = "manual",
    ) -> None:
        sid = service["service_id"]
        self.pending_approvals.pop(sid, None)
        prev = self.active_service_rules.get(sid)
        if prev and prev.get("fault_class") == plan["fault_class"]:
            # Gộp thêm IP tấn công mới nếu có thêm nguồn thứ 2/3 tấn công vào cùng dịch vụ đã có rule
            merged_ips = []
            for s in (prev.get("attacker_ip", ""), plan.get("attacker_ip", "")):
                for ip in str(s).split(","):
                    ip = ip.strip()
                    if ip and ip not in merged_ips:
                        merged_ips.append(ip)
            merged_rb = list(prev.get("rollback_cmds") or [])
            for c in plan.get("rollback_cmds") or []:
                if c not in merged_rb:
                    merged_rb.append(c)
            merged_cmd = prev.get("command", "")
            if plan.get("command") and plan["command"] not in merged_cmd:
                merged_cmd = f"{merged_cmd} && {plan['command']}" if merged_cmd else plan["command"]
            rule_record = dict(prev)
            rule_record["attacker_ip"] = ", ".join(merged_ips)
            rule_record["command"] = merged_cmd
            rule_record["rollback_cmds"] = merged_rb
            rule_record["applied_at"] = datetime.now().strftime("%H:%M:%S")
        else:
            rule_record = {
                "service_id": sid,
                "service_name": service["service_name"],
                "address": service["address"],
                "port": service["port"],
                "fault_class": plan["fault_class"],
                "attacker_ip": plan.get("attacker_ip", "203.0.113.10"),
                "target_node": plan["target_node"],
                "command": plan["command"],
                "rollback_cmd": plan.get("rollback_cmd", []),
                "rollback_cmds": list(plan.get("rollback_cmds") or []),
                "applied_at": datetime.now().strftime("%H:%M:%S"),
                "mode": mode,
            }
        self.active_service_rules[sid] = rule_record

    def _run_remediation_async(
        self,
        service: Dict[str, Any],
        plan: Dict[str, Any],
        conf_str: str,
        mode: str,
        result_id: str = "manual",
        actor: str = "AUTO-PILOT"
    ) -> None:
        def _worker() -> None:
            sid = service["service_id"]
            _, exec_status = execute_service_remediation(plan)
            target_str = f"{service['service_name']} ({service['address']}:{service['port']})"
            action_str = f"[{exec_status}] {plan['title']} | CMD: {plan['command']}"
            with self._lock:
                self.audit_logs.insert(
                    0,
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "service_id": sid,
                        "scenario": plan["fault_class"],
                        "trigger": mode,
                        "fault_class": plan["fault_class"],
                        "confidence": conf_str,
                        "target": target_str,
                        "action": action_str,
                        "status": "REMEDIATED",
                        "actor": actor,
                    },
                )
                self.audit_logs = self.audit_logs[:30]
                self.db.record_remediation_action(
                    result_id=result_id,
                    service_id=sid,
                    scenario=plan["fault_class"],
                    trigger_mode=mode,
                    fault_class=plan["fault_class"],
                    confidence=conf_str,
                    target_device=target_str,
                    action_text=action_str,
                    status="REMEDIATED",
                    actor=actor,
                )
                self._patch_latest_state_unlocked()

        threading.Thread(target=_worker, daemon=True).start()

    def approve_pending(self, service_id: str | None = None, actor: str = "admin (Web)") -> Dict[str, Any]:
        to_run: List[Tuple[Dict[str, Any], Dict[str, Any], str, str, str]] = []
        with self._lock:
            target_ids = (
                [service_id]
                if (service_id and service_id in self.pending_approvals)
                else list(self.pending_approvals.keys())
            )
            for sid in target_ids:
                p = self.pending_approvals.get(sid)
                if not p:
                    continue
                srv_doc = next((s for s in self.db.services_cache if s["service_id"] == sid), p)
                self._register_service_rule_unlocked(
                    service=srv_doc,
                    plan=p["remediation"],
                    conf_str=p["confidence"],
                    mode="HUMAN-APPROVED",
                    result_id=p.get("result_id", "manual"),
                )
                to_run.append((dict(srv_doc), p["remediation"], p["confidence"], "HUMAN-APPROVED", p.get("result_id", "manual")))
            st = self._patch_latest_state_unlocked()

        for srv_doc, plan, conf_str, mode, result_id in to_run:
            self._run_remediation_async(srv_doc, plan, conf_str, mode, result_id, actor=actor)
        return st

    def rollback_service_or_all(self, service_id: str | None = None, actor: str = "admin (Web)") -> Dict[str, Any]:
        with self._lock:
            if service_id and service_id in self.active_service_rules:
                rule = self.active_service_rules.pop(service_id)
                self.incident_cooldown.pop(service_id, None)
                remaining = list(self.active_service_rules.values())
                stop_attacks = not self.active_service_rules
                target_str = f"{rule['service_name']} ({rule['address']}:{rule['port']})"
                action_str = (
                    f"[ROLLED_BACK (LIVE OOB)] Đã gỡ rule bảo vệ riêng của dịch vụ {target_str} bằng iptables -D "
                    f"(không ảnh hưởng các dịch vụ khác)"
                )
                self.audit_logs.insert(
                    0,
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "service_id": service_id,
                        "scenario": "ServiceRollback",
                        "trigger": "SERVICE_RESET",
                        "fault_class": rule["fault_class"],
                        "confidence": "100%",
                        "target": target_str,
                        "action": action_str,
                        "status": "ROLLED_BACK",
                        "actor": actor,
                    },
                )
                self.audit_logs = self.audit_logs[:30]
                self.db.record_remediation_action(
                    result_id="rollback_srv",
                    service_id=service_id,
                    scenario="ServiceRollback",
                    trigger_mode="SERVICE_RESET",
                    fault_class=rule["fault_class"],
                    confidence="100%",
                    target_device=target_str,
                    action_text=action_str,
                    status="ROLLED_BACK",
                    actor=actor
                )
                st = self._patch_latest_state_unlocked()

                def _rb_one() -> None:
                    execute_service_rollback(rule, remaining_rules=remaining)
                    if stop_attacks:
                        stop_active_attack_processes()

                threading.Thread(target=_rb_one, daemon=True).start()
                return st
            else:
                old_rules = list(self.active_service_rules.values())
                services_snap = [dict(s) for s in self.db.services_cache]
                self.active_service_rules.clear()
                self.pending_approvals.clear()
                self.incident_cooldown.clear()
                action_str = "[ROLLED_BACK (LIVE OOB)] Đã gỡ bỏ toàn bộ các luật chặn iptables tạm thời cho tất cả dịch vụ"
                self.audit_logs.insert(
                    0,
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "service_id": "ALL_SERVICES",
                        "scenario": "RollbackAll",
                        "trigger": "ADMIN_RESET",
                        "fault_class": "Normal",
                        "confidence": "100%",
                        "target": "All Services & Firewalls",
                        "action": action_str,
                        "status": "ROLLED_BACK",
                        "actor": actor,
                    },
                )
                self.audit_logs = self.audit_logs[:30]
                self.db.record_remediation_action(
                    result_id="rollback_all",
                    service_id="ALL_SERVICES",
                    scenario="RollbackAll",
                    trigger_mode="ADMIN_RESET",
                    fault_class="Normal",
                    confidence="100%",
                    target_device="All Services & Firewalls",
                    action_text=action_str,
                    status="ROLLED_BACK",
                    actor=actor
                )
                st = self._patch_latest_state_unlocked()

                def _rb_all() -> None:
                    execute_oob_rollback(old_rules, services=services_snap)
                    self.collector.reset_accounting_cache()

                threading.Thread(target=_rb_all, daemon=True).start()
                return st


ENGINE = AIOpsStateEngine()


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            return json.loads(raw_body)
        except Exception:
            return {}

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _is_local_request(self) -> bool:
        """Kiểm tra request có đến từ localhost (để scripts/verify.sh không bị chặn)."""
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _require_auth(self, admin_only: bool = False) -> Dict[str, Any] | None:
        """Xác thực Bearer Token; nếu admin_only=True thì chỉ cho phép role ADMIN."""
        token = extract_bearer_token(self.headers)
        user = verify_token(token) if token else None
        
        if not user:
            self._send_json({"error": "Chưa đăng nhập hoặc phiên đã hết hạn"}, status=401)
            return None
        
        if admin_only and user.get("role") != "ADMIN":
            self._send_json({"error": "Chỉ ADMIN mới có quyền thao tác"}, status=403)
            return None
        
        return user

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "File Not Found")
            return
        mime, _ = mimetypes.guess_type(str(file_path))
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime or 'text/plain'}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_frontend(self) -> None:
        index = DIST_DIR / "index.html"
        if not index.exists():
            msg = (
                "Chưa build frontend. Chạy: cd monitoring/aiops-dashboard/frontend "
                "&& npm install && npm run build"
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        rel = self.path.split("?", 1)[0].lstrip("/")
        target = (DIST_DIR / rel).resolve() if rel else index
        if DIST_DIR.resolve() not in target.parents or not target.is_file():
            target = index
        self._send_file(target)

    def do_GET(self) -> None:
        if self.path.startswith("/api/auth/me"):
            user = self._require_auth()
            if not user:
                return
            self._send_json({"user": user})

        elif self.path.startswith("/api/state"):
            if not self._is_local_request() and not self._require_auth():
                return
            self._send_json(ENGINE.get_latest_state())          

        elif self.path.startswith("/api/database"):

            if not self._require_auth():
                return
            
            # Trả dữ liệu bộ nhớ đệm tức thì (< 2ms), KHÔNG chặn HTTP bằng 8 truy vấn mạng tới MongoDB Atlas mỗi 2 giây!
            with ENGINE._lock:
                payload = {
                    "stats": ENGINE.db.get_collection_stats(),
                    "Services": list(ENGINE.db.services_cache),
                    "AlertRules": list(ENGINE.db.rules_cache),
                    "AIResults": list(ENGINE.db.ai_results_cache[:12]),
                    "Alerts": list(ENGINE.db.alerts_cache[:12]),
                    "RemediationActions": list(ENGINE.db.remediations_cache[:12]),
                }
            self._send_json(payload)

        elif self.path.startswith("/api/services"):
            
            if not self._require_auth(): 
                return
            
            with ENGINE._lock:
                services = list(ENGINE.db.services_cache)
            self._send_json({"Services": services})
        
        elif self.path.startswith("/api/users"):
            if not self._require_auth(admin_only=True):
                return
            with ENGINE._lock:
                users = ENGINE.db.list_users()
            self._send_json({"Users": users})        

        elif self.path.startswith("/api/"):
            self.send_error(404, "Unknown API endpoint")
        
        else:
            self._serve_frontend()

    def do_POST(self) -> None:
        body = self._read_json_body()

        if self.path == "/api/auth/login":
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            user_doc = ENGINE.db.get_user_by_username(username)

            if not user_doc or not verify_password(password, user_doc.get("password_hash", "")):
                self._send_json({"error": "Sai tên đăng nhập hoặc mật khẩu"}, status=401)
                return
            
            token = create_token({
                'username': user_doc["username"],
                'role': user_doc["role"],
                'full_name': user_doc.get("full_name", ""),
            })

            self._send_json({
                "token": token,
                "user": {
                    "username": user_doc["username"],
                    "role": user_doc["role"],
                    "full_name": user_doc.get("full_name", ""),
                },
            })
            return
        
        elif self.path == "/api/auth/logout":
            token = extract_bearer_token(self.headers)
            if token:
                revoke_token(token)
            self._send_json({"status": "ok"})
            return
        
        elif self.path == "/alerts":
            ENGINE.alertmanager_webhooks.insert(
                0, {"time": datetime.now().strftime("%H:%M:%S"), "payload": body}
            )
            self._send_json({"status": "ok"})
            return

        # Toàn bộ API ghi bên dưới bắt buộc phải là ADMIN
        user = self._require_auth(admin_only=True)
        if not user:
            return
        
        actor = f"{user['username']} (Web)"

        if self.path == "/api/policy":
            with ENGINE._lock:
                if "threshold" in body:
                    ENGINE.forecast_threshold = float(body["threshold"]) / 100.0
                if "auto_pilot" in body:
                    ENGINE.auto_pilot = bool(body["auto_pilot"])
                ENGINE.db.update_policy_rule(
                    threshold_pct=body.get("threshold"), auto_pilot=body.get("auto_pilot")
                )
                state = ENGINE._patch_latest_state_unlocked()
            self._send_json(state)

        elif self.path == "/api/approve":
            state = ENGINE.approve_pending(service_id=body.get("service_id"), actor=actor)
            self._send_json(state)

        elif self.path == "/api/rollback":
            state = ENGINE.rollback_service_or_all(service_id=body.get("service_id"), actor=actor)
            self._send_json(state)
        
        elif self.path.startswith("/api/services"):
            with ENGINE._lock:
                ENGINE.db.create_service(body)
                services_snap = [dict(s) for s in ENGINE.db.services_cache]
                state = ENGINE._patch_latest_state_unlocked()
            if ENGINE.lab_running and ENGINE.dataplane_ready:
                threading.Thread(
                    target=ENGINE.collector.ensure_services_registered,
                    args=(services_snap,),
                    daemon=True,
                ).start()
            self._send_json(state)

        elif self.path.rstrip("/") == "/api/users":
            with ENGINE._lock:
                created, err = ENGINE.db.create_user(body)
                users = ENGINE.db.list_users()
            if err:
                self._send_json({"error": err}, status=400)
                return
            self._send_json({"user": created, "Users": users}, status=201)

        else:
            self.send_error(404, "Unknown API endpoint")

    def do_PUT(self) -> None:
        body = self._read_json_body()
        if self.path.startswith("/api/services"):
            
            if not self._require_auth(admin_only=True):
                return
            
            parts = self.path.rstrip("/").split("/")
            sid = parts[3] if len(parts) > 3 else str(body.get("service_id", ""))
            with ENGINE._lock:
                ENGINE.db.update_service(sid, body)
                services_snap = [dict(s) for s in ENGINE.db.services_cache]
                state = ENGINE._patch_latest_state_unlocked()
            if ENGINE.lab_running and ENGINE.dataplane_ready:
                threading.Thread(
                    target=ENGINE.collector.ensure_services_registered,
                    args=(services_snap,),
                    daemon=True,
                ).start()
            self._send_json(state)

        elif self.path.startswith("/api/users"):
            if not self._require_auth(admin_only=True):
                return
            parts = self.path.split("?", 1)[0].rstrip("/").split("/")
            uname = parts[3] if len(parts) > 3 else str(body.get("username", ""))
            with ENGINE._lock:
                updated, err = ENGINE.db.update_user(uname, body)
                users = ENGINE.db.list_users()
            if err:
                self._send_json({"error": err}, status=400)
                return
            self._send_json({"user": updated, "Users": users})        

        else:
            self.send_error(404, "Unknown API endpoint")

    def do_DELETE(self) -> None:
        body = self._read_json_body()
        if self.path.startswith("/api/services"):

            if not self._require_auth(admin_only=True):
                return
            
            parts = self.path.rstrip("/").split("/")
            sid = parts[3] if len(parts) > 3 else str(body.get("service_id", ""))
            with ENGINE._lock:
                ENGINE.db.delete_service(sid)
                ENGINE.service_windows.pop(sid, None)
                ENGINE.service_score_windows.pop(sid, None)
                ENGINE.active_service_rules.pop(sid, None)
                ENGINE.pending_approvals.pop(sid, None)
                state = ENGINE._patch_latest_state_unlocked()
            self._send_json(state)
        
        elif self.path.startswith("/api/users"):
            user = self._require_auth(admin_only=True)
            if not user:
                return
            parts = self.path.split("?", 1)[0].rstrip("/").split("/")
            uname = parts[3] if len(parts) > 3 else str(body.get("username", ""))
            with ENGINE._lock:
                ok, err = ENGINE.db.delete_user(uname, current_username=user["username"])
                users = ENGINE.db.list_users()
            if not ok:
                self._send_json({"error": err}, status=400)
                return
            self._send_json({"status": "ok", "Users": users})        
        
        else:
            self.send_error(404, "Unknown API endpoint")


def main() -> None:
    import signal
    import subprocess

    my_pid = os.getpid()
    parent_pid = os.getppid()
    try:
        out = subprocess.check_output(["pgrep", "-f", "aiops-dashboard/backend/app.py"], text=True)
        for p in out.splitlines():
            p = p.strip()
            if not p:
                continue
            pid_int = int(p)
            if pid_int in (my_pid, parent_pid):
                continue
            try:
                cmdline = Path(f"/proc/{pid_int}/cmdline").read_bytes().decode("utf-8", errors="ignore")
                argv0 = cmdline.split("\x00", 1)[0]
                if "python" in os.path.basename(argv0):
                    os.kill(pid_int, signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.4)
    except Exception:
        pass

    host = "0.0.0.0"
    port = int(os.environ.get("AIOPS_PORT", "5000"))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), DashboardHTTPHandler)
    print("=" * 78)
    print(f"[✓] AIOps Command Center (100% DỮ LIỆU THẬT) đang chạy tại: http://localhost:{port}")
    print(
        f"    (Containerlab: {'UP' if ENGINE.lab_running else 'OFFLINE'} | "
        f"Data Plane eth1: {'READY' if ENGINE.dataplane_ready else 'CẦN sudo ./scripts/deploy.sh'})"
    )
    print("=" * 78)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Đã dừng AIOps Command Center.")
        server.server_close()


if __name__ == "__main__":
    main()
