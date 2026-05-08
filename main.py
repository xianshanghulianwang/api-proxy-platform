"""
API中转站 - FastAPI 主程序
"""
import os
import sys
import json
import time
import uuid
import httpx
import hashlib
import logging
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 本地模块
from database import get_db, init_db
from models import *
from auth import hash_password, verify_password, create_token, verify_token, generate_api_key
from alipay import generate_alipay_url

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="API中转站", version="1.0.0")

# ===== 辅助函数 =====

def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("token")
    if not token:
        return None
    return verify_token(token)

def require_auth(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user

def render(template: str, context: dict, request: Request):
    user = get_current_user(request)
    context["user"] = user
    from fastapi.responses import TemplateResponse
    return TemplateResponse(template, context, request=request)

# ===== 代理系统辅助函数 =====

def is_agent(user_id: str) -> bool:
    """检查用户是否是代理"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM agents WHERE user_id=? AND status='active'", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def get_agent_by_user(user_id: str) -> Optional[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_agent_config() -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM config WHERE key LIKE 'agent_%' OR key='agent_enabled'")
    rows = cur.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def process_agent_commission(user_id: str, order_id: str, recharge_amount: float):
    """当用户充值时，给上级代理计算佣金"""
    conn = get_db()
    cur = conn.cursor()
    # 查找该用户的上级代理
    cur.execute("SELECT * FROM agents WHERE user_id=? AND status='active'", (user_id,))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return
    # 获取佣金比例
    cfg = get_agent_config()
    rate = float(cfg.get("agent_default_commission_rate", "0.10"))
    commission = round(recharge_amount * rate, 4)
    if commission <= 0:
        conn.close()
        return
    # 记录佣金
    cur.execute(
        "INSERT INTO agent_commissions (agent_id, user_id, order_id, recharge_amount, commission, level, created_at) VALUES (?,?,?,?,?,?,?)",
        (agent["id"], user_id, order_id, recharge_amount, commission, 1, datetime.now().isoformat())
    )
    # 更新代理总佣金和下线数
    cur.execute(
        "UPDATE agents SET total_commission=total_commission+?, total_downlines=total_downlines+1 WHERE id=?",
        (commission, agent["id"])
    )
    # 给代理账户加余额
    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (commission, agent["user_id"]))
    conn.commit()
    conn.close()
    logger.info(f"[代理佣金] 用户{user_id}充值{recharge_amount}元，代理{agent['id']}获得佣金{commission}元")

def generate_referral_code() -> str:
    """生成唯一推荐码"""
    return f"AGT{uuid.uuid4().hex[:8].upper()}"

# ===== 静态文件和模板 =====
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ===== 页面路由 =====

@app.get("/", response_class=HTMLResponse)
async def home():
    return Path(TEMPLATES_DIR / "index.html").read_text()

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return Path(TEMPLATES_DIR / "login.html").read_text()

@app.get("/register", response_class=HTMLResponse)
async def register_page():
    return Path(TEMPLATES_DIR / "register.html").read_text()

@app.get("/dashboard")
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    return Path(TEMPLATES_DIR / "dashboard.html").read_text()

@app.get("/pricing")
async def pricing(request: Request):
    return Path(TEMPLATES_DIR / "pricing.html").read_text()

@app.get("/admin")
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse("/login")
    return Path(TEMPLATES_DIR / "admin.html").read_text()

@app.get("/user/api-keys")
async def api_keys_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    return Path(TEMPLATES_DIR / "api_keys.html").read_text()

@app.get("/docs")
async def docs_page(request: Request):
    return Path(TEMPLATES_DIR / "docs.html").read_text()

# ===== API 路由 =====

# --- 认证 ---
@app.post("/api/auth/register")
async def api_register(username: str = Form(), password: str = Form(),
                       email: str = Form(""), phone: str = Form("")):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=?", (username,))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"code": 1, "msg": "用户名已存在"})

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(password)
    api_key = generate_api_key()
    now = datetime.now().isoformat()

    cur.execute(
        "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, username, pw_hash, email, phone, 0, "user", api_key, 0, now, None)
    )
    conn.commit()
    conn.close()

    token = create_token(user_id, username, "user")
    resp = JSONResponse({"code": 0, "msg": "注册成功", "data": {"username": username}})
    resp.set_cookie("token", token, httponly=True, max_age=3600*24*7)
    return resp

@app.post("/api/auth/login")
async def api_login(username: str = Form(), password: str = Form()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row or not verify_password(password, row["password_hash"]):
        return JSONResponse({"code": 1, "msg": "用户名或密码错误"})

    # 更新最后登录
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(), row["id"]))
    conn.commit()
    conn.close()

    token = create_token(row["id"], row["username"], row["role"])
    resp = JSONResponse({"code": 0, "msg": "登录成功", "data": {
        "username": row["username"],
        "role": row["role"],
        "api_key": row["api_key"]
    }})
    resp.set_cookie("token", token, httponly=True, max_age=3600*24*7)
    return resp

@app.post("/api/auth/logout")
async def api_logout():
    resp = JSONResponse({"code": 0, "msg": "已退出"})
    resp.delete_cookie("token")
    return resp

@app.get("/api/user/info")
async def api_user_info(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 1, "msg": "未登录"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user["user_id"],))
    row = cur.fetchone()
    conn.close()

    if not row:
        return JSONResponse({"code": 1, "msg": "用户不存在"})

    return JSONResponse({"code": 0, "data": {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "phone": row["phone"],
        "balance": row["balance"],
        "api_key": row["api_key"],
        "api_quota": row["api_quota"],
        "role": row["role"],
        "created_at": row["created_at"]
    }})

# --- 套餐 ---
@app.get("/api/packages")
async def api_packages():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM packages WHERE is_active=1 ORDER BY price ASC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

# --- 订单 ---
@app.post("/api/order/create")
async def api_create_order(package_id: str = Form(), request: Request = None):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "请先登录"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM packages WHERE id=? AND is_active=1", (package_id,))
    pkg = cur.fetchone()
    if not pkg:
        conn.close()
        return JSONResponse({"code": 1, "msg": "套餐不存在"})

    order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now().isoformat()

    cur.execute(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
        (order_id, user["user_id"], package_id, pkg["price"], "pending", "", "", now, None)
    )
    conn.commit()
    conn.close()

    # 生成支付链接（支付宝）
    pay_url = generate_alipay_url(order_id, pkg["price"], f"API中转站-{pkg['name']}")
    if not pay_url:
        pay_url = f"/payment/mock?order_id={order_id}&amount={pkg['price']}"

    return JSONResponse({"code": 0, "data": {
        "order_id": order_id,
        "amount": pkg["price"],
        "pay_url": pay_url
    }})

# 模拟支付（开发测试用）
@app.get("/payment/mock")
async def mock_payment(order_id: str, amount: float, request: Request):
    user = get_current_user(request)
    if not user:
        return HTMLResponse("<h2>请先登录</h2><a href='/login'>去登录</a>")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()

    if order and order["status"] == "pending":
        # 标记已支付
        cur.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?",
                    (datetime.now().isoformat(), order_id))
        package_id_val = order["package_id"]
        recharge_amount = order["amount"]
        order_remark = order.get("remark", "") or ""

        if package_id_val:
            # 套餐购买：加余额+额度
            cur.execute("SELECT * FROM packages WHERE id=?", (package_id_val,))
            pkg = cur.fetchone()
            if pkg and user["user_id"]:
                cur.execute("UPDATE users SET balance=balance+?, api_quota=api_quota+? WHERE id=?",
                            (pkg["price"], pkg["credits"], user["user_id"]))
                if pkg["price"] > 0:
                    process_agent_commission(user["user_id"], order_id, pkg["price"])
        elif "代理入驻费" in order_remark:
            # 代理入驻：创建代理记录
            agent_id = f"AGT{uuid.uuid4().hex[:12].upper()}"
            referral_code = generate_referral_code()
            cur.execute(
                "INSERT OR IGNORE INTO agents (id, user_id, referral_code, commission_rate, status, created_at) VALUES (?,?,?,?,?,?)",
                (agent_id, user["user_id"], referral_code, 0.10, "active", datetime.now().isoformat())
            )
            logger.info(f"[代理入驻] 用户{user['user_id']}成为代理，ID={agent_id}，推荐码={referral_code}")
        else:
            # 余额充值：直接加余额
            if user["user_id"]:
                cur.execute("UPDATE users SET balance=balance+? WHERE id=?",
                            (recharge_amount, user["user_id"]))
                if recharge_amount > 0:
                    process_agent_commission(user["user_id"], order_id, recharge_amount)
    conn.commit()
    conn.close()

    return HTMLResponse("""
    <html><head><meta charset="utf-8"><style>
        body { font-family: 'PingFang SC', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f0f9f4; }
        .box { text-align: center; background: white; padding: 48px 64px; border-radius: 24px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }
        .icon { font-size: 64px; margin-bottom: 16px; }
        h2 { color: #10B981; margin: 0 0 12px; }
        p { color: #666; margin: 0 0 24px; }
        a { background: #0A6E6E; color: white; padding: 12px 32px; border-radius: 24px; text-decoration: none; font-weight: 600; }
    </style></head><body>
    <div class="box">
        <div class="icon">✅</div>
        <h2>支付成功！</h2>
        <p>您的账户已充值，现在可以开始使用API服务了</p>
        <a href="/dashboard">去使用 →</a>
    </div>
    </body></html>
    """)

# 支付宝异步回调
@app.post("/api/alipay/notify")
async def alipay_notify(request: Request):
    try:
        form = await request.form()
        params = dict(form)
        trade_status = params.get("trade_status", "")

        if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            order_id = params.get("out_trade_no")
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE id=? AND status='pending'", (order_id,))
            order = cur.fetchone()
            if order:
                cur.execute("UPDATE orders SET status='paid', alipay_trade_no=?, paid_at=? WHERE id=?",
                            (params.get("trade_no", ""), datetime.now().isoformat(), order_id))
                cur.execute("SELECT * FROM packages WHERE id=?", (order["package_id"],))
                pkg = cur.fetchone()
                if pkg:
                    cur.execute("UPDATE users SET balance=balance+?, api_quota=api_quota+? WHERE id=?",
                                (pkg["price"], pkg["credits"], order["user_id"]))
                    # 代理佣金
                    if pkg["price"] > 0:
                        process_agent_commission(order["user_id"], order_id, pkg["price"])
                elif "代理入驻费" in (order.get("remark") or ""):
                    # 代理入驻：创建代理记录
                    agent_id = f"AGT{uuid.uuid4().hex[:12].upper()}"
                    referral_code = generate_referral_code()
                    cur.execute(
                        "INSERT OR IGNORE INTO agents (id, user_id, referral_code, commission_rate, status, created_at) VALUES (?,?,?,?,?,?)",
                        (agent_id, order["user_id"], referral_code, 0.10, "active", datetime.now().isoformat())
                    )
                conn.commit()
            conn.close()

        return "success"
    except Exception as e:
        logger.error(f"支付宝回调异常: {e}")
        return "fail"

# --- 充值 ---
@app.post("/api/recharge")
async def api_recharge(amount: float = Form(), request: Request = None):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "请先登录"})

    order_id = f"RCH{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now().isoformat()

    conn = get_db()
    cur = conn.cursor()
    # 创建充值订单（package_id为null表示充值）
    cur.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                (order_id, user["user_id"], None, amount, "pending", "", "", now, None))
    conn.commit()
    conn.close()

    pay_url = generate_alipay_url(order_id, amount, "API中转站-余额充值")
    if not pay_url:
        pay_url = f"/payment/mock?order_id={order_id}&amount={amount}"

    return JSONResponse({"code": 0, "data": {
        "order_id": order_id,
        "amount": amount,
        "pay_url": pay_url
    }})

# --- 用量统计 ---
@app.get("/api/usage/stats")
async def api_usage_stats(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "请先登录"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(input_tokens),0) as it, COALESCE(SUM(output_tokens),0) as ot,
               COALESCE(SUM(cost),0) as tc, COUNT(*) as calls
        FROM usage_logs WHERE user_id=?
    """, (user["user_id"],))
    row = cur.fetchone()
    cur.execute("SELECT balance, api_quota FROM users WHERE id=?", (user["user_id"],))
    user_row = cur.fetchone()
    conn.close()

    return JSONResponse({"code": 0, "data": {
        "total_calls": row["calls"],
        "total_cost": row["tc"],
        "input_tokens": row["it"],
        "output_tokens": row["ot"],
        "balance": user_row["balance"],
        "quota": user_row["api_quota"]
    }})

@app.get("/api/usage/logs")
async def api_usage_logs(page: int = 1, limit: int = 20, request: Request = None):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "请先登录"})

    offset = (page - 1) * limit
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM usage_logs WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?
    """, (user["user_id"], limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM usage_logs WHERE user_id=?", (user["user_id"],))
    total = cur.fetchone()[0]
    conn.close()

    return JSONResponse({"code": 0, "data": {
        "logs": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit
    }})

# --- 订单记录 ---
@app.get("/api/orders")
async def api_orders(page: int = 1, limit: int = 20, request: Request = None):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "请先登录"})

    offset = (page - 1) * limit
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.*, p.name as package_name
        FROM orders o LEFT JOIN packages p ON o.package_id=p.id
        WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT ? OFFSET ?
    """, (user["user_id"], limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user["user_id"],))
    total = cur.fetchone()[0]
    conn.close()

    return JSONResponse({"code": 0, "data": {
        "orders": [dict(r) for r in rows],
        "total": total
    }})

