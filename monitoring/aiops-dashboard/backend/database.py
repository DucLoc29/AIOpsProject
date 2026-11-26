#!/usr/bin/env python3
"""
Tầng Cơ sở dữ liệu MongoDB Atlas (Cloud Database) cho AIOps Command Center.
Hỗ trợ đầy đủ CRUD (Thêm / Sửa / Xóa) cho bảng `Services` đồng bộ trực tiếp lên MongoDB Atlas.

Quản lý 6 Bảng (Collections) chuẩn trong database `AIOpsProject`:
  1. `Services`           : Danh mục các Dịch vụ ứng dụng doanh nghiệp (Hỗ trợ CRUD Thêm / Sửa / Xóa động)
  2. `AlertRules`         : Cấu hình luật cảnh báo & ngưỡng điều phối hệ thống AI (Policy Gate)
  3. `AIResults`          : Kết quả suy luận AI (Model 1 4-mức -> Dự báo 5m 4-mức -> Model 2 RCA) theo từng Service
  4. `Alerts`             : Cảnh báo sự cố chính thức của từng Service
  5. `RemediationActions` : Nhật ký câu lệnh tự động ứng phó khép kín theo IP:Port & Rollback từng dịch vụ
  6. `Users`              : Tài khoản đăng nhập & phân quyền RBAC (`ADMIN` / `VIEWER`, trạng thái `ACTIVE` / `LOCKED`)
"""

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
        "service_id": "ALL_SERVER_SERVICES",
        "metric_name": "m2_forecast_5m_prob",
        "threshold_value": 60.0,
        "comparison_operator": ">=",
        "severity": "critical",
        "auto_remediate": True,
        "is_active": True,
        "description": "Ngưỡng dự báo suy giảm dịch vụ 5 phút tới (Mức HIGH/CRITICAL >= 60%) để kích hoạt Model 2 (RCA)",
    },
    {
        "rule_id": "rule_web_syn_flood",
        "service_id": "srv_web_portal",
        "metric_name": "packet_loss",
        "threshold_value": 5.0,
        "comparison_operator": ">",
        "severity": "critical",
        "auto_remediate": True,
        "is_active": True,
        "description": "Cảnh báo dịch vụ Web Portal (:80) bị SYN Flood -> Chặn gói SYN vào đúng 192.168.10.10:80",
    },
    {
        "rule_id": "rule_api_http_flood",
        "service_id": "srv_flask_api",
        "metric_name": "response_time",
        "threshold_value": 80.0,
        "comparison_operator": ">",
        "severity": "critical",
        "auto_remediate": True,
        "is_active": True,
        "description": "Cảnh báo Flask REST API (:5000) & Web (:80) bị HTTP Flood -> Chặn IP tấn công theo cổng đích",
    },
    {
        "rule_id": "rule_ssh_bruteforce",
        "service_id": "srv_ssh_access",
        "metric_name": "response_time",
        "threshold_value": 60.0,
        "comparison_operator": ">",
        "severity": "critical",
        "auto_remediate": True,
        "is_active": True,
        "description": "Cảnh báo dịch vụ Remote SSH (:22) bị Brute-force -> Chặn riêng cổng :22 tại Firewall",
    },
    {
        "rule_id": "rule_insider_portscan",
        "service_id": "srv_ssh_access",
        "metric_name": "tcp_rst_rate",
        "threshold_value": 30.0,
        "comparison_operator": ">",
        "severity": "high",
        "auto_remediate": True,
        "is_active": True,
        "description": "Cảnh báo quét cổng nội bộ LAN (PortScan) -> Cô lập IP quét theo cổng dịch vụ tại máy chủ",
    },
]

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


