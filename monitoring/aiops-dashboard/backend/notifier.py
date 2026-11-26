#!/usr/bin/env python3
"""
Telegram ChatOps & Multi-Tier Alert Escalation Module (Pure Python stdlib).

Thiết kế đáp ứng chuẩn vận hành NOC/SOC:
  - Dùng Telegram Bot API Long Polling (`getUpdates?timeout=25`) chạy trên daemon thread riêng:
    KHÔNG cần mở port inbound, KHÔNG cần tên miền hay HTTPS/SSL (chạy tốt trên Local VM, Azure VM và iPhone/Android).
  - Zero-Crash: Nếu `.env` chưa cấu hình `TELEGRAM_BOT_TOKEN` hoặc `TELEGRAM_CHAT_ID`, tự động bỏ qua kênh Telegram.
  - Non-Blocking: Mọi request gửi tin / sửa tin đẩy vào `queue.Queue` xử lý ở worker thread riêng (timeout=4s),
    tuyệt đối KHÔNG bao giờ chặn `ENGINE._lock` hay vòng lặp telemetry 2s.
  - Inline Buttons chống bấm đúp: Phê duyệt / Bỏ qua / Gỡ chặn trực tiếp từ Telegram, đồng bộ 2 chiều với Web
    bằng `editMessageText` để xóa bàn phím cũ ngay sau khi sự cố đã được xử lý.
  - Chính sách Leo thang 4 tầng + Auto-Resolved:
      * Tầng 0 (SUPPRESSED): TransientSpike -> TUYỆT ĐỐI KHÔNG gửi Telegram.
      * Tầng 1 (L1, t=0s): Cảnh báo ngay khi Model 2 chẩn đoán sự cố (kèm nút duyệt nếu Human-in-the-Loop).
      * Tầng 2 (L2, sau ESCALATION_L2_SECONDS): Leo thang khi quá hạn chưa có phản hồi.
      * Tầng 3 (L3, sau ESCALATION_L3_SECONDS): Tự động bảo vệ nếu fault != Unknown_Anomaly & confidence >= 80%
        (Unknown_Anomaly KHÔNG BAO GIỜ tự chặn, chỉ báo kiểm tra thủ công).
      * Auto-Resolved: Gửi tin hồi phục khi dịch vụ quay về NORMAL ổn định >= 2 chu kỳ liên tiếp.
"""

from __future__ import annotations

