#!/usr/bin/env python3
"""
Tầng Cơ sở dữ liệu MongoDB Atlas (Cloud Database) cho AIOps Command Center.
Hỗ trợ đầy đủ CRUD (Thêm / Sửa / Xóa) cho bảng `Services` đồng bộ trực tiếp lên MongoDB Atlas.

Quản lý 7 Bảng (Collections) chuẩn trong database `AIOpsProject`:
  1. `Services`           : Danh mục các Dịch vụ ứng dụng doanh nghiệp (Hỗ trợ CRUD Thêm / Sửa / Xóa động)
  2. `AlertRules`         : Luật cảnh báo & chính sách ngưỡng AI (Global Policy Gate & Rule riêng theo từng Service)
  3. `SystemSettings`     : Cấu hình vận hành toàn hệ thống (Leo thang L1->L2->L3, Cooldown, Telegram ChatOps, UI Polling)
  4. `AIResults`          : Kết quả suy luận AI (Model 1 4-mức -> Dự báo 5m 4-mức -> Model 2 RCA) theo từng Service
  5. `Alerts`             : Cảnh báo sự cố chính thức của từng Service (AI & Hạ tầng Alertmanager)
  6. `RemediationActions` : Nhật ký câu lệnh tự động ứng phó khép kín theo IP:Port & Rollback từng dịch vụ
  7. `Users`              : Tài khoản đăng nhập & phân quyền RBAC (`ADMIN` / `VIEWER`, trạng thái `ACTIVE` / `LOCKED`)
"""

import ipaddress
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from auth import hash_password, verify_password

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(env_path: Path = ENV_FILE) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


DEFAULT_SERVICES: List[Dict[str, Any]] = [
    {
        "service_id": "srv_web_portal",
        "service_name": "Corporate Web Portal (Nginx HTTP)",
        "service_type": "HTTP",
        "host_node": "service-server",
        "zone": "HQ_SERVER_FARM (Tier 1 - Web)",
        "address": "192.168.10.10",
        "oob_ip": "172.20.20.21",
        "port": 80,
        "check_interval": "2s",
        "status": "NORMAL",
        "description": "Cổng thông tin Web doanh nghiệp (:80) & Reverse Proxy chuyển tiếp /api/ xuống tầng App",
    },
    {
        "service_id": "srv_flask_api",
        "service_name": "Employee & Auth REST API (Flask)",
        "service_type": "API",
        "host_node": "app-server",
        "zone": "HQ_SERVER_FARM (Tier 2 - App)",
        "address": "192.168.10.20",
        "oob_ip": "172.20.20.24",
        "port": 5000,
        "check_interval": "2s",
        "status": "NORMAL",
        "description": "Dịch vụ REST API xử lý nghiệp vụ nhân sự (/api/employees, /api/search) & xác thực (/api/login)",
    },
    {
        "service_id": "srv_postgres_db",
        "service_name": "Corporate Database (PostgreSQL 16)",
        "service_type": "DATABASE",
        "host_node": "db-server",
        "zone": "HQ_SERVER_FARM (Tier 3 - Data)",
        "address": "192.168.10.21",
        "oob_ip": "172.20.20.22",
        "port": 5432,
        "check_interval": "2s",
        "status": "NORMAL",
        "description": "Dịch vụ CSDL quan hệ lưu trữ hồ sơ nhân sự và tài khoản doanh nghiệp (database: corp)",
    },
    {
        "service_id": "srv_ssh_access",
        "service_name": "Remote Admin SSH (OpenSSH)",
        "service_type": "SSH",
        "host_node": "service-server",
        "zone": "HQ_SERVER_FARM (Admin Access)",
        "address": "192.168.10.10",
        "oob_ip": "172.20.20.21",
        "port": 22,
        "check_interval": "2s",
        "status": "NORMAL",
        "description": "Dịch vụ quản trị hệ thống từ xa qua giao thức OpenSSH (:22) trên Service Server",
    },
]

DEFAULT_ALERT_RULES: List[Dict[str, Any]] = [
    {
        "rule_id": "rule_ai_policy_gate",
        "rule_type": "GLOBAL_POLICY_GATE",
        "service_id": "ALL_SERVER_SERVICES",
        "metric_name": "m2_forecast_5m_prob",
        "threshold_value": 60.0,
        "comparison_operator": ">=",
        "severity": "critical",
        "auto_remediate": True,
        "is_active": True,
        "description": "Chính sách AI mặc định toàn cục (Policy Gate: Dự báo 5m >= ngưỡng để kích hoạt Model 2 RCA)",
    },
]

LEGACY_STATIC_RULE_IDS = {
    "rule_web_syn_flood",
    "rule_api_http_flood",
    "rule_ssh_bruteforce",
    "rule_insider_portscan",
}

VALID_RESPONSE_MODES = ("INHERIT", "AUTO_PILOT", "HUMAN_APPROVAL")

IP_TO_OOB = {
    "192.168.10.10": ("service-server", "172.20.20.21"),
    "192.168.10.20": ("app-server", "172.20.20.24"),
    "192.168.10.21": ("db-server", "172.20.20.22"),
}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        val = int(raw) if raw else default
    except ValueError:
        val = default
    return max(minimum, min(maximum, val))


DEFAULT_AI_THRESHOLDS: Dict[str, float] = {
    "m1_threshold_medium": 0.5451,
    "m1_threshold_high": 0.6061,
    "m1_threshold_critical": 0.6709,
    "forecast_threshold_pct": 60.0,
    "forecast_critical_pct": 80.0,
    "m2_confidence_threshold_pct": 70.0,
}


def build_default_system_settings(ai_defaults: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Tham số vận hành mặc định (nạp khởi tạo từ .env & ngưỡng hiệu chuẩn .pkl, tinh chỉnh nóng trên Cài đặt chung)."""
    l2 = _env_int("ESCALATION_L2_SECONDS", 30, 5, 600)
    l3 = max(l2 + 5, _env_int("ESCALATION_L3_SECONDS", 60, 10, 1800))
    ai_d = dict(DEFAULT_AI_THRESHOLDS)
    if isinstance(ai_defaults, dict):
        for k in DEFAULT_AI_THRESHOLDS:
            if k in ai_defaults and ai_defaults[k] is not None:
                ai_d[k] = float(ai_defaults[k])
    return {
        "auto_pilot": True,
        "m1_threshold_medium": round(ai_d["m1_threshold_medium"], 4),
        "m1_threshold_high": round(ai_d["m1_threshold_high"], 4),
        "m1_threshold_critical": round(ai_d["m1_threshold_critical"], 4),
        "forecast_threshold_pct": round(ai_d["forecast_threshold_pct"], 1),
        "forecast_critical_pct": round(ai_d["forecast_critical_pct"], 1),
        "m2_confidence_threshold_pct": round(ai_d["m2_confidence_threshold_pct"], 1),
        "escalation_l2_seconds": l2,
        "escalation_l3_seconds": l3,
        "l3_auto_mitigate": True,
        "l3_min_confidence_pct": 80.0,
        "auto_resolved_cycles": 2,
        "incident_cooldown_seconds": 12,
        "dismissed_cooldown_seconds": 45,
        "spike_log_cooldown_seconds": 18,
        "telegram_notify_ai": True,
        "telegram_notify_infra": True,
        "telegram_notify_resolved": True,
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "telegram_l2_chat_id": os.environ.get("TELEGRAM_L2_CHAT_ID", "").strip(),
        "telegram_admin_ids": os.environ.get("TELEGRAM_ADMIN_IDS", "").strip(),
        "ui_poll_interval_ms": 2000,
    }


def build_default_users() -> List[Dict[str, Any]]:
    """Tạo 2 tài khoản mặc định (admin / viewer) với mật khẩu đã băm PBKDF2-SHA256."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "role": "ADMIN",
            "full_name": "Quản trị viên NOC/SOC",
            "status": "ACTIVE",
            "created_at": now_str,
            "last_login": "-",
        },
        {
            "username": "viewer",
            "password_hash": hash_password("viewer123"),
            "role": "VIEWER",
            "full_name": "Giám sát viên (Chỉ đọc)",
            "status": "ACTIVE",
            "created_at": now_str,
            "last_login": "-",
        },
    ]

