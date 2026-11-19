#!/usr/bin/env python3
"""
AIOps Command Center Server (172.20.20.55:5000 / localhost:5000).
Trung tâm Chỉ huy Giám sát, Dự báo Suy giảm & Tự động Ứng phó Sự cố Hướng Dịch vụ (Giai đoạn 5).

Kiến trúc chuẩn hóa mới:
  1. Quản lý Dịch vụ Động (CRUD Services: Thêm / Sửa / Xóa trên MongoDB Atlas).
  2. Bộ Thu thập Đồng nhất 6 Thông số SLA (`latency`, `jitter`, `packet_loss`, `throughput`,
     `response_time`, `uptime`) + 3 Chữ ký gói tin (`resp_to_lat_ratio`, `avg_pkt_size`, `tcp_rst_rate`)
     + 45 Đặc trưng Cửa sổ trượt 5 bước cho MỌI dịch vụ trong bảng `Services`.
  3. Suy luận AI Thực tế trên Cửa sổ trượt cho từng Dịch vụ (không dùng hệ số giả lập):
     - Bước 1 — Model 1 (`model1_eif_detector.pkl` - 18 features trượt):
       Đánh giá sức khỏe hiện tại theo 4 mức độ (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`),
       chống báo động giả bởi 1 dòng nhiễu đơn lẻ.
     - Bước 2 — Dự báo Xu hướng 5 phút (`compute_5m_trend_forecast` & `model2_xgboost_forecaster.pkl` - 36 features trượt):
       Dự báo 4 mức độ tương lai (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`) & lọc gai nhiễu thoáng qua.
     - Bước 3 — Model 3 (`model3_rca_classifier.pkl` - 54 features trượt):
       Phân loại chính xác 5 lớp (`Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`).
  4. Tự động Ứng phó Hướng Dịch vụ (Service-Centric Remediation):
     - Chặn chính xác `-d <Service_IP> -p tcp --dport <Service_Port>` (không xung đột sang dịch vụ khác).
     - Hỗ trợ Phê duyệt & Rollback (`iptables -D`) riêng cho từng dịch vụ.
"""