import html
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_env_file() -> None:
    """Nạp biến môi trường từ PROJECT_ROOT/.env bằng thư viện chuẩn (không ghi đè env sẵn có)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except Exception:
        pass

FAULT_LABELS_VI: Dict[str, str] = {
    "Normal": "Bình thường",
    "SynFlood": "Tấn công SYN Flood",
    "HttpFlood": "Tấn công HTTP Flood",
    "SshBruteforce": "Dò mật khẩu SSH (Brute-force)",
    "PortScan": "Quét cổng mạng (Port Scan)",
    "Unknown_Anomaly": "Bất thường chưa xác định (Unknown_Anomaly)",
    "TransientSpike": "Dao động thoáng qua",
}

SEVERITY_LABELS_VI: Dict[str, str] = {
    "NORMAL": "Bình thường (NORMAL)",
    "MEDIUM": "Trung bình (MEDIUM)",
    "HIGH": "Cao (HIGH)",
    "CRITICAL": "Nghiêm trọng (CRITICAL)",
}


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else "-"), quote=False)


def _fault_vi(fault_class: str) -> str:
    return FAULT_LABELS_VI.get(fault_class, fault_class or "Unknown")


def _parse_int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        val = int(raw) if raw else default
    except ValueError:
        val = default
    return max(minimum, val)


class TelegramChatOpsNotifier:
    """Điều phối gửi thông báo Telegram bất đồng bộ, nhận bấm nút Inline và lệnh ChatOps."""

    def __init__(
        self,
        on_approve: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
        on_dismiss: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
        on_rollback: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
        get_status_summary: Optional[Callable[[], str]] = None,
        get_pending_summary: Optional[Callable[[], str]] = None,
        start_threads: bool = True,
    ) -> None:
        _load_env_file()
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.raw_l2_chat_id = os.environ.get("TELEGRAM_L2_CHAT_ID", "").strip()
        self.l2_chat_id = self.raw_l2_chat_id or self.chat_id
        self.proxy_url = (
            os.environ.get("TELEGRAM_PROXY", "").strip()
            or os.environ.get("HTTPS_PROXY", "").strip()
            or os.environ.get("https_proxy", "").strip()
        )

        self.l2_seconds = _parse_int_env("ESCALATION_L2_SECONDS", 30, 5)
        self.l3_seconds = _parse_int_env("ESCALATION_L3_SECONDS", 60, self.l2_seconds + 5)
        self.notify_ai = True
        self.notify_infra = True
        self.notify_resolved = True

        admin_raw = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
        self.allowed_ids = {
            x.strip()
            for x in admin_raw.split(",")
            if x.strip()
        }
        if self.chat_id:
            self.allowed_ids.add(self.chat_id)
        if self.l2_chat_id:
            self.allowed_ids.add(self.l2_chat_id)

        self.enabled = bool(self.bot_token and self.chat_id)
        self.polling_enabled = bool(self.bot_token)

        self._on_approve = on_approve
        self._on_dismiss = on_dismiss
        self._on_rollback = on_rollback
        self._get_status_summary = get_status_summary
        self._get_pending_summary = get_pending_summary

        self._lock = threading.Lock()
        self._send_queue: "queue.Queue[Callable[[], None]]" = queue.Queue(maxsize=256)
        # Lưu message_id theo service_id để editMessageText đồng bộ 2 chiều Web <-> Telegram
        self._pending_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._rule_messages: Dict[str, List[Dict[str, Any]]] = {}

        self.bot_username: str = ""
        self.sent_count: int = 0
        self.last_sent_at: str = "-"
        self.last_error: str = ""
        self._poll_offset: int = 0
        self._stop_event = threading.Event()

        if self.enabled:
            print(
                f"[✓] Telegram ChatOps đã kích hoạt "
                f"(L2={self.l2_seconds}s, L3={self.l3_seconds}s, Long Polling)"
            )
        elif self.bot_token and not self.chat_id:
            print(
                "[i] Telegram ChatOps: đã có TELEGRAM_BOT_TOKEN nhưng chưa có TELEGRAM_CHAT_ID "
                "(gửi lệnh /id tới bot trên iPhone để lấy Chat ID, sau đó thêm vào .env)"
            )
        else:
            print(
                "[i] Telegram ChatOps: chưa cấu hình TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID -> tạm bỏ qua kênh Telegram"
            )

        if start_threads and self.polling_enabled:
            threading.Thread(target=self._sender_worker, name="tg-sender", daemon=True).start()
            threading.Thread(target=self._polling_worker, name="tg-poller", daemon=True).start()
            self._enqueue(self._fetch_bot_info)

    def stop(self) -> None:
        self._stop_event.set()

    def apply_runtime_settings(self, cfg: Dict[str, Any]) -> None:
        """Áp dụng nóng cài đặt từ trang Cài đặt chung mà không cần khởi động lại server."""
        if not isinstance(cfg, dict):
            return
        with self._lock:
            if "escalation_l2_seconds" in cfg:
                self.l2_seconds = max(5, int(cfg["escalation_l2_seconds"]))
            if "escalation_l3_seconds" in cfg:
                self.l3_seconds = max(self.l2_seconds + 5, int(cfg["escalation_l3_seconds"]))
            if "telegram_notify_ai" in cfg:
                self.notify_ai = bool(cfg["telegram_notify_ai"])
            if "telegram_notify_infra" in cfg:
                self.notify_infra = bool(cfg["telegram_notify_infra"])
            if "telegram_notify_resolved" in cfg:
                self.notify_resolved = bool(cfg["telegram_notify_resolved"])
            if "telegram_chat_id" in cfg:
                self.chat_id = str(cfg["telegram_chat_id"] or "").strip()
            if "telegram_l2_chat_id" in cfg:
                self.raw_l2_chat_id = str(cfg["telegram_l2_chat_id"] or "").strip()
            self.l2_chat_id = self.raw_l2_chat_id or self.chat_id
            if "telegram_admin_ids" in cfg:
                admin_raw = str(cfg["telegram_admin_ids"] or "").strip()
                new_allowed = {x.strip() for x in admin_raw.split(",") if x.strip()}
                if self.chat_id:
                    new_allowed.add(self.chat_id)
                if self.l2_chat_id:
                    new_allowed.add(self.l2_chat_id)
                self.allowed_ids = new_allowed
            else:
                if self.chat_id:
                    self.allowed_ids.add(self.chat_id)
                if self.l2_chat_id:
                    self.allowed_ids.add(self.l2_chat_id)
            self.enabled = bool(self.bot_token and self.chat_id)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "bot_configured": bool(self.bot_token),
                "chat_configured": bool(self.chat_id),
                "l2_separate": bool(self.raw_l2_chat_id and self.raw_l2_chat_id != self.chat_id),
                "l2_seconds": self.l2_seconds,
                "l3_seconds": self.l3_seconds,
                "notify_ai": self.notify_ai,
                "notify_infra": self.notify_infra,
                "notify_resolved": self.notify_resolved,
                "bot_username": self.bot_username,
                "sent_count": self.sent_count,
                "last_sent_at": self.last_sent_at,
                "last_error": self.last_error,
            }

    # =========================================================================
    # Low-level HTTP helpers (stdlib urllib, timeout ngắn, không chặn caller)
    # =========================================================================
    def _build_opener(self) -> urllib.request.OpenerDirector:
        if self.proxy_url:
            handler = urllib.request.ProxyHandler({"http": self.proxy_url, "https": self.proxy_url})
            return urllib.request.build_opener(handler)
        return urllib.request.build_opener()

    def _api_call(
        self, method: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 4.0
    ) -> Optional[Dict[str, Any]]:
        if not self.bot_token:
            return None
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        opener = self._build_opener()
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                if parsed.get("ok"):
                    with self._lock:
                        self.last_error = ""
                    return parsed
                desc = str(parsed.get("description", "Telegram API error"))
                with self._lock:
                    self.last_error = desc
                return None
        except urllib.error.HTTPError as exc:
            err_msg = f"HTTP {exc.code}"
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                if body.get("description"):
                    err_msg = f"HTTP {exc.code}: {body['description']}"
            except Exception:
                pass
            with self._lock:
                self.last_error = err_msg
            return None
        except Exception as exc:
            with self._lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _enqueue(self, fn: Callable[[], None]) -> None:
        if not self.polling_enabled:
            return
        try:
            self._send_queue.put_nowait(fn)
        except queue.Full:
            pass

    def _sender_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                task()
            except Exception as exc:
                with self._lock:
                    self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._send_queue.task_done()

    def _fetch_bot_info(self) -> None:
        res = self._api_call("getMe", {}, timeout=4.0)
        if res and isinstance(res.get("result"), dict):
            uname = str(res["result"].get("username", "")).strip()
            if uname:
                with self._lock:
                    self.bot_username = f"@{uname}"
                print(f"[✓] Telegram Bot sẵn sàng: @{uname}")

    def _send_message_now(
        self,
        chat_id: str,
        text_html: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        if not chat_id or not self.bot_token:
            return None
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = self._api_call("sendMessage", payload, timeout=4.0)
        if res and isinstance(res.get("result"), dict):
            msg_id = res["result"].get("message_id")
            with self._lock:
                self.sent_count += 1
                self.last_sent_at = datetime.now().strftime("%H:%M:%S")
            return int(msg_id) if msg_id is not None else None
        return None

    def _edit_message_now(
        self,
        chat_id: str,
        message_id: int,
        new_text_html: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        else:
            payload["reply_markup"] = {"inline_keyboard": []}
        self._api_call("editMessageText", payload, timeout=4.0)

    def _answer_callback_now(self, callback_id: str, text: str, show_alert: bool = False) -> None:
        self._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text[:190],
                "show_alert": show_alert,
            },
            timeout=4.0,
        )

    # =========================================================================
    # Public non-blocking notification methods (called from app.py)
    # =========================================================================
    def send_test_message(self, actor: str = "admin (Web)") -> Tuple[bool, str]:
        """Gửi 1 tin nhắn kiểm thử đồng bộ (chỉ gọi khi Admin bấm nút Test từ Web, KHÔNG giữ ENGINE._lock)."""
        if not self.enabled:
            return (
                False,
                "Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong file .env",
            )
        now_str = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
        text = (
            "🔔 <b>[AIOps Command Center — Kiểm tra kết nối]</b>\n"
            f"• Thời gian: <code>{_esc(now_str)}</code>\n"
            f"• Thực hiện bởi: <b>{_esc(actor)}</b>\n"
            f"• Chính sách Leo thang: L1 (0s) → L2 ({self.l2_seconds}s) → L3 ({self.l3_seconds}s)\n"
            "• Trạng thái: <b>Kết nối Telegram ChatOps hoạt động ổn định ✅</b>"
        )
        msg_id = self._send_message_now(self.chat_id, text)
        if msg_id is not None:
            return True, f"Đã gửi tin nhắn kiểm thử tới Telegram (message_id={msg_id})"
        with self._lock:
            err = self.last_error or "Không thể kết nối tới api.telegram.org"
        return False, f"Gửi thất bại: {err}"

    def _build_pending_html(
        self,
        header: str,
        service_name: str,
        address: str,
        port: Any,
        severity_level: str,
        fault_class: str,
        confidence: str,
        plan: Dict[str, Any],
        extra_note: str = "",
    ) -> str:
        sev_vi = SEVERITY_LABELS_VI.get(severity_level, severity_level)
        lines = [
            f"<b>{header}</b>",
            f"• <b>Dịch vụ:</b> {_esc(service_name)} (<code>{_esc(address)}:{_esc(port)}</code>)",
            f"• <b>Mức độ:</b> {_esc(sev_vi)}",
            f"• <b>Chẩn đoán AI:</b> {_esc(_fault_vi(fault_class))} (<code>{_esc(confidence)}</code>)",
            f"• <b>IP nguồn truy vết:</b> <code>{_esc(plan.get('attacker_ip', '-'))}</code>",
            f"• <b>Điểm chặn đề xuất:</b> <code>{_esc(plan.get('target_node', '-'))}</code>",
            f"• <b>Lệnh dự kiến:</b>\n<code>{_esc(plan.get('command', '-'))}</code>",
        ]
        if extra_note:
            lines.append(f"• {extra_note}")
        lines.append(f"<i>Thời gian: {datetime.now().strftime('%H:%M:%S')}</i>")
        return "\n".join(lines)

    def notify_pending_l1(self, item: Dict[str, Any]) -> None:
        if not self.enabled or not self.notify_ai:
            return
        sid = str(item["service_id"])
        sev = str(item.get("severity_level", "HIGH")).upper()
        icon = "🔴" if sev == "CRITICAL" else "🟠"
        header = f"{icon} [CẢNH BÁO TẦNG 1 — CHỜ PHÊ DUYỆT]"
        plan = item.get("remediation") or {}
        extra = (
            f"<i>Nếu chưa xử lý: Leo thang L2 sau {self.l2_seconds}s · "
            f"Tự bảo vệ L3 sau {self.l3_seconds}s (độ tin cậy ≥ 80%).</i>"
        )
        text = self._build_pending_html(
            header=header,
            service_name=item.get("service_name", sid),
            address=item.get("address", "-"),
            port=item.get("port", "-"),
            severity_level=sev,
            fault_class=str(item.get("fault_class", "Unknown")),
            confidence=str(item.get("confidence", "-")),
            plan=plan,
            extra_note=extra,
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "✅ Phê duyệt & Chặn ngay", "callback_data": f"approve:{sid}"},
                    {"text": "❌ Bỏ qua", "callback_data": f"dismiss:{sid}"},
                ]
            ]
        }

        def _job() -> None:
            msg_id = self._send_message_now(self.chat_id, text, reply_markup=kb)
            if msg_id is not None:
                with self._lock:
                    self._pending_messages.setdefault(sid, []).append(
                        {"chat_id": self.chat_id, "message_id": msg_id, "text": text}
                    )

        self._enqueue(_job)

    def notify_escalation_l2(self, item: Dict[str, Any], elapsed_s: int) -> None:
        if not self.enabled or not self.notify_ai:
            return
        sid = str(item["service_id"])
        sev = str(item.get("severity_level", "HIGH")).upper()
        plan = item.get("remediation") or {}
        header = f"⚠️ [LEO THANG TẦNG 2 — CHƯA XỬ LÝ SAU {elapsed_s}s]"
        extra = (
            f"<i>Quản trị viên Tầng 1 chưa phản hồi! Tự động bảo vệ Tầng 3 sẽ kích hoạt sau "
            f"{max(1, self.l3_seconds - elapsed_s)}s nữa nếu sự cố tiếp diễn.</i>"
        )
        text = self._build_pending_html(
            header=header,
            service_name=item.get("service_name", sid),
            address=item.get("address", "-"),
            port=item.get("port", "-"),
            severity_level=sev,
            fault_class=str(item.get("fault_class", "Unknown")),
            confidence=str(item.get("confidence", "-")),
            plan=plan,
            extra_note=extra,
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "✅ Phê duyệt & Chặn ngay", "callback_data": f"approve:{sid}"},
                    {"text": "❌ Bỏ qua", "callback_data": f"dismiss:{sid}"},
                ]
            ]
        }
        target_chat = self.l2_chat_id or self.chat_id

        def _job() -> None:
            msg_id = self._send_message_now(target_chat, text, reply_markup=kb)
            if msg_id is not None:
                with self._lock:
                    self._pending_messages.setdefault(sid, []).append(
                        {"chat_id": target_chat, "message_id": msg_id, "text": text}
                    )

        self._enqueue(_job)

    def notify_remediated(
        self,
        service: Dict[str, Any],
        plan: Dict[str, Any],
        conf_str: str,
        mode: str,
        actor: str,
        exec_status: str = "EXECUTED",
    ) -> None:
        if not self.enabled or not self.notify_ai:
            return
        sid = str(service["service_id"])
        if mode == "ESCALATION-L3":
            header = "🚨 [LEO THANG TẦNG 3 — ĐÃ TỰ ĐỘNG BẢO VỆ]"
        elif mode == "AUTO-PILOT":
            header = "🛡️ [AUTO-PILOT — ĐÃ TỰ ĐỘNG CHẶN]"
        else:
            header = "✅ [ĐÃ PHÊ DUYỆT & THỰC THI CHẶN]"

        text = "\n".join(
            [
                f"<b>{header}</b>",
                f"• <b>Dịch vụ:</b> {_esc(service.get('service_name', sid))} "
                f"(<code>{_esc(service.get('address', '-'))}:{_esc(service.get('port', '-'))}</code>)",
                f"• <b>Nguyên nhân:</b> {_esc(_fault_vi(plan.get('fault_class', '')))} (<code>{_esc(conf_str)}</code>)",
                f"• <b>IP đã chặn:</b> <code>{_esc(plan.get('attacker_ip', '-'))}</code>",
                f"• <b>Điểm chặn:</b> <code>{_esc(plan.get('target_node', '-'))}</code>",
                f"• <b>Lệnh thực thi:</b>\n<code>{_esc(plan.get('command', '-'))}</code>",
                f"• <b>Kết quả:</b> <code>{_esc(exec_status)}</code> · <b>Thực hiện bởi:</b> {_esc(actor)}",
                f"<i>Thời gian: {datetime.now().strftime('%H:%M:%S')}</i>",
            ]
        )
        kb = {
            "inline_keyboard": [
                [{"text": "↩️ Gỡ chặn (Rollback)", "callback_data": f"rollback:{sid}"}]
            ]
        }
        target_chat = self.l2_chat_id if mode == "ESCALATION-L3" else self.chat_id

        def _job() -> None:
            msg_id = self._send_message_now(target_chat, text, reply_markup=kb)
            if msg_id is not None:
                with self._lock:
                    self._rule_messages.setdefault(sid, []).append(
                        {"chat_id": target_chat, "message_id": msg_id, "text": text}
                    )

        self._enqueue(_job)

    def notify_unknown_anomaly(
        self,
        service: Dict[str, Any],
        severity_level: str,
        confidence: str,
        title: str,
        tier: str = "L1",
    ) -> None:
        """Cảnh báo Unknown_Anomaly (KHÔNG BAO GIỜ tự chặn, kể cả ở Tầng 3)."""
        if not self.enabled or not self.notify_ai:
            return
        if tier == "L3":
            header = "🚨 [LEO THANG TẦNG 3 — YÊU CẦU KIỂM TRA THỦ CÔNG]"
            target_chat = self.l2_chat_id or self.chat_id
        elif tier == "L2":
            header = "⚠️ [LEO THANG TẦNG 2 — BẤT THƯỜNG CHƯA XÁC ĐỊNH]"
            target_chat = self.l2_chat_id or self.chat_id
        else:
            header = "⚠️ [CẢNH BÁO — BẤT THƯỜNG CHƯA XÁC ĐỊNH]"
            target_chat = self.chat_id

        sev_vi = SEVERITY_LABELS_VI.get(severity_level, severity_level)
        text = "\n".join(
            [
                f"<b>{header}</b>",
                f"• <b>Dịch vụ:</b> {_esc(service.get('service_name', service.get('service_id')))} "
                f"(<code>{_esc(service.get('address', '-'))}:{_esc(service.get('port', '-'))}</code>)",
                f"• <b>Mức độ:</b> {_esc(sev_vi)}",
                f"• <b>Chẩn đoán:</b> Unknown_Anomaly (<code>{_esc(confidence)}</code>)",
                f"• <b>Chi tiết:</b> {_esc(title)}",
                "• <b>Quy tắc an toàn:</b> <i>Hệ thống KHÔNG tự chèn luật iptables. Vui lòng kiểm tra tiến trình dịch vụ / hạ tầng thủ công!</i>",
                f"<i>Thời gian: {datetime.now().strftime('%H:%M:%S')}</i>",
            ]
        )
        self._enqueue(lambda: self._send_message_now(target_chat, text))

    def notify_auto_resolved(
        self,
        service: Dict[str, Any],
        prev_fault: str,
        metrics: Dict[str, Any],
    ) -> None:
        if not self.enabled or not self.notify_resolved:
            return
        text = "\n".join(
            [
                "<b>🟢 [ĐÃ HỒI PHỤC — NORMAL]</b>",
                f"• <b>Dịch vụ:</b> {_esc(service.get('service_name', service.get('service_id')))} "
                f"(<code>{_esc(service.get('address', '-'))}:{_esc(service.get('port', '-'))}</code>)",
                f"• <b>Sự cố trước đó:</b> {_esc(_fault_vi(prev_fault))}",
                f"• <b>SLA hiện tại:</b> Latency <code>{float(metrics.get('latency', 0.0)):.1f}ms</code> · "
                f"Loss <code>{float(metrics.get('packet_loss', 0.0)):.1f}%</code> · "
                f"Uptime <code>{float(metrics.get('uptime', 100.0)):.1f}%</code>",
                "• <b>Trạng thái:</b> Dịch vụ đã ổn định về mức Bình thường (≥ 2 chu kỳ liên tiếp).",
                f"<i>Thời gian: {datetime.now().strftime('%H:%M:%S')}</i>",
            ]
        )
        self._enqueue(lambda: self._send_message_now(self.chat_id, text))

    def notify_infra_alert(
        self,
        alertname: str,
        instance: str,
        severity: str,
        status: str,
        summary: str,
    ) -> None:
        """Thông báo cảnh báo hạ tầng cứng từ Prometheus/Alertmanager (chỉ báo, KHÔNG chạy iptables)."""
        if not self.enabled or not self.notify_infra:
            return
        is_resolved = str(status).lower() == "resolved"
        header = (
            "🟢 [HẠ TẦNG ĐÃ HỒI PHỤC — ALERTMANAGER]"
            if is_resolved
            else "🔥 [CẢNH BÁO HẠ TẦNG — ALERTMANAGER]"
        )
        text = "\n".join(
            [
                f"<b>{header}</b>",
                f"• <b>Luật cảnh báo:</b> <code>{_esc(alertname)}</code>",
                f"• <b>Thiết bị / Instance:</b> <code>{_esc(instance)}</code>",
                f"• <b>Mức độ:</b> <code>{_esc(severity.upper())}</code>",
                f"• <b>Nội dung:</b> {_esc(summary)}",
                f"<i>Thời gian: {datetime.now().strftime('%H:%M:%S')}</i>",
            ]
        )
        self._enqueue(lambda: self._send_message_now(self.chat_id, text))

    def sync_pending_resolved(self, service_id: str, resolution_note: str) -> None:
        """Gỡ nút Inline trên toàn bộ tin nhắn L1/L2 cũ của service_id (đồng bộ Web <-> Telegram)."""
        if not self.enabled:
            return
        with self._lock:
            msgs = self._pending_messages.pop(service_id, [])
        if not msgs:
            return

        def _job() -> None:
            for item in msgs:
                updated = f"{item['text']}\n\n<b>{ _esc(resolution_note) }</b>"
                self._edit_message_now(item["chat_id"], item["message_id"], updated)

        self._enqueue(_job)

    def sync_rule_rolled_back(self, service_id: Optional[str], actor: str) -> None:
        """Gỡ nút Rollback trên tin nhắn Telegram khi luật đã được gỡ từ Web hoặc Telegram."""
        if not self.enabled:
            return
        now_str = datetime.now().strftime("%H:%M:%S")
        note = f"↩️ Đã gỡ chặn (Rollback) bởi {actor} lúc {now_str}"
        with self._lock:
            if service_id:
                msgs = self._rule_messages.pop(service_id, [])
            else:
                msgs = [m for lst in self._rule_messages.values() for m in lst]
                self._rule_messages.clear()
        if not msgs:
            return

        def _job() -> None:
            for item in msgs:
                updated = f"{item['text']}\n\n<b>{_esc(note)}</b>"
                self._edit_message_now(item["chat_id"], item["message_id"], updated)

        self._enqueue(_job)

    # =========================================================================
    # Inbound Long Polling (`getUpdates`) & Interactive Callback Handler
    # =========================================================================
    def _is_authorized(self, from_id: Any, chat_id: Any) -> bool:
        fid = str(from_id or "").strip()
        cid = str(chat_id or "").strip()
        if not self.allowed_ids:
            return False
        return (fid in self.allowed_ids) or (cid in self.allowed_ids)

    def _polling_worker(self) -> None:
        # Bỏ qua các update cũ tích tồn trước khi server khởi động
        init = self._api_call("getUpdates", {"timeout": 0, "limit": 50}, timeout=5.0)
        if init and isinstance(init.get("result"), list) and init["result"]:
            self._poll_offset = int(init["result"][-1]["update_id"]) + 1

        while not self._stop_event.is_set():
            res = self._api_call(
                "getUpdates",
                {
                    "offset": self._poll_offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=32.0,
            )
            if not res or not isinstance(res.get("result"), list):
                time.sleep(3.0)
                continue

            for upd in res["result"]:
                uid = int(upd.get("update_id", 0))
                if uid >= self._poll_offset:
                    self._poll_offset = uid + 1
                try:
                    if "callback_query" in upd:
                        self._handle_callback_query(upd["callback_query"])
                    elif "message" in upd:
                        self._handle_command_message(upd["message"])
                except Exception as exc:
                    with self._lock:
                        self.last_error = f"Update handler: {exc}"

    def _format_actor(self, from_user: Dict[str, Any]) -> str:
        uname = str(from_user.get("username", "")).strip()
        if uname:
            return f"Telegram (@{uname})"
        first = str(from_user.get("first_name", "")).strip()
        uid = from_user.get("id", "")
        return f"Telegram ({first or uid})"

    def _handle_callback_query(self, cb: Dict[str, Any]) -> None:
        cb_id = str(cb.get("id", ""))
        from_user = cb.get("from") or {}
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        from_id = str(from_user.get("id", ""))
        data = str(cb.get("data", "")).strip()

        if not self._is_authorized(from_id, chat_id):
            self._answer_callback_now(cb_id, "⛔ Bạn không có quyền thao tác hệ thống AIOps.", show_alert=True)
            return

        actor = self._format_actor(from_user)
        now_str = datetime.now().strftime("%H:%M:%S")
        action, _, sid = data.partition(":")

        if action == "approve" and sid and self._on_approve:
            ok, detail = self._on_approve(sid, actor)
            self._answer_callback_now(cb_id, detail, show_alert=not ok)
            if ok:
                self.sync_pending_resolved(sid, f"✅ Đã phê duyệt & chặn bởi {actor} lúc {now_str}")
            elif msg.get("message_id"):
                orig = str(msg.get("text", ""))
                self._edit_message_now(
                    chat_id,
                    int(msg["message_id"]),
                    f"{_esc(orig)}\n\n<i>{_esc(detail)}</i>",
                )
        elif action == "dismiss" and sid and self._on_dismiss:
            ok, detail = self._on_dismiss(sid, actor)
            self._answer_callback_now(cb_id, detail, show_alert=not ok)
            if ok:
                self.sync_pending_resolved(sid, f"❌ Đã bỏ qua cảnh báo bởi {actor} lúc {now_str}")
        elif action == "rollback" and sid and self._on_rollback:
            ok, detail = self._on_rollback(sid, actor)
            self._answer_callback_now(cb_id, detail, show_alert=not ok)
            if ok:
                self.sync_rule_rolled_back(sid, actor)
        else:
            self._answer_callback_now(cb_id, "Thao tác không hợp lệ hoặc đã hết hạn.")

    def _handle_command_message(self, msg: Dict[str, Any]) -> None:
        text = str(msg.get("text", "")).strip()
        if not text.startswith("/"):
            return
        cmd = text.split()[0].split("@")[0].lower()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        from_id = str((msg.get("from") or {}).get("id", ""))

        # Lệnh /id cho phép lấy Chat ID nhanh khi mới thiết lập bot trên iPhone
        if cmd in ("/id", "/start") and (not self.allowed_ids or self._is_authorized(from_id, chat_id)):
            reply = (
                "🤖 <b>AIOps Command Center Bot</b>\n"
                f"• <b>Chat ID của bạn:</b> <code>{_esc(chat_id)}</code>\n"
                f"• <b>User ID của bạn:</b> <code>{_esc(from_id)}</code>\n"
                "Dùng giá trị này cho <code>TELEGRAM_CHAT_ID</code> và <code>TELEGRAM_ADMIN_IDS</code> trong <code>.env</code>.\n"
                "Gõ /status để xem tình trạng các dịch vụ hoặc /help để xem hướng dẫn."
            )
            self._send_message_now(chat_id, reply)
            return

        if not self._is_authorized(from_id, chat_id):
            return

        if cmd == "/status" and self._get_status_summary:
            self._send_message_now(chat_id, self._get_status_summary())
        elif cmd == "/pending" and self._get_pending_summary:
            self._send_message_now(chat_id, self._get_pending_summary())
        elif cmd == "/help":
            help_txt = (
                "📘 <b>Lệnh AIOps ChatOps</b>\n"
                "• <code>/status</code> — Xem sức khỏe thời gian thực của tất cả dịch vụ\n"
                "• <code>/pending</code> — Xem danh sách sự cố đang chờ phê duyệt\n"
                "• <code>/id</code> — Hiển thị Chat ID và User ID hiện tại\n"
                "• Khi có sự cố, bạn có thể bấm trực tiếp nút <b>Phê duyệt</b>, <b>Bỏ qua</b> hoặc <b>Gỡ chặn</b> ngay trên tin nhắn."
            )
            self._send_message_now(chat_id, help_txt)