class AIOpsDatabase:
    """Quản lý kết nối MongoDB Atlas (7 bảng + index chuẩn) kèm CRUD Services & Users + bộ nhớ đệm an toàn."""

    def __init__(self, ai_defaults: Optional[Dict[str, float]] = None) -> None:
        load_env_file()
        self.mongo_uri = os.environ.get("MONGO_URI", "")
        self.db_name = os.environ.get("MONGO_DB_NAME", "AIOpsProject")
        self.connected = False
        self.status_message = "Chưa kết nối MongoDB"
        self.client: Any = None
        self.db: Any = None
        self.ai_defaults: Dict[str, float] = dict(DEFAULT_AI_THRESHOLDS)
        if isinstance(ai_defaults, dict):
            for k in DEFAULT_AI_THRESHOLDS:
                if k in ai_defaults and ai_defaults[k] is not None:
                    self.ai_defaults[k] = float(ai_defaults[k])

        self.services_cache: List[Dict[str, Any]] = [dict(s) for s in DEFAULT_SERVICES]
        self.rules_cache: List[Dict[str, Any]] = [dict(r) for r in DEFAULT_ALERT_RULES]
        self.service_alert_rules: Dict[str, Dict[str, Any]] = {}
        self.ai_results_cache: List[Dict[str, Any]] = []
        self.alerts_cache: List[Dict[str, Any]] = []
        self.remediations_cache: List[Dict[str, Any]] = []
        self.users_cache: List[Dict[str, Any]] = build_default_users()
        self.system_settings: Dict[str, Any] = build_default_system_settings(self.ai_defaults)
        self.counts: Dict[str, int] = {
            "Services": len(self.services_cache),
            "AlertRules": len(self.rules_cache),
            "SystemSettings": 1,
            "AIResults": 0,
            "Alerts": 0,
            "RemediationActions": 0,
            "Users": len(self.users_cache), 
        }

        self._connect_and_seed()

    def _run_async(self, fn: Callable[[], None]) -> None:
        """Ghi MongoDB Atlas bất đồng bộ trên luồng nền để không bao giờ làm lag thao tác Web UI."""
        def _worker() -> None:
            try:
                fn()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _connect_and_seed(self) -> None:
        if not self.mongo_uri:
            self.status_message = "Thiếu MONGO_URI trong .env (Đang chạy Local Memory)"
            return

        try:
            from pymongo import MongoClient

            self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command("ping")
            if "aiops_db" in self.client.list_database_names() and self.db_name != "aiops_db":
                self.client.drop_database("aiops_db")

            self.db = self.client[self.db_name]
            self.connected = True
            self.status_message = f"Đã kết nối MongoDB Atlas ({self.db_name})"
            print(f"[✓] Đã kết nối thành công tới MongoDB Atlas: {self.db_name}")

            existing_cols = set(self.db.list_collection_names())
            for col_name in (
                "Services",
                "AlertRules",
                "SystemSettings",
                "AIResults",
                "Alerts",
                "RemediationActions",
                "Users",
            ):
                if col_name not in existing_cols:
                    self.db.create_collection(col_name)

            # Xóa các bản ghi router/firewall cũ nếu còn tồn tại
            legacy_ids = {"srv_core_router", "srv_branch_router", "srv_firewall", "srv_web_nginx", "srv_app_api", "srv_db_postgres"}
            current_ids = {
                doc.get("service_id") for doc in self.db["Services"].find({}, {"service_id": 1})
            }
            if not current_ids or (current_ids & legacy_ids):
                self.db["Services"].delete_many({"service_id": {"$in": list(legacy_ids)}})
                if self.db["Services"].count_documents({}) == 0:
                    self.db["Services"].insert_many([dict(s) for s in DEFAULT_SERVICES])
                    print("    -> Đã khởi tạo bảng 'Services' trên MongoDB Atlas")

            # Dọn 4 bản ghi mô tả tĩnh cũ (không thực thi) nếu còn tồn tại
            self.db["AlertRules"].delete_many({"rule_id": {"$in": list(LEGACY_STATIC_RULE_IDS)}})
            if self.db["AlertRules"].count_documents({"rule_id": "rule_ai_policy_gate"}) == 0:
                self.db["AlertRules"].insert_one(dict(DEFAULT_ALERT_RULES[0]))

            # Tự động chuyển (migrate) cấu hình hệ thống từ AlertRules sang collection riêng SystemSettings (không mất dữ liệu)
            legacy_settings_doc = self.db["AlertRules"].find_one({"rule_id": "rule_system_settings"}, {"_id": 0})
            if self.db["SystemSettings"].count_documents({"setting_id": "global_system_settings"}) == 0:
                if legacy_settings_doc and isinstance(legacy_settings_doc.get("settings"), dict):
                    self.db["SystemSettings"].insert_one(
                        {
                            "setting_id": "global_system_settings",
                            "settings": dict(legacy_settings_doc["settings"]),
                            "updated_at": legacy_settings_doc.get(
                                "updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            ),
                        }
                    )
                    print("    -> Đã chuyển cấu hình hệ thống sang bảng riêng 'SystemSettings'")
                else:
                    self.db["SystemSettings"].insert_one(
                        {
                            "setting_id": "global_system_settings",
                            "settings": build_default_system_settings(self.ai_defaults),
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )
                    print("    -> Đã khởi tạo bảng 'SystemSettings' trên MongoDB Atlas")
            if legacy_settings_doc is not None:
                self.db["AlertRules"].delete_many({"rule_id": "rule_system_settings"})

            if self.db["Users"].count_documents({}) == 0:
                self.db["Users"].insert_many([dict(u) for u in self.users_cache])
                print(" -> Đã khởi tạo bảng 'Users' (admin / viewer) trên MongoDB Atlas")

            self._ensure_indexes()
            self._refresh_caches()
        except Exception as exc:
            self.connected = False
            self.status_message = f"Lỗi kết nối MongoDB ({type(exc).__name__}) - Chạy Local Fallback"
            print(f"[!] Không thể kết nối MongoDB Atlas: {exc}")

    def _ensure_indexes(self) -> None:
        """Tạo các Unique Index & Compound Index chuẩn cho 7 collections để tối ưu truy vấn và toàn vẹn dữ liệu."""
        if not self.connected or self.db is None:
            return
        index_specs = [
            ("Users", [("username", 1)], {"unique": True, "name": "idx_users_username_unique"}),
            ("Services", [("service_id", 1)], {"unique": True, "name": "idx_services_id_unique"}),
            ("AlertRules", [("rule_id", 1)], {"unique": True, "name": "idx_alertrules_id_unique"}),
            ("AlertRules", [("service_id", 1)], {"name": "idx_alertrules_service_id"}),
            ("SystemSettings", [("setting_id", 1)], {"unique": True, "name": "idx_systemsettings_id_unique"}),
            ("AIResults", [("timestamp", -1)], {"name": "idx_airesults_ts_desc"}),
            ("AIResults", [("service_id", 1), ("timestamp", -1)], {"name": "idx_airesults_svc_ts"}),
            ("Alerts", [("created_at", -1)], {"name": "idx_alerts_created_desc"}),
            ("Alerts", [("service_id", 1), ("created_at", -1)], {"name": "idx_alerts_svc_created"}),
            ("Alerts", [("status", 1), ("created_at", -1)], {"name": "idx_alerts_status_created"}),
            ("RemediationActions", [("timestamp", -1)], {"name": "idx_remediations_ts_desc"}),
            ("RemediationActions", [("service_id", 1), ("timestamp", -1)], {"name": "idx_remediations_svc_ts"}),
        ]
        for col_name, keys, opts in index_specs:
            try:
                self.db[col_name].create_index(keys, **opts)
            except Exception:
                pass

    # =========================================================================
    # CRUD SERVICES (THÊM / SỬA / XÓA DỊCH VỤ ĐỘNG)
    # =========================================================================
    @staticmethod
    def _validate_service_endpoint(address: str, port_raw: Any) -> Tuple[str, int]:
        addr = str(address or "").strip()
        if not addr:
            raise ValueError("Địa chỉ IP/Hostname dịch vụ không được để trống")
        try:
            ipaddress.ip_address(addr)
        except ValueError:
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,126}[a-zA-Z0-9]$", addr):
                raise ValueError(f"Địa chỉ IP/Hostname không hợp lệ: '{addr}'")

        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise ValueError("Cổng dịch vụ (port) phải là số nguyên hợp lệ")
        if port < 1 or port > 65535:
            raise ValueError("Cổng dịch vụ (port) phải nằm trong khoảng 1 – 65535")
        return addr, port

    def create_service(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_name = str(payload.get("service_name", "New Service")).strip()[:120] or "New Service"
        raw_id = str(payload.get("service_id", "")).strip()
        if raw_id:
            if not re.match(r"^[a-zA-Z0-9_.-]{2,64}$", raw_id):
                raise ValueError("service_id chỉ được chứa chữ cái, số và ký tự _, ., - (2–64 ký tự)")
        else:
            slug = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")[:18] or "service"
            raw_id = f"srv_{slug}_{datetime.now().strftime('%H%M%S')}"

        addr_in = payload.get("address", "192.168.10.10")
        port_in = payload.get("port", 8080)
        if port_in in ("", None):
            port_in = 8080
        addr, port = self._validate_service_endpoint(str(addr_in or "192.168.10.10"), port_in)
        stype = str(payload.get("service_type", "HTTP")).strip().upper()[:24] or "HTTP"
        default_host, default_oob = IP_TO_OOB.get(addr, ("service-server", "172.20.20.21"))

        new_doc: Dict[str, Any] = {
            "service_id": raw_id,
            "service_name": raw_name,
            "service_type": stype,
            "host_node": str(payload.get("host_node", default_host)).strip()[:64] or default_host,
            "zone": str(payload.get("zone", "HQ_SERVER_FARM (Custom Service)")).strip()[:80],
            "address": addr,
            "oob_ip": str(payload.get("oob_ip", default_oob)).strip()[:64] or default_oob,
            "port": port,
            "check_interval": str(payload.get("check_interval", "2s")).strip()[:16] or "2s",
            "status": "NORMAL",
            "description": str(
                payload.get("description", f"Dịch vụ {stype} vận hành tại {addr}:{port}")
            ).strip()[:300],
        }

        # Tránh trùng service_id
        self.services_cache = [s for s in self.services_cache if s["service_id"] != raw_id]
        self.services_cache.append(dict(new_doc))
        self.counts["Services"] = len(self.services_cache)

        if self.connected and self.db is not None:
            doc_copy = dict(new_doc)
            self._run_async(
                lambda: self.db["Services"].update_one(
                    {"service_id": raw_id}, {"$set": doc_copy}, upsert=True
                )
            )

        return new_doc

    def update_service(self, service_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target = None
        for s in self.services_cache:
            if s["service_id"] == service_id:
                target = s
                break
        if target is None:
            return None

        addr_in = payload.get("address", target["address"])
        port_in = payload.get("port", target["port"])
        if port_in in ("", None):
            port_in = target["port"]
        addr, port = self._validate_service_endpoint(str(addr_in or target["address"]), port_in)
        default_host, default_oob = IP_TO_OOB.get(
            addr, (target.get("host_node", "service-server"), target.get("oob_ip", "172.20.20.21"))
        )

        updates = {
            "service_name": str(payload.get("service_name", target["service_name"])).strip()[:120] or target["service_name"],
            "service_type": str(payload.get("service_type", target["service_type"])).strip().upper()[:24] or target["service_type"],
            "address": addr,
            "port": port,
            "host_node": str(payload.get("host_node", default_host)).strip()[:64] or default_host,
            "oob_ip": str(payload.get("oob_ip", default_oob)).strip()[:64] or default_oob,
            "zone": str(payload.get("zone", target.get("zone", "HQ_SERVER_FARM"))).strip()[:80],
            "check_interval": str(payload.get("check_interval", target.get("check_interval", "2s"))).strip()[:16],
            "description": str(payload.get("description", target.get("description", ""))).strip()[:300],
        }
        target.update(updates)

        if self.connected and self.db is not None:
            upd_copy = dict(updates)
            self._run_async(
                lambda: self.db["Services"].update_one({"service_id": service_id}, {"$set": upd_copy})
            )

        return target

    def delete_service(self, service_id: str) -> bool:
        if len(self.services_cache) <= 1:
            # Giữ tối thiểu 1 dịch vụ trong hệ thống
            return False
        before = len(self.services_cache)
        self.services_cache = [s for s in self.services_cache if s["service_id"] != service_id]
        self.counts["Services"] = len(self.services_cache)
        self.delete_service_alert_rule(service_id)

        if self.connected and self.db is not None:
            self._run_async(lambda: self.db["Services"].delete_one({"service_id": service_id}))
        return len(self.services_cache) < before

    def update_services_live_state(self, evaluations: Dict[str, Dict[str, Any]]) -> None:
        """Cập nhật trạng thái 4 mức độ (NORMAL / MEDIUM / HIGH / CRITICAL) & 6 thông số SLA vào danh mục Services."""
        for s in self.services_cache:
            sid = s["service_id"]
            ev = evaluations.get(sid)
            if ev:
                m = ev.get("metrics", {})
                s["status"] = ev["severity_level"]
                s["severity_level"] = ev["severity_level"]
                s["forecast_level_5m"] = ev["model2"]["forecast_level"]
                s["eif_score"] = ev["model1"]["anomaly_score"]
                s["prob_5m_pct"] = ev["model2"]["prob_5m_pct"]
                s["fault_class"] = ev["model3"]["fault_class"]
                s["latency_ms"] = m.get("latency", 0.0)
                s["jitter_ms"] = m.get("jitter", 0.0)
                s["packet_loss_pct"] = m.get("packet_loss", 0.0)
                s["throughput_kbs"] = m.get("throughput", 0.0)
                s["response_time_ms"] = m.get("response_time", 0.0)
                s["uptime_pct"] = m.get("uptime", 100.0)

    def _refresh_caches(self) -> None:
        if not self.connected or self.db is None:
            return
        try:
            db_services = list(self.db["Services"].find({}, {"_id": 0}))
            if db_services:
                runtime_map = {s["service_id"]: s for s in self.services_cache}
                merged_services = []
                for s in db_services:
                    prev = runtime_map.get(s["service_id"], {})
                    for k in (
                        "status",
                        "severity_level",
                        "forecast_level_5m",
                        "eif_score",
                        "prob_5m_pct",
                        "fault_class",
                        "latency_ms",
                        "jitter_ms",
                        "packet_loss_pct",
                        "throughput_kbs",
                        "response_time_ms",
                        "uptime_pct",
                    ):
                        if k in prev:
                            s[k] = prev[k]
                    merged_services.append(s)
                self.services_cache = merged_services
            all_rules = list(self.db["AlertRules"].find({}, {"_id": 0}))
            settings_doc = self.db["SystemSettings"].find_one(
                {"setting_id": "global_system_settings"}, {"_id": 0}
            )
            if settings_doc is None:
                settings_doc = next((r for r in all_rules if r.get("rule_id") == "rule_system_settings"), None)
            self.rules_cache = [
                r
                for r in all_rules
                if r.get("rule_id") != "rule_system_settings"
                and r.get("rule_id") not in LEGACY_STATIC_RULE_IDS
            ] or [dict(r) for r in DEFAULT_ALERT_RULES]

            merged_settings = build_default_system_settings(self.ai_defaults)
            policy_cfg = self.get_policy_config()
            merged_settings["auto_pilot"] = bool(policy_cfg.get("auto_pilot", True))
            merged_settings["forecast_threshold_pct"] = float(policy_cfg.get("threshold_pct", 60.0))
            if settings_doc and isinstance(settings_doc.get("settings"), dict):
                saved = settings_doc["settings"]
                for k, v in saved.items():
                    if k not in merged_settings or v is None:
                        continue
                    # Nếu .env mới cấu hình Telegram mà bản lưu cũ đang trống thì ưu tiên .env
                    if k in ("telegram_chat_id", "telegram_admin_ids") and not str(v).strip() and merged_settings[k]:
                        continue
                    merged_settings[k] = v
            self.system_settings = merged_settings

            loaded_srv_rules: Dict[str, Dict[str, Any]] = {}
            for r in all_rules:
                if r.get("rule_type") == "SERVICE_AI_POLICY" or str(r.get("rule_id", "")).startswith("rule_srv_"):
                    sid = str(r.get("service_id", "")).strip()
                    if sid:
                        r_norm_in = dict(r)
                        if str(r_norm_in.get("response_mode", "")).upper() == "ALERT_ONLY":
                            r_norm_in["response_mode"] = "HUMAN_APPROVAL"
                        norm, err = self._validate_service_alert_rule(sid, r_norm_in, self.system_settings)
                        if norm and not err:
                            loaded_srv_rules[sid] = norm
            self.service_alert_rules = loaded_srv_rules
            self.ai_results_cache = list(
                self.db["AIResults"].find({}, {"_id": 0}).sort("timestamp", -1).limit(200)
            )
            self.alerts_cache = list(
                self.db["Alerts"].find({}, {"_id": 0}).sort("created_at", -1).limit(200)
            )
            self.remediations_cache = list(
                self.db["RemediationActions"].find({}, {"_id": 0}).sort("timestamp", -1).limit(200)
            )
            
            db_users = list(self.db["Users"].find({}, {"_id": 0}))
            if db_users:
                for u in db_users:
                    u.setdefault("status", "ACTIVE")
                    u.setdefault("created_at", "-")
                    u.setdefault("last_login", "-")
                self.users_cache = db_users

            self.counts = {
                "Services": len(self.services_cache),
                "AlertRules": len(self.rules_cache),
                "SystemSettings": self.db["SystemSettings"].count_documents({}),
                "AIResults": self.db["AIResults"].count_documents({}),
                "Alerts": self.db["Alerts"].count_documents({}),
                "RemediationActions": self.db["RemediationActions"].count_documents({}),
                "Users": len(self.users_cache),
            }
        except Exception:
            pass

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Tìm tài khoản từ RAM cache (không gọi mạng tới MongoDB Atlas)."""
        uname = str(username or "").strip().lower()
        for u in self.users_cache:
            if str(u.get("username", "")).lower() == uname:
                return u
        return None

    # CRUD Users
    @staticmethod
    def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
        """Trả về thông tin user an toàn (không bao giờ trả password_hash)."""
        return {
            "username": user.get("username", ""),
            "role": str(user.get("role", "VIEWER")).upper(),
            "full_name": user.get("full_name", ""),
            "status": str(user.get("status", "ACTIVE")).upper(),
            "created_at": user.get("created_at", "-"),
            "last_login": user.get("last_login", "-"),
        }

    def _active_admin_count(self) -> int:
        return sum(
            1
            for u in self.users_cache
            if str(u.get("role", "")).upper() == "ADMIN"
            and str(u.get("status", "ACTIVE")).upper() == "ACTIVE"
        )

    def list_users(self) -> List[Dict[str, Any]]:
        """Lấy danh sách toàn bộ tài khoản (không kèm mật khẩu băm)."""
        return [self._public_user(u) for u in self.users_cache]

    def record_user_login(self, username: str) -> None:
        target = self.get_user_by_username(username)
        if target is None:
            return
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        target["last_login"] = now_str
        uname = target["username"]
        if self.connected and self.db is not None:
            self._run_async(
                lambda: self.db["Users"].update_one(
                    {"username": uname}, {"$set": {"last_login": now_str}}
                )
            )

    def create_user(
        self, payload: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        uname = str(payload.get("username", "")).strip().lower()
        password = str(payload.get("password", ""))
        role = str(payload.get("role", "VIEWER")).strip().upper()
        status = str(payload.get("status", "ACTIVE")).strip().upper()
        full_name = str(payload.get("full_name", "")).strip() or uname

        if not uname or not re.match(r"^[a-z0-9_.-]{3,32}$", uname):
            return None, "Tên đăng nhập phải từ 3–32 ký tự (chữ thường, số, _, ., -)"
        if len(password) < 4:
            return None, "Mật khẩu phải có ít nhất 4 ký tự"
        if role not in ("ADMIN", "VIEWER"):
            return None, "Vai trò chỉ chấp nhận ADMIN hoặc VIEWER"
        if status not in ("ACTIVE", "LOCKED"):
            return None, "Trạng thái chỉ chấp nhận ACTIVE hoặc LOCKED"
        if self.get_user_by_username(uname) is not None:
            return None, f"Tài khoản '{uname}' đã tồn tại"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_doc: Dict[str, Any] = {
            "username": uname,
            "password_hash": hash_password(password),
            "role": role,
            "full_name": full_name,
            "status": status,
            "created_at": now_str,
            "last_login": "-",
        }
        self.users_cache.append(dict(new_doc))
        self.counts["Users"] = len(self.users_cache)

        if self.connected and self.db is not None:
            doc_copy = dict(new_doc)
            self._run_async(
                lambda: self.db["Users"].update_one(
                    {"username": uname}, {"$set": doc_copy}, upsert=True
                )
            )
        return self._public_user(new_doc), None

    def update_user(
        self, username: str, payload: Dict[str, Any], current_username: str = ""
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        target = self.get_user_by_username(username)
        if target is None:
            return None, f"Không tìm thấy tài khoản '{username}'"

        uname = target["username"]
        cur_uname = str(current_username or "").strip().lower()
        old_role = str(target.get("role", "VIEWER")).upper()
        old_status = str(target.get("status", "ACTIVE")).upper()
        new_role = str(payload.get("role", old_role)).strip().upper()
        new_status = str(payload.get("status", old_status)).strip().upper()

        if new_role not in ("ADMIN", "VIEWER"):
            return None, "Vai trò chỉ chấp nhận ADMIN hoặc VIEWER"
        if new_status not in ("ACTIVE", "LOCKED"):
            return None, "Trạng thái chỉ chấp nhận ACTIVE hoặc LOCKED"

        if cur_uname and uname == cur_uname:
            if new_role != "ADMIN" and old_role == "ADMIN":
                return None, "Không thể tự hạ quyền tài khoản đang đăng nhập"
            if new_status == "LOCKED":
                return None, "Không thể tự khóa tài khoản đang đăng nhập"

        # Chặn hạ quyền hoặc khóa nếu đây là ADMIN đang hoạt động cuối cùng
        was_active_admin = old_role == "ADMIN" and old_status == "ACTIVE"
        will_be_active_admin = new_role == "ADMIN" and new_status == "ACTIVE"
        if was_active_admin and not will_be_active_admin and self._active_admin_count() <= 1:
            return None, "Phải giữ ít nhất 1 tài khoản ADMIN đang hoạt động trong hệ thống"

        updates: Dict[str, Any] = {
            "role": new_role,
            "status": new_status,
            "full_name": str(payload.get("full_name", target.get("full_name", ""))).strip() or uname,
        }
        new_password = str(payload.get("password", ""))
        if new_password:
            if len(new_password) < 4:
                return None, "Mật khẩu mới phải có ít nhất 4 ký tự"
            updates["password_hash"] = hash_password(new_password)

        target.update(updates)
        if self.connected and self.db is not None:
            upd_copy = dict(updates)
            self._run_async(
                lambda: self.db["Users"].update_one({"username": uname}, {"$set": upd_copy})
            )
        return self._public_user(target), None

    def change_own_password(
        self, username: str, current_password: str, new_password: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        target = self.get_user_by_username(username)
        if target is None:
            return None, "Không tìm thấy tài khoản"
        if not verify_password(current_password, str(target.get("password_hash", ""))):
            return None, "Mật khẩu hiện tại không đúng"
        if len(new_password) < 4:
            return None, "Mật khẩu mới phải có ít nhất 4 ký tự"
        if new_password == current_password:
            return None, "Mật khẩu mới phải khác mật khẩu hiện tại"

        new_hash = hash_password(new_password)
        target["password_hash"] = new_hash
        uname = target["username"]
        if self.connected and self.db is not None:
            self._run_async(
                lambda: self.db["Users"].update_one(
                    {"username": uname}, {"$set": {"password_hash": new_hash}}
                )
            )
        return dict(target), None

    def delete_user(
        self, username: str, current_username: str = ""
    ) -> Tuple[bool, Optional[str]]:
        target = self.get_user_by_username(username)
        if target is None:
            return False, f"Không tìm thấy tài khoản '{username}'"

        uname = target["username"]
        if current_username and uname == current_username.strip().lower():
            return False, "Không thể tự xóa tài khoản đang đăng nhập"

        if (
            str(target.get("role", "")).upper() == "ADMIN"
            and str(target.get("status", "ACTIVE")).upper() == "ACTIVE"
            and self._active_admin_count() <= 1
        ):
            return False, "Phải giữ ít nhất 1 tài khoản ADMIN đang hoạt động trong hệ thống"

        self.users_cache = [u for u in self.users_cache if u["username"] != uname]
        self.counts["Users"] = len(self.users_cache)

        if self.connected and self.db is not None:
            self._run_async(lambda: self.db["Users"].delete_one({"username": uname}))
        return True, None

    def get_policy_config(self) -> Dict[str, Any]:
        for r in self.rules_cache:
            if r.get("rule_id") == "rule_ai_policy_gate":
                return {
                    "threshold_pct": float(r.get("threshold_value", 60.0)),
                    "auto_pilot": bool(r.get("auto_remediate", True)),
                }
        return {"threshold_pct": 60.0, "auto_pilot": True}

    def update_policy_rule(
        self, threshold_pct: Optional[float] = None, auto_pilot: Optional[bool] = None
    ) -> None:
        for r in self.rules_cache:
            if r.get("rule_id") == "rule_ai_policy_gate":
                if threshold_pct is not None:
                    r["threshold_value"] = round(float(threshold_pct), 1)
                if auto_pilot is not None:
                    r["auto_remediate"] = bool(auto_pilot)

        if threshold_pct is not None:
            thr_val = round(float(threshold_pct), 1)
            self.system_settings["forecast_threshold_pct"] = thr_val
            if float(self.system_settings.get("forecast_critical_pct", 80.0)) < thr_val + 5.0:
                self.system_settings["forecast_critical_pct"] = round(min(98.0, thr_val + 5.0), 1)
        if auto_pilot is not None:
            self.system_settings["auto_pilot"] = bool(auto_pilot)

        if self.connected and self.db is not None:
            update_fields: Dict[str, Any] = {}
            if threshold_pct is not None:
                update_fields["threshold_value"] = round(float(threshold_pct), 1)
            if auto_pilot is not None:
                update_fields["auto_remediate"] = bool(auto_pilot)
            if update_fields:
                uf_copy = dict(update_fields)
                settings_copy = dict(self.system_settings)
                self._run_async(
                    lambda: (
                        self.db["AlertRules"].update_one(
                            {"rule_id": "rule_ai_policy_gate"}, {"$set": uf_copy}, upsert=True
                        ),
                        self.db["SystemSettings"].update_one(
                            {"setting_id": "global_system_settings"},
                            {
                                "$set": {
                                    "setting_id": "global_system_settings",
                                    "settings": settings_copy,
                                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                            },
                            upsert=True,
                        ),
                    )
                )

    def _sync_rules_cache_from_memory(self) -> None:
        """Đồng bộ danh sách rules_cache (Global Policy Gate + các AlertRule riêng theo dịch vụ)."""
        global_rule = next(
            (r for r in self.rules_cache if r.get("rule_id") == "rule_ai_policy_gate"),
            dict(DEFAULT_ALERT_RULES[0]),
        )
        self.rules_cache = [global_rule] + [dict(r) for r in self.service_alert_rules.values()]
        self.counts["AlertRules"] = len(self.rules_cache)

    @staticmethod
    def _validate_service_alert_rule(
        service_id: str,
        payload: Dict[str, Any],
        base_settings: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, f"Dữ liệu AlertRule của '{service_id}' không hợp lệ"

        def _pnum(key: str, lo: float, hi: float, label: str):
            raw = payload.get(key, base_settings.get(key))
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return None, f"[{service_id}] {label} phải là số hợp lệ"
            if val < lo or val > hi:
                return None, f"[{service_id}] {label} phải nằm trong khoảng {lo} – {hi}"
            return val, None

        m1_med, err = _pnum("m1_threshold_medium", 0.35, 0.85, "Model 1 · Ngưỡng MEDIUM")
        if err:
            return None, err
        m1_high, err = _pnum("m1_threshold_high", 0.40, 0.88, "Model 1 · Ngưỡng HIGH")
        if err:
            return None, err
        m1_crit, err = _pnum("m1_threshold_critical", 0.45, 0.95, "Model 1 · Ngưỡng CRITICAL")
        if err:
            return None, err
        if m1_high < m1_med + 0.01:
            return None, f"[{service_id}] Model 1 · Ngưỡng HIGH ({m1_high:.4f}) phải lớn hơn MEDIUM ({m1_med:.4f}) ít nhất 0.01"
        if m1_crit < m1_high + 0.01:
            return None, f"[{service_id}] Model 1 · Ngưỡng CRITICAL ({m1_crit:.4f}) phải lớn hơn HIGH ({m1_high:.4f}) ít nhất 0.01"

        fc_thr, err = _pnum("forecast_threshold_pct", 40.0, 90.0, "Dự báo 5m · Kích hoạt M2 (%)")
        if err:
            return None, err
        fc_crit, err = _pnum("forecast_critical_pct", 55.0, 98.0, "Dự báo 5m · Ngưỡng CRITICAL (%)")
        if err:
            return None, err
        if fc_crit < fc_thr + 5.0:
            return None, f"[{service_id}] Dự báo 5m · Ngưỡng CRITICAL ({fc_crit:.1f}%) phải lớn hơn ngưỡng kích hoạt ({fc_thr:.1f}%) ít nhất 5%"

        m2_conf, err = _pnum("m2_confidence_threshold_pct", 50.0, 95.0, "Model 2 RCA · Tin cậy tối thiểu (%)")
        if err:
            return None, err

        mode = str(payload.get("response_mode", "INHERIT")).strip().upper() or "INHERIT"
        if mode not in VALID_RESPONSE_MODES:
            return None, f"[{service_id}] Chế độ ứng phó chỉ chấp nhận: {', '.join(VALID_RESPONSE_MODES)}"

        is_active = bool(payload.get("is_active", True))
        return {
            "rule_id": f"rule_srv_{service_id}",
            "rule_type": "SERVICE_AI_POLICY",
            "service_id": service_id,
            "is_active": is_active,
            "response_mode": mode,
            "m1_threshold_medium": round(m1_med, 4),
            "m1_threshold_high": round(m1_high, 4),
            "m1_threshold_critical": round(m1_crit, 4),
            "forecast_threshold_pct": round(fc_thr, 1),
            "forecast_critical_pct": round(fc_crit, 1),
            "m2_confidence_threshold_pct": round(m2_conf, 1),
            "updated_at": str(payload.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }, None

    def get_service_alert_rules(self) -> Dict[str, Dict[str, Any]]:
        return {sid: dict(rule) for sid, rule in self.service_alert_rules.items()}

    def get_effective_service_policy(self, service_id: str) -> Dict[str, Any]:
        """Trả bộ ngưỡng AI & chế độ ứng phó hiệu lực cho 1 dịch vụ (từ RAM cache < 0.01ms): Rule riêng -> Kế thừa chung."""
        g = self.system_settings
        global_auto = bool(g.get("auto_pilot", True))
        custom = self.service_alert_rules.get(service_id)
        if custom:
            raw_mode = str(custom.get("response_mode", "INHERIT")).upper()
            if raw_mode == "INHERIT":
                eff_mode = "AUTO_PILOT" if global_auto else "HUMAN_APPROVAL"
            else:
                eff_mode = raw_mode
            return {
                "service_id": service_id,
                "has_custom_rule": True,
                "is_active": bool(custom.get("is_active", True)),
                "response_mode": raw_mode,
                "effective_response_mode": eff_mode,
                "m1_threshold_medium": float(custom.get("m1_threshold_medium", g["m1_threshold_medium"])),
                "m1_threshold_high": float(custom.get("m1_threshold_high", g["m1_threshold_high"])),
                "m1_threshold_critical": float(custom.get("m1_threshold_critical", g["m1_threshold_critical"])),
                "forecast_threshold_pct": float(custom.get("forecast_threshold_pct", g["forecast_threshold_pct"])),
                "forecast_critical_pct": float(custom.get("forecast_critical_pct", g["forecast_critical_pct"])),
                "m2_confidence_threshold_pct": float(custom.get("m2_confidence_threshold_pct", g["m2_confidence_threshold_pct"])),
            }
        return {
            "service_id": service_id,
            "has_custom_rule": False,
            "is_active": True,
            "response_mode": "INHERIT",
            "effective_response_mode": "AUTO_PILOT" if global_auto else "HUMAN_APPROVAL",
            "m1_threshold_medium": float(g["m1_threshold_medium"]),
            "m1_threshold_high": float(g["m1_threshold_high"]),
            "m1_threshold_critical": float(g["m1_threshold_critical"]),
            "forecast_threshold_pct": float(g["forecast_threshold_pct"]),
            "forecast_critical_pct": float(g["forecast_critical_pct"]),
            "m2_confidence_threshold_pct": float(g["m2_confidence_threshold_pct"]),
        }

    def upsert_service_alert_rule(
        self, service_id: str, payload: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        valid_sids = {s["service_id"] for s in self.services_cache}
        if service_id not in valid_sids:
            return None, f"Không tìm thấy dịch vụ '{service_id}'"
        norm, err = self._validate_service_alert_rule(service_id, payload, self.system_settings)
        if err or not norm:
            return None, err
        norm["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.service_alert_rules[service_id] = dict(norm)
        self._sync_rules_cache_from_memory()
        if self.connected and self.db is not None:
            doc_copy = dict(norm)
            rule_id = norm["rule_id"]
            self._run_async(
                lambda: self.db["AlertRules"].update_one(
                    {"rule_id": rule_id}, {"$set": doc_copy}, upsert=True
                )
            )
        return dict(norm), None

    def delete_service_alert_rule(self, service_id: str) -> bool:
        existed = self.service_alert_rules.pop(service_id, None) is not None
        self._sync_rules_cache_from_memory()
        if self.connected and self.db is not None:
            rule_id = f"rule_srv_{service_id}"
            self._run_async(lambda: self.db["AlertRules"].delete_one({"rule_id": rule_id}))
        return existed

    def get_system_settings(self) -> Dict[str, Any]:
        return dict(self.system_settings)

    def get_public_settings(self) -> Dict[str, Any]:
        """Trả cài đặt vận hành không chứa thông tin nhạy cảm (Chat ID / Admin ID) để đính kèm /api/state."""
        return {
            k: v
            for k, v in self.system_settings.items()
            if k not in ("telegram_chat_id", "telegram_l2_chat_id", "telegram_admin_ids")
        }

    def update_system_settings(
        self, payload: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, "Dữ liệu cài đặt không hợp lệ"

        cur = dict(self.system_settings)

        def _parse_num(key: str, cast, lo, hi, label: str):
            if key not in payload:
                return cur[key], None
            try:
                val = cast(payload[key])
            except (TypeError, ValueError):
                return None, f"{label} phải là số hợp lệ"
            if val < lo or val > hi:
                return None, f"{label} phải nằm trong khoảng {lo} – {hi}"
            return val, None

        l2, err = _parse_num("escalation_l2_seconds", int, 5, 600, "Thời gian leo thang Tầng 2 (giây)")
        if err:
            return None, err
        l3, err = _parse_num("escalation_l3_seconds", int, 10, 1800, "Thời gian leo thang Tầng 3 (giây)")
        if err:
            return None, err
        if l3 < l2 + 5:
            return None, f"Thời gian leo thang Tầng 3 ({l3}s) phải lớn hơn Tầng 2 ít nhất 5 giây (≥ {l2 + 5}s)"

        m1_med, err = _parse_num("m1_threshold_medium", float, 0.35, 0.85, "Model 1 · Ngưỡng MEDIUM")
        if err:
            return None, err
        m1_high, err = _parse_num("m1_threshold_high", float, 0.40, 0.88, "Model 1 · Ngưỡng HIGH")
        if err:
            return None, err
        m1_crit, err = _parse_num("m1_threshold_critical", float, 0.45, 0.95, "Model 1 · Ngưỡng CRITICAL")
        if err:
            return None, err
        if m1_high < m1_med + 0.01:
            return None, f"Model 1 · Ngưỡng HIGH ({m1_high:.4f}) phải lớn hơn MEDIUM ({m1_med:.4f}) ít nhất 0.01"
        if m1_crit < m1_high + 0.01:
            return None, f"Model 1 · Ngưỡng CRITICAL ({m1_crit:.4f}) phải lớn hơn HIGH ({m1_high:.4f}) ít nhất 0.01"

        forecast_pct, err = _parse_num("forecast_threshold_pct", float, 40.0, 90.0, "Dự báo 5m · Ngưỡng kích hoạt M2 (%)")
        if err:
            return None, err
        forecast_crit_pct, err = _parse_num("forecast_critical_pct", float, 55.0, 98.0, "Dự báo 5m · Ngưỡng CRITICAL (%)")
        if err:
            return None, err
        if forecast_crit_pct < forecast_pct + 5.0:
            return None, (
                f"Dự báo 5m · Ngưỡng CRITICAL ({forecast_crit_pct:.1f}%) phải lớn hơn "
                f"ngưỡng kích hoạt M2 ({forecast_pct:.1f}%) ít nhất 5%"
            )
        m2_conf_pct, err = _parse_num("m2_confidence_threshold_pct", float, 50.0, 95.0, "Model 2 RCA · Độ tin cậy tối thiểu (%)")
        if err:
            return None, err
        l3_conf, err = _parse_num("l3_min_confidence_pct", float, 70.0, 99.0, "Độ tin cậy tối thiểu Tầng 3 (%)")
        if err:
            return None, err
        auto_res_cycles, err = _parse_num("auto_resolved_cycles", int, 1, 10, "Số chu kỳ hồi phục Auto-Resolved")
        if err:
            return None, err
        inc_cd, err = _parse_num("incident_cooldown_seconds", int, 5, 120, "Cooldown chống lặp sự cố (giây)")
        if err:
            return None, err
        dis_cd, err = _parse_num("dismissed_cooldown_seconds", int, 10, 600, "Thời gian tạm ẩn sau khi Bỏ qua (giây)")
        if err:
            return None, err
        spk_cd, err = _parse_num("spike_log_cooldown_seconds", int, 5, 120, "Cooldown ghi log TransientSpike (giây)")
        if err:
            return None, err
        poll_ms, err = _parse_num("ui_poll_interval_ms", int, 1000, 10000, "Chu kỳ làm mới giao diện (ms)")
        if err:
            return None, err

        def _parse_bool(key: str) -> bool:
            return bool(payload[key]) if key in payload else bool(cur[key])

        def _parse_str(key: str) -> str:
            return str(payload[key]).strip() if key in payload else str(cur.get(key, "")).strip()

        updated: Dict[str, Any] = {
            "auto_pilot": _parse_bool("auto_pilot"),
            "m1_threshold_medium": round(float(m1_med), 4),
            "m1_threshold_high": round(float(m1_high), 4),
            "m1_threshold_critical": round(float(m1_crit), 4),
            "forecast_threshold_pct": round(float(forecast_pct), 1),
            "forecast_critical_pct": round(float(forecast_crit_pct), 1),
            "m2_confidence_threshold_pct": round(float(m2_conf_pct), 1),
            "escalation_l2_seconds": int(l2),
            "escalation_l3_seconds": int(l3),
            "l3_auto_mitigate": _parse_bool("l3_auto_mitigate"),
            "l3_min_confidence_pct": round(float(l3_conf), 1),
            "auto_resolved_cycles": int(auto_res_cycles),
            "incident_cooldown_seconds": int(inc_cd),
            "dismissed_cooldown_seconds": int(dis_cd),
            "spike_log_cooldown_seconds": int(spk_cd),
            "telegram_notify_ai": _parse_bool("telegram_notify_ai"),
            "telegram_notify_infra": _parse_bool("telegram_notify_infra"),
            "telegram_notify_resolved": _parse_bool("telegram_notify_resolved"),
            "telegram_chat_id": _parse_str("telegram_chat_id"),
            "telegram_l2_chat_id": _parse_str("telegram_l2_chat_id"),
            "telegram_admin_ids": _parse_str("telegram_admin_ids"),
            "ui_poll_interval_ms": int(poll_ms),
        }

        # Validate tùy chọn `service_rules` (luật AlertRule riêng theo từng dịch vụ) trước khi ghi bất kỳ thay đổi nào
        validated_service_ops: List[Tuple[str, Optional[Dict[str, Any]]]] = []
        if "service_rules" in payload and isinstance(payload["service_rules"], dict):
            valid_sids = {s["service_id"] for s in self.services_cache}
            for sid, r_cfg in payload["service_rules"].items():
                sid_s = str(sid).strip()
                if sid_s not in valid_sids:
                    continue
                if r_cfg is None:
                    validated_service_ops.append((sid_s, None))
                else:
                    norm, r_err = self._validate_service_alert_rule(sid_s, r_cfg, updated)
                    if r_err or not norm:
                        return None, r_err
                    norm["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    validated_service_ops.append((sid_s, norm))

        self.system_settings = updated
        for sid_s, norm in validated_service_ops:
            if norm is None:
                self.service_alert_rules.pop(sid_s, None)
                if self.connected and self.db is not None:
                    rid = f"rule_srv_{sid_s}"
                    self._run_async(lambda rid=rid: self.db["AlertRules"].delete_one({"rule_id": rid}))
            else:
                self.service_alert_rules[sid_s] = dict(norm)
                if self.connected and self.db is not None:
                    doc_copy = dict(norm)
                    rid = norm["rule_id"]
                    self._run_async(
                        lambda rid=rid, doc_copy=doc_copy: self.db["AlertRules"].update_one(
                            {"rule_id": rid}, {"$set": doc_copy}, upsert=True
                        )
                    )
        self._sync_rules_cache_from_memory()
        self.update_policy_rule(
            threshold_pct=updated["forecast_threshold_pct"],
            auto_pilot=updated["auto_pilot"],
        )
        return dict(updated), None

    def reset_system_settings(self) -> Dict[str, Any]:
        """Khôi phục thông số vận hành, ngưỡng AI (.pkl) và gỡ toàn bộ AlertRule riêng về mặc định chung."""
        cur = dict(self.system_settings)
        defaults = {
            "auto_pilot": True,
            "m1_threshold_medium": round(float(self.ai_defaults["m1_threshold_medium"]), 4),
            "m1_threshold_high": round(float(self.ai_defaults["m1_threshold_high"]), 4),
            "m1_threshold_critical": round(float(self.ai_defaults["m1_threshold_critical"]), 4),
            "forecast_threshold_pct": round(float(self.ai_defaults["forecast_threshold_pct"]), 1),
            "forecast_critical_pct": round(float(self.ai_defaults["forecast_critical_pct"]), 1),
            "m2_confidence_threshold_pct": round(float(self.ai_defaults["m2_confidence_threshold_pct"]), 1),
            "escalation_l2_seconds": 30,
            "escalation_l3_seconds": 60,
            "l3_auto_mitigate": True,
            "l3_min_confidence_pct": 80.0,
            "auto_resolved_cycles": 2,
            "incident_cooldown_seconds": 12,
            "dismissed_cooldown_seconds": 45,
            "spike_log_cooldown_seconds": 18,
            "telegram_notify_ai": True,
            "telegram_notify_infra": True,
            "telegram_notify_resolved": True,
            "telegram_chat_id": cur.get("telegram_chat_id", ""),
            "telegram_l2_chat_id": cur.get("telegram_l2_chat_id", ""),
            "telegram_admin_ids": cur.get("telegram_admin_ids", ""),
            "ui_poll_interval_ms": 2000,
        }
        self.system_settings = defaults
        self.service_alert_rules.clear()
        self._sync_rules_cache_from_memory()
        if self.connected and self.db is not None:
            self._run_async(
                lambda: self.db["AlertRules"].delete_many(
                    {"$or": [{"rule_type": "SERVICE_AI_POLICY"}, {"rule_id": {"$regex": "^rule_srv_"}}]}
                )
            )
        self.update_policy_rule(
            threshold_pct=defaults["forecast_threshold_pct"],
            auto_pilot=defaults["auto_pilot"],
        )
        return dict(defaults)

    def record_ai_inference_and_incident(
        self,
        service_id: str,
        scenario: str,
        m1_score: float,
        m1_severity: str,
        m2_prob_pct: float,
        m2_forecast_level: str,
        trigger_m3: bool,
        m3_prediction: str,
        m3_confidence: float,
        telemetry_snapshot: Dict[str, Any],
        recommendation: str,
    ) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result_id = f"res_{datetime.now().strftime('%H%M%S_%f')[:11]}"

        ai_doc = {
            "result_id": result_id,
            "service_id": service_id,
            "scenario": scenario,
            "timestamp": now_str,
            "m1_eif_score": round(m1_score, 4),
            "m1_severity_level": m1_severity,
            "m2_forecast_5m_prob": round(m2_prob_pct, 2),
            "m2_forecast_level": m2_forecast_level,
            "policy_trigger_m3": trigger_m3,
            "prediction": m3_prediction,
            "confidence_pct": round(m3_confidence, 2),
            "risk_level": m1_severity,
            "telemetry_snapshot": telemetry_snapshot,
            "recommendation": recommendation,
        }

        self.ai_results_cache.insert(0, ai_doc)
        self.ai_results_cache = self.ai_results_cache[:200]
        self.counts["AIResults"] = self.counts.get("AIResults", 0) + 1

        if self.connected and self.db is not None:
            ai_copy = dict(ai_doc)
            self._run_async(lambda: self.db["AIResults"].insert_one(ai_copy))

        if trigger_m3:
            alert_doc = {
                "alert_id": f"alt_{datetime.now().strftime('%H%M%S_%f')[:11]}",
                "result_id": result_id,
                "service_id": service_id,
                "alert_type": m3_prediction,
                "severity": m1_severity.lower(),
                "message": (
                    f"[{service_id}] Mức hiện tại: {m1_severity} (EIF={m1_score:.3f}), "
                    f"Dự báo 5m: {m2_forecast_level} ({m2_prob_pct:.1f}%), "
                    f"Chẩn đoán RCA: {m3_prediction} ({m3_confidence:.1f}%) — {recommendation}"
                ),
                "created_at": now_str,
                "status": "ACTIVE",
            }
            self.alerts_cache.insert(0, alert_doc)
            self.alerts_cache = self.alerts_cache[:200]
            self.counts["Alerts"] = self.counts.get("Alerts", 0) + 1
            if self.connected and self.db is not None:
                alt_copy = dict(alert_doc)
                self._run_async(lambda: self.db["Alerts"].insert_one(alt_copy))

        return result_id

    def record_infrastructure_alert(
        self,
        alertname: str,
        instance: str,
        severity: str,
        status: str,
        summary: str,
    ) -> Dict[str, Any]:
        """Ghi cảnh báo hạ tầng cứng từ Prometheus/Alertmanager vào bảng Alerts (không chạm AIResults)."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_resolved = str(status).lower() == "resolved"
        alert_doc = {
            "alert_id": f"am_{datetime.now().strftime('%H%M%S_%f')[:11]}",
            "result_id": "alertmanager",
            "service_id": str(instance or "INFRASTRUCTURE"),
            "alert_type": f"Infra:{alertname}",
            "severity": str(severity or "warning").lower(),
            "message": f"[Alertmanager · {'RESOLVED' if is_resolved else 'FIRING'}] {summary}",
            "created_at": now_str,
            "status": "RESOLVED" if is_resolved else "ACTIVE",
        }
        self.alerts_cache.insert(0, alert_doc)
        self.alerts_cache = self.alerts_cache[:200]
        self.counts["Alerts"] = self.counts.get("Alerts", 0) + 1
        if self.connected and self.db is not None:
            alt_copy = dict(alert_doc)
            self._run_async(lambda: self.db["Alerts"].insert_one(alt_copy))
        return alert_doc

    def record_remediation_action(
        self,
        result_id: str,
        service_id: str,
        scenario: str,
        trigger_mode: str,
        fault_class: str,
        confidence: str,
        target_device: str,
        action_text: str,
        status: str,
        actor: str = "SYSTEM"
    ) -> None:
        now_str = datetime.now().strftime("%H:%M:%S")
        rem_doc = {
            "action_id": f"act_{datetime.now().strftime('%H%M%S_%f')[:11]}",
            "result_id": result_id,
            "service_id": service_id,
            "time": now_str,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scenario": scenario,
            "trigger": trigger_mode,
            "fault_class": fault_class,
            "confidence": confidence,
            "target": target_device,
            "action": action_text,
            "status": status,
            "actor": actor, 
        }
        self.remediations_cache.insert(0, rem_doc)
        self.remediations_cache = self.remediations_cache[:200]
        self.counts["RemediationActions"] = self.counts.get("RemediationActions", 0) + 1

        if self.connected and self.db is not None:
            rem_copy = dict(rem_doc)
            self._run_async(lambda: self.db["RemediationActions"].insert_one(rem_copy))

    def get_collection_stats(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "db_name": self.db_name,
            "status_message": self.status_message,
            "counts": dict(self.counts),
        }