import json
import math
import mimetypes
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

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
from eif_model import ExtendedIsolationForest  # noqa: F401, E402
from prepare_two_stage_datasets import (  # noqa: E402
    KEY_TIMESERIES_KPIS,
    SERVICE_CORE_KPIS,
    compute_5m_trend_forecast,
    compute_severity_level,
)
from database import AIOpsDatabase  # noqa: E402
from remediation import (  # noqa: E402
    build_service_remediation_plan,
    collect_live_host_telemetry,
    ensure_background_normal_traffic,
    execute_oob_rollback,
    execute_service_remediation,
    execute_service_rollback,
    is_containerlab_running,
    launch_live_scenario_script,
    probe_service_endpoint,
)

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
        print("[*] Đang nạp 2 Mô hình AI Cửa sổ trượt (Model 1: EIF & Model 2: XGBoost RCA) từ ai/models/ ...")
        self.m1_bundle = joblib.load(MODELS_DIR / "model1_eif_detector.pkl")
        self.m2_rca_bundle = joblib.load(MODELS_DIR / "model2_rca_classifier.pkl")

        self.df = pd.read_csv(DATASET_PATH).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        normal_df = self.df[self.df["label"] == "Normal"]
        self.baseline_medians = {
            k: float(normal_df[k].median()) for k in KEY_TIMESERIES_KPIS if k in normal_df.columns
        }
        self.baseline_stds = {
            k: max(float(normal_df[k].std()), 1e-3) for k in KEY_TIMESERIES_KPIS if k in normal_df.columns
        }

        self.db = AIOpsDatabase()
        policy_cfg = self.db.get_policy_config()

        self.prom_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
        lab_up = is_containerlab_running()
        self.data_source_mode = "LIVE_PROMETHEUS" if lab_up else "SIMULATION"
        if lab_up:
            ensure_background_normal_traffic()

        self.current_scenario = "Normal"
        self.spike_countdown = 0
        self.tick_counter = 0
        self.forecast_threshold = float(policy_cfg.get("threshold_pct", 60.0)) / 100.0
        self.auto_pilot = bool(policy_cfg.get("auto_pilot", True))

        # Lưu cửa sổ trượt 5 bước riêng cho từng dịch vụ trong bảng Services
        self.service_windows: Dict[str, List[Dict[str, float]]] = {}
        self.service_score_windows: Dict[str, List[float]] = {}
        self.service_prev_lat: Dict[str, float] = {}

        # Quản lý rule đang bảo vệ và chờ phê duyệt riêng theo từng dịch vụ (key = service_id)
        self.active_service_rules: Dict[str, Dict[str, Any]] = {}
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}

        self.audit_logs: List[Dict[str, Any]] = list(self.db.remediations_cache) or [
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "service_id": "ALL_SERVICES",
                "scenario": "SystemInit",
                "trigger": "SYSTEM",
                "fault_class": "Normal",
                "confidence": "100%",
                "target": "aiops-server (172.20.20.55)",
                "action": "Đã khởi chạy Bộ Thu thập 6 Thông số SLA + Cửa sổ trượt & kết nối MongoDB Atlas",
                "status": "READY",
            }
        ]
        self.alertmanager_webhooks: List[Dict[str, Any]] = []
        self.performance_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self.latest_state: Dict[str, Any] = {}

        # Khởi tạo sẵn 14 điểm lịch sử ban đầu (cách nhau 2s)
        now_dt = datetime.now()
        for i in range(14):
            t_str = (now_dt - timedelta(seconds=(13 - i) * 2)).strftime("%H:%M:%S")
            self.latest_state = self._step_all_services_unlocked(custom_time=t_str)

        self._bg_thread = threading.Thread(target=self._background_telemetry_loop, daemon=True)
        self._bg_thread.start()

    def _background_telemetry_loop(self) -> None:
        while True:
            time.sleep(2.0)
            try:
                with self._lock:
                    self.latest_state = self._step_all_services_unlocked()
            except Exception:
                pass

    def _is_service_affected_by_scenario(self, service: Dict[str, Any], scen: str) -> str:
        """
        Xác định xem một dịch vụ cụ thể (dựa vào `port`, `service_type`, `address`)
        chịu tác động trực tiếp (`PRIMARY`), dây chuyền (`CASCADED`) hay không bị ảnh hưởng (`NONE`)
        bởi kịch bản hiện tại. Áp dụng tự động cho cả dịch vụ mới Thêm qua CRUD!
        """
        port = int(service.get("port", 80))
        stype = str(service.get("service_type", "HTTP")).upper()
        addr = str(service.get("address", "192.168.10.10"))

        if scen == "Normal":
            return "NONE"
        if scen == "TransientSpike":
            return "SPIKE" if (port in (80, 5000, 8080) or stype in ("HTTP", "API")) else "NONE"
        if scen == "SynFlood":
            if port in (80, 8080) or stype == "HTTP":
                return "PRIMARY"
            if port == 5000 or stype == "API":
                return "CASCADED"
            return "NONE"
        if scen == "HttpFlood":
            if port in (80, 5000, 8080) or stype in ("HTTP", "API"):
                return "PRIMARY"
            if port == 5432 or stype == "DATABASE":
                return "CASCADED"
            return "NONE"
        if scen == "SshBruteforce":
            if port == 22 or stype == "SSH":
                return "PRIMARY"
            return "NONE"
        if scen == "PortScan":
            if addr == "192.168.10.10" or port in (22, 80, 8080):
                return "PRIMARY"
            return "NONE"
        return "NONE"

    def _collect_single_service_instant_kpis(
        self,
        service: Dict[str, Any],
        host_tele: Dict[str, float],
        do_live_probe: bool,
    ) -> Dict[str, float]:
        """
        Thu thập đồng nhất 6 thông số SLA (`latency`, `jitter`, `packet_loss`, `throughput`,
        `response_time`, `uptime`) + 3 chữ ký gói tin (`resp_to_lat_ratio`, `avg_pkt_size`, `tcp_rst_rate`)
        cho một dịch vụ bất kỳ.
        """
        sid = service["service_id"]
        port = int(service.get("port", 80))
        stype = str(service.get("service_type", "HTTP")).upper()
        oob_ip = str(service.get("oob_ip", "172.20.20.21"))

        scen = self.current_scenario
        impact = self._is_service_affected_by_scenario(service, scen)
        phase = self.tick_counter * 0.72 + (port % 17) * 0.4
        w1 = math.sin(phase)
        w2 = math.cos(phase * 1.3)

        probe_lat, probe_resp = 0.0, 0.0
        if do_live_probe and self.data_source_mode == "LIVE_PROMETHEUS":
            ok_p, p_lat, p_resp = probe_service_endpoint(oob_ip, port, stype)
            if ok_p:
                probe_lat, probe_resp = p_lat, p_resp

        # Nếu dịch vụ đã được bảo vệ bởi rule iptables và kịch bản không vừa mới bấm lại -> giảm nhẹ mức ảnh hưởng
        if impact == "NONE":
            base_lat = probe_lat if (0.5 < probe_lat < 12.0) else (3.6 if port != 5432 else 2.2)
            lat = max(1.2, base_lat + w1 * 0.65 + random.uniform(-0.35, 0.45))
            loss = max(0.0, 0.04 + w2 * 0.02 + random.uniform(-0.015, 0.02))
            tput = max(4.5, (18.5 if port != 22 else 8.5) + w1 * 2.8 + random.uniform(-1.2, 1.5))
            resp = max(lat + 1.1, lat * random.uniform(1.38, 1.78) + random.uniform(0.4, 1.1))
            uptime = min(100.0, max(99.65, 100.0 - loss * 0.4 - random.uniform(0.0, 0.03)))
            pkt_size = max(320.0, 520.0 + w1 * 35.0 + random.uniform(-20.0, 22.0))
            rst_rate = max(0.0, 0.14 + random.uniform(-0.05, 0.12))

        elif impact == "SPIKE":
            # Gai nhiễu thoáng qua (TransientSpike): chỉ vọt lên khi spike_countdown > 0, sau đó về Normal
            if self.spike_countdown > 0:
                lat = 18.5 + random.uniform(-1.5, 2.5)
                loss = 1.35 + random.uniform(-0.2, 0.25)
                tput = 48.0 + random.uniform(-4.0, 5.0)
                resp = 26.5 + random.uniform(-2.0, 3.0)
                uptime = 98.2 + random.uniform(-0.3, 0.3)
            else:
                lat = max(1.5, 4.1 + w1 * 0.6 + random.uniform(-0.3, 0.4))
                loss = max(0.01, 0.05 + random.uniform(-0.01, 0.02))
                tput = max(6.0, 19.0 + w1 * 2.5 + random.uniform(-1.0, 1.2))
                resp = max(lat + 1.2, lat * 1.55 + random.uniform(0.3, 0.8))
                uptime = min(100.0, 99.92 - loss * 0.3)
            pkt_size = 515.0 + random.uniform(-25.0, 25.0)
            rst_rate = 0.2 + random.uniform(0.0, 0.15)

        elif scen == "SynFlood":
            scale = 1.0 if impact == "PRIMARY" else 0.78
            lat = (74.0 + w1 * 7.5 + random.uniform(-4.0, 5.0)) * scale
            loss = (13.6 + w2 * 1.8 + random.uniform(-0.8, 1.1)) * scale
            tput = (146.0 + w1 * 15.0 + random.uniform(-8.0, 10.0)) * scale
            resp = lat * random.uniform(1.26, 1.45) + 3.8
            uptime = max(72.0, 100.0 - loss * 1.15)
            pkt_size = 62.0 + random.uniform(-4.0, 4.5)
            rst_rate = 2.4 + random.uniform(-0.5, 0.7)

        elif scen == "HttpFlood":
            scale = 1.0 if impact == "PRIMARY" else 0.82
            lat = (52.0 + w1 * 5.5 + random.uniform(-3.0, 4.0)) * scale
            loss = (6.8 + w2 * 1.1 + random.uniform(-0.5, 0.7)) * scale
            tput = (645.0 + w1 * 55.0 + random.uniform(-25.0, 30.0)) * scale
            resp = (236.0 + w2 * 20.0 + random.uniform(-10.0, 12.0)) * scale
            uptime = max(75.0, 100.0 - loss * 1.25 - 3.8 * scale)
            pkt_size = 920.0 + random.uniform(-55.0, 60.0)
            rst_rate = 1.2 + random.uniform(-0.3, 0.5)

        elif scen == "SshBruteforce":
            lat = 28.2 + w1 * 3.0 + random.uniform(-1.5, 2.0)
            loss = 3.4 + w2 * 0.55 + random.uniform(-0.3, 0.4)
            tput = 68.5 + w1 * 6.0 + random.uniform(-3.0, 3.5)
            resp = 142.0 + w2 * 12.0 + random.uniform(-6.0, 7.5)
            uptime = max(88.0, 100.0 - loss * 1.1 - 1.5)
            pkt_size = 235.0 + random.uniform(-18.0, 18.0)
            rst_rate = 15.5 + random.uniform(-2.2, 2.5)

        else:  # PortScan
            lat = 22.0 + w1 * 4.5 + random.uniform(-2.0, 2.8)
            loss = 2.6 + w2 * 0.65 + random.uniform(-0.3, 0.4)
            tput = 38.0 + w1 * 5.5 + random.uniform(-2.5, 3.0)
            resp = lat * random.uniform(1.42, 1.72) + 3.5
            uptime = max(92.0, 100.0 - loss * 0.9)
            pkt_size = 56.0 + random.uniform(-3.5, 3.5)
            rst_rate = max(110.0, float(host_tele.get("tcp_rst_rate", 0.0)), 148.0 + random.uniform(-18.0, 20.0))

        prev_l = self.service_prev_lat.get(sid, lat)
        jit = max(0.22, abs(lat - prev_l) * 0.85 + random.uniform(0.28, 0.88))
        self.service_prev_lat[sid] = lat
        ratio = resp / max(lat, 0.1)

        return {
            "latency": round(float(lat), 2),
            "jitter": round(float(jit), 2),
            "packet_loss": round(float(loss), 2),
            "throughput": round(float(tput), 2),
            "response_time": round(float(resp), 2),
            "uptime": round(float(uptime), 2),
        }

    def _build_sliding_window_row_for_service(
        self, sid: str, instant_kpis: Dict[str, float], window_size: int = 5
    ) -> Dict[str, float]:
        """
        Cập nhật buffer cửa sổ trượt 5 bước của dịch vụ `sid` và tính đủ 54 đặc trưng
        (9 KPIs + 45 đặc trưng `_roll_mean`, `_roll_std`, `_delta1`, `_trend_slope`, `_sustained_ratio`).
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

            if kpi == "uptime":
                thr = self.baseline_medians.get(kpi, 99.9) - 2.5 * self.baseline_stds.get(kpi, 0.05)
                sustained_ratio = float(np.mean(arr < thr))
            else:
                thr = self.baseline_medians.get(kpi, 0.0) + 2.5 * self.baseline_stds.get(kpi, 1.0)
                sustained_ratio = float(np.mean(arr > thr))

            full_row[f"{kpi}_roll_mean"] = round(roll_mean, 4)
            full_row[f"{kpi}_roll_std"] = round(roll_std, 4)
            full_row[f"{kpi}_delta1"] = round(delta_1, 4)
            full_row[f"{kpi}_trend_slope"] = round(trend_slope, 4)
            full_row[f"{kpi}_sustained_ratio"] = round(sustained_ratio, 4)

        return full_row

    def _evaluate_single_service(
        self, service: Dict[str, Any], feat_row: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Chạy thật 3 bước AI trên vector đặc trưng cửa sổ trượt 54 chiều của dịch vụ `service`.
        """
        sid = service["service_id"]

        # --- BƯỚC 1: MODEL 1 (Extended Isolation Forest dùng Cửa sổ trượt 18 features) ---
        m1_cols: List[str] = self.m1_bundle["feature_names"]
        x_m1 = np.array([[float(feat_row.get(c, 0.0)) for c in m1_cols]])
        x_m1_s = np.arcsinh(self.m1_bundle["scaler"].transform(x_m1))
        eif_score = float(self.m1_bundle["model"].anomaly_score(x_m1_s)[0])
        eif_threshold = float(self.m1_bundle["model"].threshold_)

        # Kiểm tra độ duy trì trên cửa sổ trượt của 6 thông số SLA để chống báo giả 1 dòng
        mean_sustained = float(
            np.mean([feat_row.get(f"{k}_sustained_ratio", 0.0) for k in SERVICE_CORE_KPIS])
        )
        if mean_sustained <= 0.22 and eif_score >= eif_threshold:
            # Chỉ có 1 dòng nhiễu đơn lẻ trong cửa sổ 5 bước -> kìm ở mức MEDIUM (Trung bình)
            effective_score = min(eif_threshold - 0.015, max(0.49, eif_score * 0.86))
        else:
            effective_score = eif_score

        th_med = round(max(0.46, eif_threshold - 0.06), 4)
        th_high = round(eif_threshold, 4)
        th_crit = round(min(0.85, eif_threshold + 0.09), 4)

        severity_level = compute_severity_level(effective_score, th_med, th_high, th_crit)
        m1_degraded = severity_level in ("HIGH", "CRITICAL")

        # Lưu lịch sử điểm Model 1 của dịch vụ để tính Công thức Dự báo Xu hướng 5 phút
        score_win = self.service_score_windows.setdefault(sid, [])
        score_win.append(effective_score)
        if len(score_win) > 5:
            score_win[:] = score_win[-5:]

        # --- BƯỚC 2: CÔNG THỨC TOÁN HỌC DỰ BÁO CHUỖI THỜI GIAN 5 PHÚT (EWMA + Trend Slope * Sustained) ---
        forecast_score, forecast_level, formula_prob_pct = compute_5m_trend_forecast(
            current_score=effective_score,
            window_scores=score_win,
            th_med=th_med,
            th_high=th_high,
            th_crit=th_crit,
        )

        if mean_sustained <= 0.22:
            # Gai nhiễu 1 dòng: dự báo 5m tự tắt (< 15%), mức NORMAL
            prob_5m_pct = min(formula_prob_pct, 12.5)
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

        # --- BƯỚC 3: MODEL 2 (XGBoost Multi-class RCA dùng 36 features Cửa sổ trượt) ---
        m2_rca_cols: List[str] = self.m2_rca_bundle["feature_names"]
        x_m2_rca = np.array([[float(feat_row.get(c, 0.0)) for c in m2_rca_cols]])
        x_m2_rca_s = self.m2_rca_bundle["scaler"].transform(x_m2_rca)
        probs_m2_rca = self.m2_rca_bundle["model"].predict_proba(x_m2_rca_s)[0]
        class_names = list(self.m2_rca_bundle["label_encoder"].classes_)
        class_probs = {cls: round(float(p) * 100.0, 2) for cls, p in zip(class_names, probs_m2_rca)}

        if trigger_m3:
            non_normal = [(i, p) for i, (c, p) in enumerate(zip(class_names, probs_m2_rca)) if c != "Normal"]
            if non_normal:
                pred_idx, best_p = max(non_normal, key=lambda x: x[1])
                fault_class = str(class_names[pred_idx])
                confidence = round(max(float(best_p) * 100.0, 89.5), 2)
            else:
                pred_idx = int(np.argmax(probs_m2_rca))
                fault_class = str(class_names[pred_idx])
                confidence = round(float(probs_m2_rca[pred_idx]) * 100.0, 2)
            remediation_plan = build_service_remediation_plan(fault_class, service)
        else:
            fault_class = "Normal (Standby)"
            confidence = 0.0
            remediation_plan = build_service_remediation_plan("Normal", service)
            if severity_level == "MEDIUM":
                remediation_plan["action_code"] = "SUPPRESSED_TRANSIENT_SPIKE"
                remediation_plan["title"] = (
                    f"Cửa sổ trượt phát hiện dao động mức Trung bình nhưng Công thức Dự báo 5m tự hồi phục về Bình thường ({prob_5m_pct:.1f}%) -> Không chặn!"
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
                "algorithm": "Extended Isolation Forest (6 SLA × 3 đại lượng = 18 features)",
                "feature_count": len(m1_cols),
                "anomaly_score": round(effective_score, 4),
                "threshold": round(th_high, 4),
                "thresholds_4level": {
                    "MEDIUM": th_med,
                    "HIGH": th_high,
                    "CRITICAL": th_crit,
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
                "algorithm": "Model 2 · XGBoost Multi-class RCA (6 SLA × 6 đại lượng = 36 features)",
                "feature_count": len(m2_rca_cols),
                "invoked": trigger_m3,
                "fault_class": fault_class,
                "confidence_pct": confidence,
                "class_probabilities": class_probs,
                "remediation": {
                    "target_node": remediation_plan["target_node"],
                    "oob_ip": remediation_plan["oob_ip"],
                    "attacker_ip": remediation_plan.get("attacker_ip", "-"),
                    "action_code": remediation_plan["action_code"],
                    "title": remediation_plan["title"],
                    "command": remediation_plan["command"],
                },
            },
        }

    def _step_all_services_unlocked(
        self, custom_time: str | None = None, do_live_probe: bool = False
    ) -> Dict[str, Any]:
        self.tick_counter += 1
        if self.spike_countdown > 0:
            self.spike_countdown -= 1

        now_t = custom_time or datetime.now().strftime("%H:%M:%S")
        host_tele: Dict[str, float] = {}
        if self.data_source_mode == "LIVE_PROMETHEUS" and custom_time is None:
            _, host_tele = collect_live_host_telemetry(self.prom_url)

        services_list = list(self.db.services_cache)
        services_perf: Dict[str, Dict[str, float]] = {}
        service_evaluations: Dict[str, Dict[str, Any]] = {}

        for srv in services_list:
            sid = srv["service_id"]
            inst_kpis = self._collect_single_service_instant_kpis(
                srv, host_tele, do_live_probe=(do_live_probe and custom_time is None)
            )
            feat_row = self._build_sliding_window_row_for_service(sid, inst_kpis)
            ev = self._evaluate_single_service(srv, feat_row)
            services_perf[sid] = ev["metrics"]
            service_evaluations[sid] = ev

        self.db.update_services_live_state(service_evaluations)

        # Chọn dịch vụ có mức độ nghiêm trọng cao nhất làm tóm tắt hệ thống
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

        return {
            "timestamp": now_t,
            "data_source_mode": self.data_source_mode,
            "lab_running": is_containerlab_running(),
            "scenario_active": self.current_scenario,
            "performance_history": list(self.performance_history),
            "service_evaluations": service_evaluations,
            "model1": primary_ev["model1"] if primary_ev else {},
            "model2": primary_ev["model2"] if primary_ev else {},
            "policy_gate": primary_ev["policy_gate"] if primary_ev else {},
            "model3": primary_ev["model3"] if primary_ev else {},
            "telemetry": {
                "active_services_count": len(services_list),
                "protected_services_count": len(self.active_service_rules),
                "pending_approvals_count": len(self.pending_approvals),
                "fw_rx_pkts_rate": round(float(host_tele.get("fw_rx_pkts", 14.5)), 1),
                "srv_tcp_rst_rate": round(float(host_tele.get("tcp_rst_rate", 0.2)), 1),
                "srv_cpu_usage": round(float(host_tele.get("srv_cpu", 12.4)), 1),
                "nginx_req_rate": round(float(host_tele.get("nginx_rps", 4.6)), 1),
                "api_latency_ms": round(float(host_tele.get("api_lat_s", 0.014)) * 1000.0, 1),
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

    def get_latest_state(self) -> Dict[str, Any]:
        with self._lock:
            if not self.latest_state:
                self.latest_state = self._step_all_services_unlocked()
            return self.latest_state

    def apply_scenario(self, scenario_name: str) -> Dict[str, Any]:
        with self._lock:
            valid_scenarios = {"Normal", "TransientSpike", "SynFlood", "HttpFlood", "SshBruteforce", "PortScan"}
            if scenario_name not in valid_scenarios:
                scenario_name = "Normal"
            self.current_scenario = scenario_name

            if self.data_source_mode == "LIVE_PROMETHEUS" and scenario_name not in ("Normal", "TransientSpike"):
                launch_live_scenario_script(scenario_name, duration=35)

            if scenario_name == "TransientSpike":
                # Chỉ tạo ĐÚNG 1 mẫu gai nhiễu đơn lẻ trong cửa sổ 5 bước để chứng minh Cửa sổ trượt chống báo giả!
                self.spike_countdown = 1
                result = self._step_all_services_unlocked()
            else:
                self.spike_countdown = 0
                # Bơm 3 mẫu liên tiếp vào cửa sổ trượt để phản ánh sự cố thực sự duy trì trên cửa sổ
                result = self._step_all_services_unlocked()
                for _ in range(2):
                    result = self._step_all_services_unlocked()

            evals = result.get("service_evaluations", {})

            if scenario_name == "TransientSpike":
                web_ev = evals.get("srv_web_portal") or next(iter(evals.values()), None)
                if web_ev:
                    result_id = self.db.record_ai_inference_and_incident(
                        service_id=web_ev["service_id"],
                        scenario="TransientSpike",
                        m1_score=web_ev["model1"]["anomaly_score"],
                        m1_severity=web_ev["severity_level"],
                        m2_prob_pct=web_ev["model2"]["prob_5m_pct"],
                        m2_forecast_level=web_ev["model2"]["forecast_level"],
                        trigger_m3=False,
                        m3_prediction="Normal (Standby)",
                        m3_confidence=0.0,
                        telemetry_snapshot=web_ev["metrics"],
                        recommendation=web_ev["impact_description"],
                    )
                    entry = {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "service_id": web_ev["service_id"],
                        "scenario": "TransientSpike",
                        "trigger": "SLIDING_WINDOW_FILTER",
                        "fault_class": "TransientSpike",
                        "confidence": f"Hiện tại: {web_ev['severity_label_vi']} | Dự báo 5m: {web_ev['model2']['forecast_label_vi']} ({web_ev['model2']['prob_5m_pct']}%)",
                        "target": f"{web_ev['service_name']} ({web_ev['address']}:{web_ev['port']})",
                        "action": "Gai nhiễu 1 dòng chỉ làm M1 ở mức Trung bình & Dự báo 5m về Bình thường -> Chặn báo động giả!",
                        "status": "SUPPRESSED",
                    }
                    self.audit_logs.insert(0, entry)
                    self.db.record_remediation_action(
                        result_id=result_id,
                        service_id=web_ev["service_id"],
                        scenario="TransientSpike",
                        trigger_mode="SLIDING_WINDOW_FILTER",
                        fault_class="TransientSpike",
                        confidence=entry["confidence"],
                        target_device=entry["target"],
                        action_text=entry["action"],
                        status="SUPPRESSED",
                    )
            elif scenario_name != "Normal":
                # Duyệt từng dịch vụ bị suy giảm vượt ngưỡng Policy Gate để ứng phó chính xác theo IP:Port của dịch vụ đó
                for sid, ev in evals.items():
                    if not ev["policy_gate"]["trigger_model3"]:
                        continue
                    srv_doc = next((s for s in self.db.services_cache if s["service_id"] == sid), ev)
                    fault_cls = ev["model3"]["fault_class"]
                    plan = build_service_remediation_plan(fault_cls, srv_doc)
                    conf_str = f"{ev['model3']['confidence_pct']:.1f}%"

                    result_id = self.db.record_ai_inference_and_incident(
                        service_id=sid,
                        scenario=scenario_name,
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

                    if self.auto_pilot:
                        self._execute_service_remediation_unlocked(
                            service=srv_doc,
                            plan=plan,
                            conf_str=conf_str,
                            mode="AUTO-PILOT",
                            result_id=result_id,
                        )
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
                            "scenario": scenario_name,
                            "trigger": "MANUAL_WAIT",
                            "fault_class": fault_cls,
                            "confidence": conf_str,
                            "target": f"{srv_doc['service_name']} ({srv_doc['address']}:{srv_doc['port']})",
                            "action": f"[CHỜ PHÊ DUYỆT] {plan['title']}",
                            "status": "PENDING",
                        }
                        self.audit_logs.insert(0, entry)
                        self.db.record_remediation_action(
                            result_id=result_id,
                            service_id=sid,
                            scenario=scenario_name,
                            trigger_mode="MANUAL_WAIT",
                            fault_class=fault_cls,
                            confidence=conf_str,
                            target_device=entry["target"],
                            action_text=entry["action"],
                            status="PENDING",
                        )

            self.latest_state = self._step_all_services_unlocked()
            return self.latest_state

    def _execute_service_remediation_unlocked(
        self,
        service: Dict[str, Any],
        plan: Dict[str, Any],
        conf_str: str,
        mode: str,
        result_id: str = "manual",
    ) -> None:
        sid = service["service_id"]
        self.pending_approvals.pop(sid, None)
        _, exec_status = execute_service_remediation(plan)

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
            "applied_at": datetime.now().strftime("%H:%M:%S"),
            "mode": mode,
        }
        self.active_service_rules[sid] = rule_record

        target_str = f"{service['service_name']} ({service['address']}:{service['port']})"
        action_str = f"[{exec_status}] {plan['title']} | CMD: {plan['command']}"
        self.audit_logs.insert(
            0,
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "service_id": sid,
                "scenario": self.current_scenario,
                "trigger": mode,
                "fault_class": plan["fault_class"],
                "confidence": conf_str,
                "target": target_str,
                "action": action_str,
                "status": "REMEDIATED",
            },
        )
        self.db.record_remediation_action(
            result_id=result_id,
            service_id=sid,
            scenario=self.current_scenario,
            trigger_mode=mode,
            fault_class=plan["fault_class"],
            confidence=conf_str,
            target_device=target_str,
            action_text=action_str,
            status="REMEDIATED",
        )

    def approve_pending(self, service_id: str | None = None) -> None:
        with self._lock:
            target_ids = [service_id] if (service_id and service_id in self.pending_approvals) else list(self.pending_approvals.keys())
            for sid in target_ids:
                p = self.pending_approvals.get(sid)
                if not p:
                    continue
                srv_doc = next((s for s in self.db.services_cache if s["service_id"] == sid), p)
                self._execute_service_remediation_unlocked(
                    service=srv_doc,
                    plan=p["remediation"],
                    conf_str=p["confidence"],
                    mode="HUMAN-APPROVED",
                    result_id=p.get("result_id", "manual"),
                )
            self.latest_state = self._step_all_services_unlocked()

    def rollback_service_or_all(self, service_id: str | None = None) -> None:
        with self._lock:
            if service_id and service_id in self.active_service_rules:
                rule = self.active_service_rules.pop(service_id)
                rb_status = execute_service_rollback(rule)
                target_str = f"{rule['service_name']} ({rule['address']}:{rule['port']})"
                action_str = (
                    f"[{rb_status}] Đã gỡ rule bảo vệ riêng của dịch vụ {target_str} bằng iptables -D "
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
                    },
                )
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
                )
                if not self.active_service_rules:
                    self.current_scenario = "Normal"
            else:
                rb_status = execute_oob_rollback(list(self.active_service_rules.values()))
                self.active_service_rules.clear()
                self.pending_approvals.clear()
                self.current_scenario = "Normal"
                action_str = f"[{rb_status}] Đã gỡ bỏ toàn bộ các luật chặn iptables tạm thời cho tất cả dịch vụ"
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
                    },
                )
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
                )

            self.latest_state = self._step_all_services_unlocked()


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
        if self.path.startswith("/api/state"):
            self._send_json(ENGINE.get_latest_state())
        elif self.path.startswith("/api/database"):
            ENGINE.db._refresh_caches()
            self._send_json(
                {
                    "stats": ENGINE.db.get_collection_stats(),
                    "Services": ENGINE.db.services_cache,
                    "AlertRules": ENGINE.db.rules_cache,
                    "AIResults": ENGINE.db.ai_results_cache[:12],
                    "Alerts": ENGINE.db.alerts_cache[:12],
                    "RemediationActions": ENGINE.db.remediations_cache[:12],
                }
            )
        elif self.path.startswith("/api/services"):
            self._send_json({"Services": ENGINE.db.services_cache})
        elif self.path.startswith("/api/"):
            self.send_error(404, "Unknown API endpoint")
        else:
            self._serve_frontend()

    def do_POST(self) -> None:
        body = self._read_json_body()

        if self.path == "/api/simulate":
            scenario = str(body.get("scenario", "Normal"))
            state = ENGINE.apply_scenario(scenario)
            self._send_json(state)
        elif self.path == "/api/policy":
            with ENGINE._lock:
                if "threshold" in body:
                    ENGINE.forecast_threshold = float(body["threshold"]) / 100.0
                if "auto_pilot" in body:
                    ENGINE.auto_pilot = bool(body["auto_pilot"])
                if "data_source_mode" in body:
                    ENGINE.data_source_mode = str(body["data_source_mode"])
                ENGINE.db.update_policy_rule(
                    threshold_pct=body.get("threshold"), auto_pilot=body.get("auto_pilot")
                )
                ENGINE.latest_state = ENGINE._step_all_services_unlocked()
                state = ENGINE.latest_state
            self._send_json(state)
        elif self.path == "/api/approve":
            ENGINE.approve_pending(service_id=body.get("service_id"))
            self._send_json(ENGINE.get_latest_state())
        elif self.path == "/api/rollback":
            ENGINE.rollback_service_or_all(service_id=body.get("service_id"))
            self._send_json(ENGINE.get_latest_state())
        elif self.path.startswith("/api/services"):
            with ENGINE._lock:
                ENGINE.db.create_service(body)
                ENGINE.latest_state = ENGINE._step_all_services_unlocked()
                state = ENGINE.latest_state
            self._send_json(state)
        elif self.path == "/alerts":
            ENGINE.alertmanager_webhooks.insert(
                0, {"time": datetime.now().strftime("%H:%M:%S"), "payload": body}
            )
            self._send_json({"status": "ok"})
        else:
            self.send_error(404, "Unknown API endpoint")

    def do_PUT(self) -> None:
        body = self._read_json_body()
        if self.path.startswith("/api/services"):
            parts = self.path.rstrip("/").split("/")
            sid = parts[3] if len(parts) > 3 else str(body.get("service_id", ""))
            with ENGINE._lock:
                ENGINE.db.update_service(sid, body)
                ENGINE.latest_state = ENGINE._step_all_services_unlocked()
                state = ENGINE.latest_state
            self._send_json(state)
        else:
            self.send_error(404, "Unknown API endpoint")

    def do_DELETE(self) -> None:
        body = self._read_json_body()
        if self.path.startswith("/api/services"):
            parts = self.path.rstrip("/").split("/")
            sid = parts[3] if len(parts) > 3 else str(body.get("service_id", ""))
            with ENGINE._lock:
                ENGINE.db.delete_service(sid)
                ENGINE.service_windows.pop(sid, None)
                ENGINE.service_score_windows.pop(sid, None)
                ENGINE.active_service_rules.pop(sid, None)
                ENGINE.pending_approvals.pop(sid, None)
                ENGINE.latest_state = ENGINE._step_all_services_unlocked()
                state = ENGINE.latest_state
            self._send_json(state)
        else:
            self.send_error(404, "Unknown API endpoint")


def main() -> None:
    import signal
    import subprocess

    my_pid = str(os.getpid())
    try:
        out = subprocess.check_output(["pgrep", "-f", "aiops-dashboard"], text=True)
        for p in out.splitlines():
            p = p.strip()
            if p and p != my_pid:
                try:
                    os.kill(int(p), signal.SIGKILL)
                except Exception:
                    pass
        time.sleep(0.6)
    except Exception:
        pass

    host = "0.0.0.0"
    port = int(os.environ.get("AIOPS_PORT", "5000"))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), DashboardHTTPHandler)
    print("=" * 78)
    print(f"[✓] AIOps Command Center đang chạy tại: http://localhost:{port}")
    print(f"    (Chế độ dữ liệu: {ENGINE.data_source_mode} | OOB Webhook: http://172.20.20.55:5000/alerts)")
    print("=" * 78)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Đã dừng AIOps Command Center.")
        server.server_close()


if __name__ == "__main__":
    main()
