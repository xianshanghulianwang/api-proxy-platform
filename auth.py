"""
终点平台 - 认证模块
"""
import hashlib
import uuid
import time
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database import get_db

# 邮件配置（需要从后台配置获取或硬编码）
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 587
SMTP_USER = "your_email@qq.com"  # 需要配置
SMTP_PASS = "your_auth_code"  # 需要配置授权码

def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def verify_password(pwd: str, hashed: str) -> bool:
    return hashlib.sha256(pwd.encode()).hexdigest() == hashed

def create_token(user_id: str, username: str, role: str = "user") -> str:
    import base64, json as _json
    payload = _json.dumps({
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": time.time() + 86400 * 7
    })
    return base64.b64encode(payload.encode()).decode()

def verify_token(token: str) -> dict:
    import base64, json as _json
    try:
        payload = _json.loads(base64.b64decode(token.encode()).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except:
        return None

def generate_api_key() -> str:
    return f"sk_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"

def generate_referral_code() -> str:
    return f"ZD{uuid.uuid4().hex[:8].upper()}"

def generate_email_code() -> str:
    """生成6位数字验证码"""
    return ''.join(random.choices(string.digits, k=6))

def send_email_code(email: str, code: str, purpose: str = "注册验证") -> bool:
    """发送邮箱验证码"""
    try:
        msg = MIMEMultipart()
        msg['From'] = f"终点平台 <{SMTP_USER}>"
        msg['To'] = email
        msg['Subject'] = f"【终点】{purpose}验证码"
        
        html = f"""
        <html>
        <body style="font-family: 'Microsoft YaHei', Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 16px 16px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 24px;">终点平台</h1>
            </div>
            <div style="background: #fff; padding: 30px; border: 1px solid #e0e0e0; border-top: none; border-radius: 0 0 16px 16px;">
                <p style="font-size: 16px; color: #333;">您好，</p>
                <p style="font-size: 16px; color: #333;">您的验证码是：</p>
                <div style="background: #f5f5f5; padding: 20px; text-align: center; margin: 20px 0; border-radius: 8px;">
                    <span style="font-size: 32px; font-weight: bold; color: #667eea; letter-spacing: 8px;">{code}</span>
                </div>
                <p style="font-size: 14px; color: #666;">验证码有效期10分钟，请勿告知他人。</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 12px; color: #999;">此邮件由系统自动发送，请勿回复。</p>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False

def save_email_code(email: str, code: str, purpose: str = "register") -> bool:
    """保存邮箱验证码到数据库"""
    from datetime import datetime, timedelta
    conn = get_db()
    cur = conn.cursor()
    
    # 先标记旧验证码为已使用
    cur.execute("UPDATE email_codes SET used=1 WHERE email=? AND purpose=?", (email, purpose))
    
    # 添加新验证码
    expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    cur.execute(
        "INSERT INTO email_codes (email, code, purpose, expires_at, created_at) VALUES (?,?,?,?,?)",
        (email, code, purpose, expires_at, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return True

def verify_email_code(email: str, code: str, purpose: str = "register") -> bool:
    """验证邮箱验证码"""
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT * FROM email_codes WHERE email=? AND code=? AND purpose=? AND used=0 AND expires_at>?",
        (email, code, purpose, datetime.now().isoformat())
    )
    record = cur.fetchone()
    
    if record:
        cur.execute("UPDATE email_codes SET used=1 WHERE id=?", (record["id"],))
        conn.commit()
        conn.close()
        return True
    
    conn.close()
    return False

def check_email_code(email: str, code: str, purpose: str = "register") -> bool:
    """仅检查验证码是否正确（不标记为已使用）"""
    from datetime import datetime
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT * FROM email_codes WHERE email=? AND code=? AND purpose=? AND used=0 AND expires_at>?",
        (email, code, purpose, datetime.now().isoformat())
    )
    record = cur.fetchone()
    conn.close()
    return record is not None
