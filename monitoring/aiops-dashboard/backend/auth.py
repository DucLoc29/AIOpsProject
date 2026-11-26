#!/usr/bin/env python3
"""
Module Xác thực Mật khẩu (PBKDF2-HMAC-SHA256), Signed Token (HMAC-SHA256)
và Giới hạn tốc độ đăng nhập (Anti Brute-force).
Thuần thư viện chuẩn Python — không phụ thuộc `python-dotenv` hay package ngoài.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_LOCK = threading.Lock()
# Lưu token đã đăng xuất kèm mốc hết hạn {token: exp_ts} để tự dọn bộ nhớ
_REVOKED_TOKENS: Dict[str, int] = {}
# Lưu lịch sử đăng nhập sai theo IP {ip: [ts1, ts2, ...]}
_FAILED_LOGINS: Dict[str, List[float]] = {}
_FALLBACK_RUNTIME_SECRET = secrets.token_hex(32)

MAX_LOGIN_FAILURES = 15
LOGIN_WINDOW_SECONDS = 60.0


def _load_env_if_needed() -> None:
    if os.environ.get("AUTH_SECRET"):
        return
    if not ENV_FILE.exists():
        return
    try:
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_env_if_needed()


def _get_secret() -> bytes:
    """Lấy AUTH_SECRET từ .env; nếu thiếu dùng khóa ngẫu nhiên theo phiên chạy."""
    _load_env_if_needed()
    secret = os.environ.get("AUTH_SECRET", "").strip() or _FALLBACK_RUNTIME_SECRET
    return secret.encode("utf-8")


# =========================================================================
# 1. BĂM & KIỂM TRA MẬT KHẨU (PBKDF2-HMAC-SHA256)
# =========================================================================
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, expected_hex = stored_hash.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(dk.hex(), expected_hex)


def password_fingerprint(stored_hash: str) -> str:
    """Dấu vân tay ngắn của password_hash để vô hiệu hóa ngay token cũ khi đổi mật khẩu."""
    if not stored_hash:
        return ""
    return hashlib.sha256(stored_hash.encode("utf-8")).hexdigest()[:12]


# =========================================================================
# 2. TẠO, XÁC THỰC & THU HỒI TOKEN (HMAC-SHA256)
# =========================================================================
def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def create_token(user: Dict[str, Any], ttl_seconds: int = 86400) -> str:
    """Tạo token có thời hạn (mặc định 24 giờ = 86400 giây)."""
    payload = {
        "username": user["username"],
        "role": user["role"],
        "full_name": user.get("full_name", user["username"]),
        "pfp": password_fingerprint(str(user.get("password_hash", ""))),
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64_encode(payload_json_bytes)
    signature = hmac.new(_get_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64_encode(signature)}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Kiểm tra chữ ký, hạn dùng và danh sách thu hồi. Hợp lệ -> trả dict payload, sai -> None."""
    if not token or "." not in token:
        return None
    with _LOCK:
        if token in _REVOKED_TOKENS:
            return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(_get_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64_encode(expected_sig), sig_b64):
            return None
        payload = json.loads(_b64_decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def revoke_token(token: str) -> None:
    """Đánh dấu token đã đăng xuất và tự dọn các token quá hạn khỏi RAM."""
    if not token:
        return
    now_i = int(time.time())
    exp_ts = now_i + 86400
    try:
        payload_b64 = token.split(".", 1)[0]
        payload = json.loads(_b64_decode(payload_b64).decode("utf-8"))
        exp_ts = int(payload.get("exp", exp_ts))
    except Exception:
        pass
    with _LOCK:
        expired = [k for k, v in _REVOKED_TOKENS.items() if v < now_i]
        for k in expired:
            _REVOKED_TOKENS.pop(k, None)
        _REVOKED_TOKENS[token] = exp_ts


# =========================================================================
# 3. GIỚI HẠN TỐC ĐỘ ĐĂNG NHẬP (ANTI BRUTE-FORCE THEO IP)
# =========================================================================
def check_login_rate_limit(client_ip: str) -> Tuple[bool, int]:
    """Trả về `(allowed, retry_after_seconds)`."""
    now = time.monotonic()
    with _LOCK:
        hist = [t for t in _FAILED_LOGINS.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _FAILED_LOGINS[client_ip] = hist
        if len(hist) >= MAX_LOGIN_FAILURES:
            retry_after = max(1, int(LOGIN_WINDOW_SECONDS - (now - hist[0])))
            return False, retry_after
    return True, 0


def record_login_failure(client_ip: str) -> None:
    now = time.monotonic()
    with _LOCK:
        hist = [t for t in _FAILED_LOGINS.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        hist.append(now)
        _FAILED_LOGINS[client_ip] = hist


def clear_login_failures(client_ip: str) -> None:
    with _LOCK:
        _FAILED_LOGINS.pop(client_ip, None)


# =========================================================================
# 4. HELPER ĐỌC HEADER TỪ REQUEST
# =========================================================================
def extract_bearer_token(headers: Any) -> str:
    auth_header = headers.get("Authorization", "") if headers else ""
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def get_user_from_headers(headers: Any) -> Optional[Dict[str, Any]]:
    token = extract_bearer_token(headers)
    return verify_token(token)
