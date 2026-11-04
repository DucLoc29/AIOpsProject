#!/usr/bin/env python3
"""
AIOps Command Center Server (172.20.20.55:5000 / localhost:5000).
Trung tâm Chỉ huy Giám sát, Dự báo Suy giảm & Tự động Ứng phó Sự cố Mạng (Giai đoạn 5).

Tính năng cốt lõi:
  1. Nạp trực tiếp 3 Model AI thực tế từ `ai/models/`:
     - Model 1 (`model1_eif_detector.pkl`): Extended Isolation Forest (14 KPIs tức thời)
     - Model 2 (`model2_xgboost_forecaster.pkl`): XGBoost 5m Forecaster (84 đặc trưng cửa sổ trượt)
     - Model 3 (`model3_rca_classifier.pkl`): XGBoost Multi-class RCA Classifier (42 đặc trưng đa tầng)
  2. Hệ thống Điều phối Chính sách (System Policy Engine):
     - Cho phép chỉnh ngưỡng dự báo 5 phút (`forecast_threshold`, mặc định 60%)
     - Chuyển đổi chế độ `Auto-Pilot` (Tự động khắc phục) hoặc `Human-in-the-Loop` (Chờ phê duyệt)
  3. Hỗ trợ 2 nguồn dữ liệu trực tiếp trên giao diện:
     - Chế độ Mô phỏng Kịch bản (Interactive Scenario Replay từ `aiops_network_dataset.csv`) để xem trước
       cách 3 Model phản ứng với 8 tình huống (`Normal`, `TransientSpike`, `WanCongestion`, `WanLossLatency`,
       `SynFlood`, `PortScan`, `SshBruteforce`, `HttpFlood`).
     - Nhận Webhook từ Alertmanager (`/alerts`).
"""

import json
import mimetypes
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "ai" / "collector"))
sys.path.insert(0, str(PROJECT_ROOT / "ai"))

import eif_model  # noqa: F401, E402

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"
MODELS_DIR = PROJECT_ROOT / "ai" / "models"
DATASET_PATH = PROJECT_ROOT / "ai" / "dataset" / "aiops_network_dataset.csv"

REMEDIATION_COMMANDS = {
    "Normal": {
        "target_node": "N/A",
        "oob_ip": "-",
        "action_code": "MONITOR_ONLY",
        "title": "Hệ thống hoạt động bình thường",
        "command": "# Không cần can thiệp",
    },
    "WanCongestion": {
        "target_node": "core-router & branch-router",
        "oob_ip": "172.20.20.11 / .12",
        "action_code": "REROUTE_OSPF_WAN2",
        "title": "Đổi OSPF Cost WAN1 (10 -> 100), lái lưu lượng sang WAN2 Backup",
        "command": "docker exec clab-aiops-prod-branch-router vtysh -c 'conf t' -c 'interface eth2' -c 'ip ospf cost 100'",
    },
    "WanLossLatency": {
        "target_node": "core-router & branch-router",
        "oob_ip": "172.20.20.11 / .12",
        "action_code": "FAILOVER_GRAY_FAILURE_WAN2",
        "title": "Suy giảm mềm WAN1 (Loss 20%, Delay 200ms) -> Chuyển định tuyến sang WAN2",
        "command": "docker exec clab-aiops-prod-branch-router vtysh -c 'conf t' -c 'interface eth2' -c 'ip ospf cost 100'",
    },
    "SynFlood": {
        "target_node": "firewall",
        "oob_ip": "172.20.20.13",
        "action_code": "FIREWALL_DROP_ATTACKER",
        "title": "Chặn IP tấn công SYN Flood từ Internet tại Firewall",
        "command": "docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 -s 203.0.113.10 -p tcp --syn -j DROP",
    },
    "PortScan": {
        "target_node": "service-server",
        "oob_ip": "172.20.20.21",
        "action_code": "ISOLATE_INSIDER_LAN",
        "title": "Cô lập máy nội bộ LAN (Insider Threat) quét cổng trực tiếp tại Service Server",
        "command": "docker exec clab-aiops-prod-service-server iptables -I INPUT 1 -s 192.168.10.30 -j DROP",
    },
    "SshBruteforce": {
        "target_node": "firewall",
        "oob_ip": "172.20.20.13",
        "action_code": "BLOCK_SSH_BRUTEFORCE",
        "title": "Chặn kết nối dò mật khẩu SSH (Port 22) từ Internet Attacker tại Firewall",
        "command": "docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 -s 203.0.113.10 -p tcp --dport 22 -j DROP",
    },
    "HttpFlood": {
        "target_node": "firewall & service-server",
        "oob_ip": "172.20.20.13 / .21",
        "action_code": "RATE_LIMIT_HTTP_FLOOD",
        "title": "Áp dụng bộ lọc giới hạn tốc độ HTTP (iptables hashlimit) & chặn IP Flood",
        "command": "docker exec clab-aiops-prod-firewall iptables -I FORWARD 1 -p tcp --dport 80 -m limit --limit 50/s -j ACCEPT",
    },
}


