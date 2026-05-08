import hashlib
import hmac
import base64
import uuid
import time
import jwt
from datetime import datetime, timedelta

SECRET_KEY = "apihub-secret-key-change-in-production-2025"
TOKEN_EXPIRE_HOURS = 24 * 7  # 7天

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == hashed

def create_token(user_id: str, username: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def generate_api_key() -> str:
    return f"AK-{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex}"