def build_default_system_settings() -> Dict[str, Any]:
    """Tham số vận hành mặc định (nạp khởi tạo từ .env, có thể tinh chỉnh nóng trên trang Cài đặt chung)."""
    l2 = _env_int("ESCALATION_L2_SECONDS", 30, 5, 600)
    l3 = max(l2 + 5, _env_int("ESCALATION_L3_SECONDS", 60, 10, 1800))
    return {
        "auto_pilot": True,
        "forecast_threshold_pct": 60.0,
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
    """Quản lý kết nối MongoDB Atlas (6 bảng) kèm CRUD Services & Users + bộ nhớ đệm an toàn."""

    def __init__(self) -> None:
        load_env_file()
        self.mongo_uri = os.environ.get("MONGO_URI", "")
        self.db_name = os.environ.get("MONGO_DB_NAME", "AIOpsProject")
        self.connected = False
        self.status_message = "Chưa kết nối MongoDB"
        self.client: Any = None
        self.db: Any = None

        self.services_cache: List[Dict[str, Any]] = [dict(s) for s in DEFAULT_SERVICES]
        self.rules_cache: List[Dict[str, Any]] = [dict(r) for r in DEFAULT_ALERT_RULES]
        self.ai_results_cache: List[Dict[str, Any]] = []
        self.alerts_cache: List[Dict[str, Any]] = []
        self.remediations_cache: List[Dict[str, Any]] = []
        self.users_cache: List[Dict[str, Any]] = build_default_users()
        self.system_settings: Dict[str, Any] = build_default_system_settings()
        self.counts: Dict[str, int] = {
            "Services": len(self.services_cache),
            "AlertRules": len(self.rules_cache),
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
            for col_name in ("Services", "AlertRules", "AIResults", "Alerts", "RemediationActions", "Users"):
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

            expected_rule_ids = {r["rule_id"] for r in DEFAULT_ALERT_RULES}
            current_rule_ids = {
                doc.get("rule_id") for doc in self.db["AlertRules"].find({}, {"rule_id": 1})
            }
            if not expected_rule_ids.issubset(current_rule_ids):
                self.db["AlertRules"].delete_many({"rule_id": {"$ne": "rule_system_settings"}})
                self.db["AlertRules"].insert_many([dict(r) for r in DEFAULT_ALERT_RULES])

            if self.db["Users"].count_documents({}) == 0:
                self.db["Users"].insert_many([dict(u) for u in self.users_cache])
                print(" -> Đã khởi tạo bảng 'Users' (admin / viewer) trên MongoDB Atlas")
            
            self._refresh_caches()
        except Exception as exc:
            self.connected = False
            self.status_message = f"Lỗi kết nối MongoDB ({type(exc).__name__}) - Chạy Local Fallback"
            print(f"[!] Không thể kết nối MongoDB Atlas: {exc}")

    # =========================================================================
    # CRUD SERVICES (THÊM / SỬA / XÓA DỊCH VỤ ĐỘNG)
    # =========================================================================
    def create_service(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_name = str(payload.get("service_name", "New Service")).strip() or "New Service"
        raw_id = str(payload.get("service_id", "")).strip()
        if not raw_id:
            slug = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")[:18]
            raw_id = f"srv_{slug}_{datetime.now().strftime('%H%M%S')}"

        addr = str(payload.get("address", "192.168.10.10")).strip() or "192.168.10.10"
        port = int(payload.get("port", 8080) or 8080)
        stype = str(payload.get("service_type", "HTTP")).strip().upper() or "HTTP"
        default_host, default_oob = IP_TO_OOB.get(addr, ("service-server", "172.20.20.21"))

        new_doc: Dict[str, Any] = {
            "service_id": raw_id,
            "service_name": raw_name,
            "service_type": stype,
            "host_node": str(payload.get("host_node", default_host)).strip() or default_host,
            "zone": str(payload.get("zone", "HQ_SERVER_FARM (Custom Service)")).strip(),
            "address": addr,
            "oob_ip": str(payload.get("oob_ip", default_oob)).strip() or default_oob,
            "port": port,
            "check_interval": str(payload.get("check_interval", "2s")).strip() or "2s",
            "status": "NORMAL",
            "description": str(
                payload.get("description", f"Dịch vụ {stype} vận hành tại {addr}:{port}")
            ).strip(),
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

        addr = str(payload.get("address", target["address"])).strip() or target["address"]
        default_host, default_oob = IP_TO_OOB.get(
            addr, (target.get("host_node", "service-server"), target.get("oob_ip", "172.20.20.21"))
        )

        updates = {
            "service_name": str(payload.get("service_name", target["service_name"])).strip(),
            "service_type": str(payload.get("service_type", target["service_type"])).strip().upper(),
            "address": addr,
            "port": int(payload.get("port", target["port"]) or target["port"]),
            "host_node": str(payload.get("host_node", default_host)).strip() or default_host,
            "oob_ip": str(payload.get("oob_ip", default_oob)).strip() or default_oob,
            "zone": str(payload.get("zone", target.get("zone", "HQ_SERVER_FARM"))).strip(),
            "check_interval": str(payload.get("check_interval", target.get("check_interval", "2s"))).strip(),
            "description": str(payload.get("description", target.get("description", ""))).strip(),
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
            settings_doc = next((r for r in all_rules if r.get("rule_id") == "rule_system_settings"), None)
            self.rules_cache = [
                r for r in all_rules if r.get("rule_id") != "rule_system_settings"
            ] or [dict(r) for r in DEFAULT_ALERT_RULES]

            merged_settings = build_default_system_settings()
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
            self.ai_results_cache = list(
                self.db["AIResults"].find({}, {"_id": 0}).sort("timestamp", -1).limit(15)
            )
            self.alerts_cache = list(
                self.db["Alerts"].find({}, {"_id": 0}).sort("created_at", -1).limit(15)
            )
            self.remediations_cache = list(
                self.db["RemediationActions"].find({}, {"_id": 0}).sort("timestamp", -1).limit(15)
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
            self.system_settings["forecast_threshold_pct"] = round(float(threshold_pct), 1)
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
                        self.db["AlertRules"].update_one(
                            {"rule_id": "rule_system_settings"},
                            {
                                "$set": {
                                    "rule_id": "rule_system_settings",
                                    "settings": settings_copy,
                                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                            },
                            upsert=True,
                        ),
                    )
                )

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

        forecast_pct, err = _parse_num("forecast_threshold_pct", float, 40.0, 90.0, "Ngưỡng dự báo 5 phút (%)")
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
            "forecast_threshold_pct": round(float(forecast_pct), 1),
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

        self.system_settings = updated
        self.update_policy_rule(
            threshold_pct=updated["forecast_threshold_pct"],
            auto_pilot=updated["auto_pilot"],
        )
        return dict(updated), None

    def reset_system_settings(self) -> Dict[str, Any]:
        """Khôi phục thông số vận hành về mặc định chuẩn đồ án, giữ nguyên kết nối Telegram Chat ID hiện tại."""
        cur = dict(self.system_settings)
        defaults = {
            "auto_pilot": True,
            "forecast_threshold_pct": 60.0,
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
        self.ai_results_cache = self.ai_results_cache[:20]
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
            self.alerts_cache = self.alerts_cache[:20]
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
        self.alerts_cache = self.alerts_cache[:20]
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
        self.remediations_cache = self.remediations_cache[:20]
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