class AIOpsStateEngine:
    def __init__(self) -> None:
        print("[*] Đang nạp 3 Model AI từ ai/models/ ...")
        self.m1_bundle = joblib.load(MODELS_DIR / "model1_eif_detector.pkl")
        self.m2_bundle = joblib.load(MODELS_DIR / "model2_xgboost_forecaster.pkl")
        self.m3_bundle = joblib.load(MODELS_DIR / "model3_rca_classifier.pkl")

        self.df = pd.read_csv(DATASET_PATH).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        self.scenario_samples: Dict[str, List[Dict[str, Any]]] = {}
        self._index_scenarios()

        self.current_scenario = "Normal"
        self.sample_cursor = 0
        self.forecast_threshold = 0.60
        self.auto_pilot = True
        self.wan_active_path = "WAN1 (Primary - Cost 10)"
        self.blocked_ips: List[str] = []
        self.pending_approval: Dict[str, Any] | None = None
        self.audit_logs: List[Dict[str, Any]] = [
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "scenario": "SystemInit",
                "trigger": "SYSTEM",
                "fault_class": "Normal",
                "confidence": "100%",
                "target": "aiops-server (172.20.20.55)",
                "action": "Đã nạp thành công Model 1 (EIF), Model 2 (XGBoost 5m), Model 3 (XGBoost RCA)",
                "status": "READY",
            }
        ]
        self.alertmanager_webhooks: List[Dict[str, Any]] = []

    def _index_scenarios(self) -> None:
        # 1. Gai nhiễu thoáng qua (TransientSpike)
        spikes = self.df[(self.df["label"] == "Normal") & (self.df["current_degraded"] == 1)]
        self.scenario_samples["TransientSpike"] = spikes.to_dict(orient="records")

        # 2. 7 kịch bản chuẩn
        for label_name, group in self.df.groupby("label"):
            if label_name == "Normal":
                clean = group[group["current_degraded"] == 0]
                self.scenario_samples["Normal"] = clean.to_dict(orient="records")
            else:
                active = group[group["current_degraded"] == 1]
                self.scenario_samples[str(label_name)] = (
                    active.to_dict(orient="records")
                    if not active.empty
                    else group.to_dict(orient="records")
                )

    def get_current_row(self) -> Dict[str, Any]:
        rows = self.scenario_samples.get(self.current_scenario, self.scenario_samples["Normal"])
        if not rows:
            return self.df.iloc[0].to_dict()
        idx = self.sample_cursor % len(rows)
        self.sample_cursor += 1
        return rows[idx]

    def run_three_stage_inference(self, row: Dict[str, Any]) -> Dict[str, Any]:
        # --- TẦNG 1: MODEL 1 (Extended Isolation Forest - 14 features) ---
        m1_cols: List[str] = self.m1_bundle["feature_names"]
        x_m1 = np.array([[float(row.get(c, 0.0)) for c in m1_cols]])
        x_m1_s = np.arcsinh(self.m1_bundle["scaler"].transform(x_m1))
        eif_score = float(self.m1_bundle["model"].anomaly_score(x_m1_s)[0])
        eif_threshold = float(self.m1_bundle["model"].threshold_)
        m1_degraded = bool(self.m1_bundle["model"].predict(x_m1_s)[0] == 1)

        # --- TẦNG 2: MODEL 2 (XGBoost 5m Forecaster - 84 features) ---
        m2_cols: List[str] = self.m2_bundle["feature_names"]
        x_m2 = np.array([[float(row.get(c, 0.0)) for c in m2_cols]])
        x_m2_s = self.m2_bundle["scaler"].transform(x_m2)
        prob_5m = float(self.m2_bundle["model"].predict_proba(x_m2_s)[0][1])

        # --- HỆ THỐNG ĐIỀU PHỐI (Policy Gate) ---
        trigger_m3 = m1_degraded and (prob_5m >= self.forecast_threshold)

        # --- TẦNG 3: MODEL 3 (XGBoost Multi-class RCA Classifier - 42 features) ---
        m3_cols: List[str] = self.m3_bundle["feature_names"]
        x_m3 = np.array([[float(row.get(c, 0.0)) for c in m3_cols]])
        x_m3_s = self.m3_bundle["scaler"].transform(x_m3)
        probs_m3 = self.m3_bundle["model"].predict_proba(x_m3_s)[0]
        class_names = list(self.m3_bundle["label_encoder"].classes_)
        class_probs = {
            cls: round(float(p) * 100.0, 2) for cls, p in zip(class_names, probs_m3)
        }

        if trigger_m3:
            pred_idx = int(np.argmax(probs_m3))
            fault_class = str(class_names[pred_idx])
            confidence = round(float(probs_m3[pred_idx]) * 100.0, 2)
            remediation_plan = REMEDIATION_COMMANDS.get(
                fault_class, REMEDIATION_COMMANDS["Normal"]
            )
        else:
            fault_class = "Normal (Standby)"
            confidence = 0.0
            remediation_plan = {
                "target_node": "N/A",
                "oob_ip": "-",
                "action_code": "SKIPPED_BY_POLICY_GATE",
                "title": (
                    "Đã chặn báo động giả (Gai nhiễu thoáng qua - Không kéo dài 5 phút)"
                    if m1_degraded
                    else "Mạng ổn định - Model 3 ở chế độ nghỉ (Standby)"
                ),
                "command": "# Không kích hoạt Model 3",
            }

        return {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "scenario_active": self.current_scenario,
            "model1": {
                "algorithm": "Extended Isolation Forest (EIF)",
                "feature_count": len(m1_cols),
                "anomaly_score": round(eif_score, 4),
                "threshold": round(eif_threshold, 4),
                "is_degraded": m1_degraded,
            },
            "model2": {
                "algorithm": "XGBoost 5m Forecaster",
                "feature_count": len(m2_cols),
                "prob_5m_pct": round(prob_5m * 100.0, 2),
                "threshold_pct": round(self.forecast_threshold * 100.0, 0),
                "is_sustained": prob_5m >= self.forecast_threshold,
            },
            "policy_gate": {
                "trigger_model3": trigger_m3,
                "auto_pilot": self.auto_pilot,
                "reason": (
                    "KÍCH HOẠT MODEL 3 (Bất thường tức thời + Dự báo 5m vượt ngưỡng)"
                    if trigger_m3
                    else (
                        "CHẶN BÁO ĐỘNG GIẢ (Model 1 báo bất thường nhưng Dự báo 5m thấp)"
                        if m1_degraded
                        else "BÌNH THƯỜNG (Model 1 không phát hiện suy giảm)"
                    )
                ),
            },
            "model3": {
                "algorithm": "XGBoost Multi-class RCA",
                "feature_count": len(m3_cols),
                "invoked": trigger_m3,
                "fault_class": fault_class,
                "confidence_pct": confidence,
                "class_probabilities": class_probs,
                "remediation": remediation_plan,
            },
            "telemetry": {
                "wan1_rx_mbps": round(float(row.get("wan1_rx_bytes_rate", 0.0)) * 8 / 1e6, 2),
                "wan1_tx_mbps": round(float(row.get("wan1_tx_bytes_rate", 0.0)) * 8 / 1e6, 2),
                "wan2_rx_mbps": round(float(row.get("wan2_rx_bytes_rate", 0.0)) * 8 / 1e6, 2),
                "wan_retrans_rate": round(float(row.get("branch_tcp_retrans_rate", 0.0)), 2),
                "wan_syn_retrans": round(float(row.get("wan_tcp_syn_retrans_rate", 0.0)), 2),
                "fw_rx_pkts_rate": round(float(row.get("fw_rx_pkts_rate", 0.0)), 1),
                "client_lan_tx_pkts": round(float(row.get("client_lan_tx_pkts_rate", 0.0)), 1),
                "srv_tcp_rst_rate": round(float(row.get("srv_tcp_out_rsts_rate", 0.0)), 1),
                "srv_cpu_usage": round(float(row.get("srv_cpu_usage", 0.0)), 1),
                "nginx_req_rate": round(float(row.get("nginx_req_rate", 0.0)), 1),
                "api_latency_ms": round(float(row.get("api_latency_avg", 0.0)) * 1000.0, 1),
            },
            "network_state": {
                "wan_active_path": self.wan_active_path,
                "blocked_ips": self.blocked_ips,
                "pending_approval": self.pending_approval,
            },
            "audit_logs": self.audit_logs[:12],
        }

    def apply_scenario(self, scenario_name: str) -> Dict[str, Any]:
        if scenario_name not in self.scenario_samples:
            scenario_name = "Normal"
        self.current_scenario = scenario_name
        row = self.get_current_row()
        result = self.run_three_stage_inference(row)

        if result["policy_gate"]["trigger_model3"]:
            fault_cls = result["model3"]["fault_class"]
            rem = result["model3"]["remediation"]
            conf_str = f"{result['model3']['confidence_pct']:.1f}%"

            if self.auto_pilot:
                self._execute_remediation(fault_cls, rem, conf_str, mode="AUTO-PILOT")
            else:
                self.pending_approval = {
                    "fault_class": fault_cls,
                    "confidence": conf_str,
                    "remediation": rem,
                }
                self.audit_logs.insert(
                    0,
                    {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "scenario": scenario_name,
                        "trigger": "MANUAL_WAIT",
                        "fault_class": fault_cls,
                        "confidence": conf_str,
                        "target": rem["target_node"],
                        "action": f"[CHỜ PHÊ DUYỆT] {rem['title']}",
                        "status": "PENDING",
                    },
                )
        elif scenario_name == "TransientSpike":
            self.audit_logs.insert(
                0,
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "scenario": "TransientSpike",
                    "trigger": "FILTERED",
                    "fault_class": "TransientSpike",
                    "confidence": f"XGB 5m: {result['model2']['prob_5m_pct']}%",
                    "target": "Policy Gate",
                    "action": "Model 1 phát hiện gai bất thường nhưng Model 2 dự báo tự tắt (<60%) -> Chặn báo động giả!",
                    "status": "SUPPRESSED",
                },
            )

        return self.run_three_stage_inference(row)

    def _execute_remediation(
        self, fault_cls: str, rem: Dict[str, str], conf_str: str, mode: str
    ) -> None:
        self.pending_approval = None
        if fault_cls in ("WanCongestion", "WanLossLatency"):
            self.wan_active_path = "WAN2 (Backup - OSPF Failover Active)"
        elif fault_cls in ("SynFlood", "SshBruteforce", "HttpFlood"):
            if "203.0.113.10" not in self.blocked_ips:
                self.blocked_ips.append("203.0.113.10 (Internet Attacker)")
        elif fault_cls == "PortScan":
            if "192.168.10.30" not in self.blocked_ips:
                self.blocked_ips.append("192.168.10.30 (Insider Client-PC)")

        self.audit_logs.insert(
            0,
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "scenario": self.current_scenario,
                "trigger": mode,
                "fault_class": fault_cls,
                "confidence": conf_str,
                "target": f"{rem['target_node']} ({rem['oob_ip']})",
                "action": f"{rem['title']} | CMD: {rem['command']}",
                "status": "REMEDIATED",
            },
        )

    def approve_pending(self) -> None:
        if self.pending_approval:
            p = self.pending_approval
            self._execute_remediation(
                p["fault_class"], p["remediation"], p["confidence"], mode="HUMAN-APPROVED"
            )

    def rollback_all(self) -> None:
        self.wan_active_path = "WAN1 (Primary - Cost 10)"
        self.blocked_ips.clear()
        self.pending_approval = None
        self.current_scenario = "Normal"
        self.audit_logs.insert(
            0,
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "scenario": "Rollback",
                "trigger": "ADMIN_RESET",
                "fault_class": "Normal",
                "confidence": "100%",
                "target": "All Routers & Firewalls",
                "action": "Khôi phục OSPF Cost WAN1 = 10 & gỡ bỏ các luật chặn iptables tạm thời",
                "status": "ROLLED_BACK",
            },
        )