# --- 管理员 API ---
@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, email, phone, balance, api_quota, role, created_at, last_login FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

@app.get("/api/admin/overview")
async def api_admin_overview(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE last_login >= date('now','-7 days')")
    active_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
    total_orders = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid'")
    total_revenue = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM usage_logs")
    total_tokens = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
    pending_orders = cur.fetchone()[0]
    conn.close()

    return JSONResponse({"code": 0, "data": {
        "total_users": total_users,
        "active_users_7d": active_users,
        "total_orders": total_orders,
        "total_revenue": round(total_revenue, 2),
        "total_tokens": total_tokens,
        "pending_orders": pending_orders
    }})

@app.get("/api/admin/api-keys")
async def api_admin_keys(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM api_keys ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

@app.post("/api/admin/api-keys")
async def api_admin_add_key(
    name: str = Form(),
    provider: str = Form(),
    api_key: str = Form(),
    base_url: str = Form(""),
    model: str = Form(""),
    price_per_1k: float = Form(0),
    request: Request = None
):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})

    key_id = str(uuid.uuid4())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,1,?)",
        (key_id, name, provider, api_key, base_url, model, price_per_1k, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return JSONResponse({"code": 0, "msg": "添加成功"})

@app.delete("/api/admin/api-keys/{key_id}")
async def api_admin_del_key(key_id: str, request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"code": 0, "msg": "删除成功"})

