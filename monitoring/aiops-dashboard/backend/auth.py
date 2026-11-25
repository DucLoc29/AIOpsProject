#!/usr/bin/env python3
"""Module xác thực mật khẩu (PBKDF2) & Signed Token (HMAC-SHA256)"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from dotenv import load_dotenv
load_dotenv()

# Danh sách token đã đăng xuất (lưu trên RAM)
_REVOKED_TOKENS = set()

def _get_secret() -> bytes:
    """Lấy chuỗi bí mật AUTH_SECRET từ .env (chuyển sang kiểu bytes để ký HMAC)."""
    secret = os.environ.get("AUTH_SECRET", "dev-fallback-secret-change-me")
    return secret.encode("utf-8")

# =========================================================================
# 1. BĂM & KIỂM TRA MẬT KHẨU (Tương đương bcrypt.hash / bcrypt.compare)
# =========================================================================
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", 
        password.encode("utf-8"), 
        salt.encode("utf-8"), 
        100_000
    )
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, expected_hex = stored_hash.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(dk.hex(), expected_hex)


# =========================================================================
# 2. TẠO, XÁC THỰC & HỦY TOKEN (Tương đương jwt.sign / jwt.verify)
# =========================================================================
def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)    
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def create_token(user: dict, ttl_seconds: int = 86400) -> str:
    """Tạo token có thời hạn (mặc định 24 giờ = 86400 giây)."""
    payload = {
        "username": user["username"],
        "role": user["role"],
        "full_name": user.get("full_name", user["username"]),
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64_encode(payload_json_bytes)
    signature = hmac.new(
        _get_secret(), 
        payload_b64.encode("ascii"), 
        hashlib.sha256
    ).digest()

    return f"{payload_b64}.{_b64_encode(signature)}"


def verify_token(token: str):
    """ 
        Kiểm tra chữ ký, hạn dùng và danh sách đăng xuất. 
        Hợp lệ -> trả dict user, sai -> None.
    """
    if not token or token in _REVOKED_TOKENS or "." not in token:
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
    """Đánh dấu token đã đăng xuất."""
    if token:
        _REVOKED_TOKENS.add(token)


# =========================================================================
# 3. HELPER ĐỌC HEADER TỪ REQUEST (Dùng cho app.py)
# =========================================================================
def extract_bearer_token(headers) -> str:
    if headers:
        auth_header = headers.get("Authorization", "")
    else:
        auth_header = ""

    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    
    return ""


def get_user_from_headers(headers):
    """Tương đương middleware authenticateToken trong Express."""
    token = extract_bearer_token(headers)
    return verify_token(token)