ENGINE = AIOpsStateEngine()


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Giữ log gọn gàng
        pass

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_file(TEMPLATES_DIR / "index.html")
        elif self.path.startswith("/static/"):
            rel = self.path[len("/static/") :]
            self._send_file(STATIC_DIR / rel)
        elif self.path.startswith("/api/state"):
            row = ENGINE.get_current_row()
            self._send_json(ENGINE.run_three_stage_inference(row))
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if self.path == "/api/simulate":
            scenario = str(body.get("scenario", "Normal"))
            state = ENGINE.apply_scenario(scenario)
            self._send_json(state)
        elif self.path == "/api/policy":
            if "threshold" in body:
                ENGINE.forecast_threshold = float(body["threshold"]) / 100.0
            if "auto_pilot" in body:
                ENGINE.auto_pilot = bool(body["auto_pilot"])
            row = ENGINE.get_current_row()
            self._send_json(ENGINE.run_three_stage_inference(row))
        elif self.path == "/api/approve":
            ENGINE.approve_pending()
            row = ENGINE.get_current_row()
            self._send_json(ENGINE.run_three_stage_inference(row))
        elif self.path == "/api/rollback":
            ENGINE.rollback_all()
            row = ENGINE.get_current_row()
            self._send_json(ENGINE.run_three_stage_inference(row))
        elif self.path == "/alerts":
            # Nhận Webhook từ Alertmanager (172.20.20.52:9093)
            ENGINE.alertmanager_webhooks.insert(
                0, {"time": datetime.now().strftime("%H:%M:%S"), "payload": body}
            )
            self._send_json({"status": "ok"})
        else:
            self.send_error(404, "Unknown API endpoint")


def main() -> None:
    host = "0.0.0.0"
    port = 5000
    server = ThreadingHTTPServer((host, port), DashboardHTTPHandler)
    print("=" * 78)
    print(f"[✓] AIOps Command Center đang chạy tại: http://localhost:{port}")
    print("    (Tương thích OOB Webhook: http://172.20.20.55:5000/alerts)")
    print("=" * 78)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Đã dừng AIOps Command Center.")
        server.server_close()


if __name__ == "__main__":
    main()