@app.get("/api/admin/packages")
async def api_admin_packages(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM packages ORDER BY price ASC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

@app.post("/api/admin/packages")
async def api_admin_add_package(
    name: str = Form(),
    description: str = Form(""),
    price: float = Form(),
    credits: int = Form(),
    validity_days: int = Form(30),
    request: Request = None
):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})
    pkg_id = str(uuid.uuid4())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO packages VALUES (?,?,?,?,?,?,1,?)",
        (pkg_id, name, description, price, credits, validity_days, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return JSONResponse({"code": 0, "msg": "添加成功"})

@app.delete("/api/admin/packages/{pkg_id}")
async def api_admin_del_package(pkg_id: str, request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return JSONResponse({"code": 403, "msg": "无权限"})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE packages SET is_active=0 WHERE id=?", (pkg_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"code": 0, "msg": "删除成功"})

# --- 配置 ---
@app.get("/api/config")
async def api_config():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM config")
    rows = cur.fetchall()
    conn.close()
    config = {r["key"]: r["value"] for r in rows}
    # 隐藏敏感字段
    safe = {
        "platform_name": config.get("platform_name", "API中转站"),
        "platform_logo": config.get("platform_logo", ""),
        "contact_wechat": config.get("contact_wechat", ""),
        "contact_email": config.get("contact_email", ""),
    }
    return JSONResponse({"code": 0, "data": safe})

# --- 代理转发 API（核心功能）---
@app.post("/api/v1/chat/completions")
async def api_proxy(request: Request, api_key_hdr: str = None):
    """
    OpenAI兼容格式的聊天接口
    Authorization: Bearer 用户API密钥
    """
    # 1. 获取客户端IP
    client_ip = request.client.host if request.client else "unknown"

    # 2. 验证 Authorization
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": {"message": "Missing or invalid Authorization header", "type": "invalid_request"}}, status_code=401)

    user_api_key = auth.replace("Bearer ", "").strip()

    # 3. 验证用户 API Key
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE api_key=?", (user_api_key,))
    user_row = cur.fetchone()
    if not user_row:
        conn.close()
        return JSONResponse({"error": {"message": "Invalid API key", "type": "invalid_request"}}, status_code=401)

    user_balance = user_row["balance"]
    user_quota = user_row["api_quota"]
    user_id = user_row["id"]

    # 4. 读取请求体
    body = await request.json()
    model = body.get("model", "")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # 5. 查找对应的上游 API Key
    cur.execute("SELECT * FROM api_keys WHERE is_active=1 AND (model=? OR model='') LIMIT 1",
                (model,))
    upstream_key = cur.fetchone()
    if not upstream_key:
        # 按provider查找
        provider_map = {
            "gpt-4": "openai", "gpt-3.5": "openai",
            "minimax": "minimax", "MiniMax-Text": "minimax",
            "deepseek": "deepseek", "deepseek-chat": "deepseek",
            "doubao": "volcengine", "doubao-pro": "volcengine",
        }
        provider = provider_map.get(model.split("-")[0], "minimax")
        cur.execute("SELECT * FROM api_keys WHERE is_active=1 AND provider=? LIMIT 1", (provider,))
        upstream_key = cur.fetchone()

    if not upstream_key:
        conn.close()
        return JSONResponse({"error": {"message": "No upstream API key configured for this model", "type": "invalid_request"}}, status_code=503)

    # 6. 检查余额/配额
    if user_balance <= 0 and user_quota <= 0:
        conn.close()
        return JSONResponse({"error": {"message": "余额或配额不足，请先充值", "type": "insufficient_quota"}}, status_code=402)

    # 7. 构造转发请求
    upstream_url = f"{upstream_key['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {upstream_key['api_key']}",
        "Content-Type": "application/json"
    }

    # 估算token（粗略）
    input_text = " ".join(m.get("content","") for m in messages)
    est_input_tokens = len(input_text) // 4

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(upstream_url, json=body, headers=headers)
            resp_data = resp.json()

        # 8. 计算并扣除费用
        output_text = ""
        if "choices" in resp_data:
            output_text = resp_data["choices"][0].get("message", {}).get("content", "")
        elif "content" in resp_data:
            output_text = resp_data.get("content", "")

        est_output_tokens = len(output_text) // 4
        cost = round((est_input_tokens + est_output_tokens) * upstream_key["price_per_1k"] / 1000, 6)
        cost = max(cost, 0.0001)  # 最低一分钱

        # 扣除费用
        if user_balance > cost:
            cur.execute("UPDATE users SET balance=balance-? WHERE id=?", (cost, user_id))
        else:
            cur.execute("UPDATE users SET api_quota=api_quota-1 WHERE id=?", (user_id,))
            cost = 0

        # 记录用量
        cur.execute(
            "INSERT INTO usage_logs (user_id, api_key_id, model, input_tokens, output_tokens, cost, called_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, upstream_key["id"], model, est_input_tokens, est_output_tokens, cost, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        resp_data["_cost"] = cost
        resp_data["_user_balance"] = user_balance - cost if user_balance >= cost else user_balance
        return resp_data

    except httpx.TimeoutException:
        conn.close()
        return JSONResponse({"error": {"message": "上游API超时，请稍后重试", "type": "upstream_timeout"}}, status_code=504)
    except Exception as e:
        logger.error(f"代理转发异常: {e}")
        conn.close()
        return JSONResponse({"error": {"message": f"代理异常: {str(e)}", "type": "proxy_error"}}, status_code=500)

# ===== 代理系统：申请成为代理 =====
@app.get("/agent/register")
async def agent_register_page(request: Request):
    user = require_auth(request)
    cfg = get_agent_config()
    if cfg.get("agent_enabled") != "true":
        return render("message.html", {"msg": "代理系统已关闭", "title": "代理申请"}, request)
    agent = get_agent_by_user(user["user_id"])
    if agent:
        return RedirectResponse(url="/agent/dashboard", status_code=302)
    # 检查是否已有待处理的代理申请订单
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM orders WHERE user_id=? AND status='pending' AND remark LIKE '%代理入驻%'", (user["user_id"],))
    pending = cur.fetchone()
    conn.close()
    return render("agent_register.html", {
        "cfg": cfg,
        "pending": pending is not None,
        "entry_fee": float(cfg.get("agent_entry_fee", "299")),
    }, request)

@app.post("/api/agent/apply")
async def api_agent_apply(request: Request):
    user = require_auth(request)
    cfg = get_agent_config()
    if cfg.get("agent_enabled") != "true":
        return JSONResponse({"success": False, "error": "代理系统已关闭"})
    agent = get_agent_by_user(user["user_id"])
    if agent:
        return JSONResponse({"success": False, "error": "您已经是代理"})
    entry_fee = float(cfg.get("agent_entry_fee", "299"))
    order_id = f"AGT{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (id, user_id, package_id, amount, status, remark, created_at) VALUES (?,?,NULL,?,?,?,?)",
        (order_id, user["user_id"], entry_fee, "pending", "代理入驻费", datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    pay_url = f"/payment/mock?order_id={order_id}&amount={entry_fee}"
    return JSONResponse({"success": True, "order_id": order_id, "pay_url": pay_url})

@app.get("/agent/dashboard")
async def agent_dashboard(request: Request):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return RedirectResponse(url="/agent/register", status_code=302)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT ac.*, u.username FROM agent_commissions ac LEFT JOIN users u ON ac.user_id=u.id WHERE ac.agent_id=? ORDER BY ac.created_at DESC LIMIT 50",
        (agent["id"],))
    commissions = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT COUNT(*) as cnt FROM users WHERE id IN (SELECT user_id FROM agents WHERE parent_agent_id=?)",
        (agent["id"],))
    downline_count = cur.fetchone()["cnt"]
    conn.close()
    cfg = get_agent_config()
    rate = float(cfg.get("agent_default_commission_rate", "0.10")) * 100
    return render("agent_dashboard.html", {
        "agent": agent,
        "commissions": commissions[:20],
        "total_commission": agent["total_commission"],
        "downline_count": downline_count,
        "commission_rate": rate,
    }, request)

@app.get("/api/agent/info")
async def api_agent_info(request: Request):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return JSONResponse({"is_agent": False})
    cfg = get_agent_config()
    return JSONResponse({
        "is_agent": True,
        "agent": {
            "referral_code": agent["referral_code"],
            "commission_rate": float(cfg.get("agent_default_commission_rate", "0.10")) * 100,
            "total_commission": agent["total_commission"],
            "total_downlines": agent["total_downlines"],
            "status": agent["status"],
        }
    })

@app.get("/api/agent/commissions")
async def api_agent_commissions(request: Request, limit: int = 20, offset: int = 0):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return JSONResponse({"error": "非代理"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT ac.*, u.username FROM agent_commissions ac LEFT JOIN users u ON ac.user_id=u.id WHERE ac.agent_id=? ORDER BY ac.created_at DESC LIMIT ? OFFSET ?",
        (agent["id"], limit, offset))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) as total FROM agent_commissions WHERE agent_id=?", (agent["id"],))
    total = cur.fetchone()["total"]
    conn.close()
    return JSONResponse({"commissions": [dict(r) for r in rows], "total": total})

@app.get("/api/agent/stats")
async def api_agent_stats(request: Request):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return JSONResponse({"error": "非代理"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT SUM(commission) as total, COUNT(*) as count FROM agent_commissions WHERE agent_id=?",
        (agent["id"],))
    row = cur.fetchone()
    conn.close()
    return JSONResponse({
        "total_commission": row["total"] or 0,
        "commission_count": row["count"] or 0,
        "downline_count": agent["total_downlines"],
        "referral_code": agent["referral_code"],
    })

# ===== 代理系统：注册时记录推荐码 =====
@app.post("/api/auth/register")
async def api_register(request: Request, username: str = Form(...), password: str = Form(...), referral_code: str = Form("")):
    # 原有注册逻辑 + 记录推荐码到cookie
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=?", (username,))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"success": False, "error": "用户名已存在"})
    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    cur.execute(
        "INSERT INTO users (id, username, password, balance, api_quota, api_key, created_at) VALUES (?,?,?,0,0,?,?)",
        (user_id, username, hashed, generate_api_key(), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    # 如果有推荐码，记录到cookie
    if referral_code:
        response = JSONResponse({"success": True, "redirect": "/dashboard"})
        response.set_cookie("referral_code", referral_code, httponly=True, max_age=7*24*3600)
        return response
    return JSONResponse({"success": True, "redirect": "/dashboard"})

# ===== 管理员：代理配置 =====
@app.get("/api/admin/agent-config")
async def api_admin_agent_config(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    cfg = get_agent_config()
    return JSONResponse({
        "agent_enabled": cfg.get("agent_enabled", "false"),
        "agent_entry_fee": float(cfg.get("agent_entry_fee", "299")),
        "agent_default_commission_rate": float(cfg.get("agent_default_commission_rate", "0.10")),
    })

@app.post("/api/admin/agent-config")
async def api_admin_update_agent_config(request: Request, enabled: bool = Form(False), entry_fee: float = Form(299), commission_rate: float = Form(0.10)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('agent_enabled', ?)", (str(enabled).lower(),))
    cur.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('agent_entry_fee', ?)", (str(entry_fee),))
    cur.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('agent_default_commission_rate', ?)", (str(commission_rate),))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})

@app.get("/api/admin/agents")
async def api_admin_list_agents(request: Request, page: int = 1, limit: int = 20):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM agents")
    total = cur.fetchone()["total"]
    cur.execute(
        "SELECT a.*, u.username, u.balance as user_balance FROM agents a JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
        (limit, (page-1)*limit))
    agents = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse({"agents": agents, "total": total, "page": page, "limit": limit})

@app.post("/api/admin/agents/{agent_id}/commission-rate")
async def api_admin_update_agent_rate(request: Request, agent_id: str, rate: float = Form(...)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE agents SET commission_rate=? WHERE id=?", (rate, agent_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})

@app.get("/api/admin/commissions")
async def api_admin_list_commissions(request: Request, page: int = 1, limit: int = 30):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM agent_commissions")
    total = cur.fetchone()["total"]
    cur.execute(
        "SELECT ac.*, u.username, ag.id as agent_code FROM agent_commissions ac LEFT JOIN users u ON ac.user_id=u.id LEFT JOIN agents ag ON ac.agent_id=ag.id ORDER BY ac.created_at DESC LIMIT ? OFFSET ?",
        (limit, (page-1)*limit))
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"commissions": [dict(r) for r in rows], "total": total})

@app.post("/api/admin/agents/{agent_id}/status")
async def api_admin_toggle_agent_status(request: Request, agent_id: str, status: str = Form(...)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE agents SET status=? WHERE id=?", (status, agent_id))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})

# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8000)
