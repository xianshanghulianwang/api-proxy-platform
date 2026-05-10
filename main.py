"""
终点平台 - API中转站 SaaS系统
"""
import os
import sys
import json
import time
import uuid
import httpx
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import get_db, init_db
from models import *
from auth import (
    hash_password, verify_password, create_token, verify_token, 
    generate_api_key, generate_referral_code, generate_email_code,
    send_email_code, save_email_code, verify_email_code, check_email_code
)
from alipay import generate_alipay_url

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="终点 - API中转站SaaS", version="2.0.0")

# ==================== 辅助函数 ====================

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
    return HTMLResponse(Path(TEMPLATES_DIR / template).read_text())

def get_config(key: str, default: str = "") -> str:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

# ==================== 静态文件和模板 ====================

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def home():
    return Path(TEMPLATES_DIR / "index.html").read_text()

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return Path(TEMPLATES_DIR / "login.html").read_text()

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    # 检查是否已登录
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")
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

@app.get("/docs")
async def docs_page(request: Request):
    return Path(TEMPLATES_DIR / "docs.html").read_text()

@app.get("/api_keys")
async def api_keys_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    return Path(TEMPLATES_DIR / "api_keys.html").read_text()

# ==================== 代理页面 ====================

@app.get("/agent/register")
async def agent_register_page(request: Request):
    user = require_auth(request)
    
    # 检查是否已是代理
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if agent:
        conn.close()
        return RedirectResponse("/agent/dashboard")
    
    # 获取代理等级
    cur.execute("SELECT * FROM agent_tiers WHERE is_active=1 ORDER BY price ASC")
    tiers = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return render("agent_register.html", {"tiers": tiers}, request)

@app.get("/agent/dashboard")
async def agent_dashboard_page(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    conn.close()
    
    if not agent:
        return RedirectResponse("/agent/register")
    
    return render("agent_dashboard.html", {"agent": dict(agent)}, request)

@app.get("/agent/users")
async def agent_users_page(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return RedirectResponse("/agent/register")
    
    # 获取直接下线
    cur.execute("""
        SELECT u.*, ac.created_at as registered_at 
        FROM users u 
        JOIN agents a ON u.agent_id = a.id 
        WHERE a.user_id = ? 
        ORDER BY u.created_at DESC
    """, (user["user_id"],))
    downlines = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return render("agent_users.html", {"downlines": downlines}, request)

@app.get("/agent/commissions")
async def agent_commissions_page(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return RedirectResponse("/agent/register")
    
    # 获取佣金记录
    cur.execute("""
        SELECT ac.*, u.username 
        FROM agent_commissions ac 
        JOIN users u ON ac.user_id = u.id 
        WHERE ac.agent_id = ? 
        ORDER BY ac.created_at DESC 
        LIMIT 50
    """, (agent["id"],))
    commissions = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return render("agent_commissions.html", {"commissions": commissions}, request)

@app.get("/agent/withdraw")
async def agent_withdraw_page(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return RedirectResponse("/agent/register")
    
    # 获取提现记录
    cur.execute("""
        SELECT * FROM withdrawals WHERE agent_id = ? ORDER BY created_at DESC LIMIT 20
    """, (agent["id"],))
    withdrawals = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return render("agent_withdraw.html", {
        "agent": dict(agent),
        "withdrawals": withdrawals
    }, request)

# ==================== 管理员页面 ====================

@app.get("/admin")
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse("/login")
    return Path(TEMPLATES_DIR / "admin.html").read_text()

# ==================== API: 认证 ====================

@app.post("/api/auth/send_code")
async def api_send_code(request: Request):
    data = await request.json()
    email = data.get("email", "").strip().lower()
    phone = data.get("phone", "").strip()
    
    if not email or "@" not in email:
        return JSONResponse({"code": 1, "msg": "请输入正确的邮箱"})
    
    if not phone or len(phone) != 11:
        return JSONResponse({"code": 1, "msg": "请输入正确的手机号"})
    
    # 生成验证码
    code = generate_email_code()
    
    # 保存验证码
    save_email_code(email, code, "register")
    
    # 发送邮件（实际项目中需要配置SMTP）
    # send_email_code(email, code, "注册验证")
    
    # 开发模式：返回验证码方便测试
    logger.info(f"[注册验证码] {email} -> {code}")
    
    return JSONResponse({"code": 0, "msg": "验证码已发送", "data": {"code": code}})

@app.post("/api/auth/register")
async def api_register(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    phone = data.get("phone", "").strip()
    email = data.get("email", "").strip().lower()
    email_code = data.get("email_code", "").strip()
    password = data.get("password", "").strip()
    referral_code = data.get("referral_code", "").strip()
    
    # 验证必填项
    if not username or len(username) < 4:
        return JSONResponse({"code": 1, "msg": "用户名至少4位"})
    if not phone or len(phone) != 11:
        return JSONResponse({"code": 1, "msg": "请输入正确的手机号"})
    if not email or "@" not in email:
        return JSONResponse({"code": 1, "msg": "请输入正确的邮箱"})
    if not email_code or len(email_code) != 6:
        return JSONResponse({"code": 1, "msg": "请输入6位验证码"})
    if not password or len(password) < 6:
        return JSONResponse({"code": 1, "msg": "密码至少6位"})
    
    # 验证邮箱验证码
    if not check_email_code(email, email_code, "register"):
        return JSONResponse({"code": 1, "msg": "验证码错误或已过期"})
    
    conn = get_db()
    cur = conn.cursor()
    
    # 检查用户名是否存在
    cur.execute("SELECT id FROM users WHERE username=?", (username,))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"code": 1, "msg": "用户名已存在"})
    
    # 检查手机号是否存在
    cur.execute("SELECT id FROM users WHERE phone=?", (phone,))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"code": 1, "msg": "该手机号已注册"})
    
    # 检查邮箱是否存在
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"code": 1, "msg": "该邮箱已注册"})
    
    # 查找推荐人
    parent_agent_id = None
    if referral_code:
        cur.execute("SELECT id, user_id FROM agents WHERE referral_code=?", (referral_code,))
        parent = cur.fetchone()
        if parent:
            parent_agent_id = parent["id"]
    
    # 创建用户
    user_id = str(uuid.uuid4())
    pw_hash = hash_password(password)
    api_key = generate_api_key()
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO users (id, username, password_hash, email, email_verified, phone, phone_verified, 
                         balance, role, user_type, agent_id, api_key, api_quota, created_at)
        VALUES (?,?,?,?,1,?,1,?,?,?,?,?,?,?)
    """, (user_id, username, pw_hash, email, phone, 0, "user", "customer", 
          parent_agent_id, api_key, 0, now))
    
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
    cur.execute("SELECT * FROM users WHERE username=? OR phone=? OR email=?", (username, username, username))
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

# ==================== API: 用户信息 ====================

@app.get("/api/user/info")
async def api_user_info(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 1, "msg": "未登录"})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user["user_id"],))
    row = cur.fetchone()
    
    # 检查是否是代理
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
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
        "user_type": row["user_type"],
        "is_agent": agent is not None,
        "agent_id": agent["id"] if agent else None,
        "referral_code": agent["referral_code"] if agent else None,
        "created_at": row["created_at"]
    }})

# ==================== API: 套餐 ====================

@app.get("/api/packages")
async def api_packages():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM packages WHERE is_active=1 ORDER BY price ASC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

@app.get("/api/agent/tiers")
async def api_agent_tiers():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_tiers WHERE is_active=1 ORDER BY price ASC")
    rows = cur.fetchall()
    conn.close()
    return JSONResponse({"code": 0, "data": [dict(r) for r in rows]})

# ==================== API: 代理申请 ====================

@app.post("/api/agent/apply")
async def api_agent_apply(request: Request, tier_id: str = Form()):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    
    # 检查是否已是代理
    cur.execute("SELECT id FROM agents WHERE user_id=?", (user["user_id"],))
    if cur.fetchone():
        conn.close()
        return JSONResponse({"code": 1, "msg": "您已是代理"})
    
    # 获取等级信息
    cur.execute("SELECT * FROM agent_tiers WHERE id=?", (tier_id,))
    tier = cur.fetchone()
    if not tier:
        conn.close()
        return JSONResponse({"code": 1, "msg": "等级不存在"})
    
    # 检查名额
    if tier["max_agents"] > 0 and tier["current_agents"] >= tier["max_agents"]:
        conn.close()
        return JSONResponse({"code": 1, "msg": "该等级名额已满"})
    
    # 扣除余额或创建订单
    cur.execute("SELECT balance FROM users WHERE id=?", (user["user_id"],))
    user_row = cur.fetchone()
    
    if user_row["balance"] < tier["price"]:
        conn.close()
        return JSONResponse({"code": 1, "msg": "余额不足，请先充值"})
    
    # 扣除余额
    cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (tier["price"], user["user_id"]))
    
    # 创建代理
    agent_id = str(uuid.uuid4())
    referral_code = generate_referral_code()
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO agents (id, user_id, tier_id, referral_code, commission_rate_l1, 
                          commission_rate_l2, commission_rate_l3, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (agent_id, user["user_id"], tier_id, referral_code, 
          tier["commission_l1"], tier["commission_l2"], tier["commission_l3"], "active", now))
    
    # 更新等级人数
    cur.execute("UPDATE agent_tiers SET current_agents = current_agents + 1 WHERE id=?", (tier_id,))
    
    conn.commit()
    conn.close()
    
    logger.info(f"[代理入驻] 用户{user['user_id']}成为{tier['name']}，代理ID={agent_id}")
    
    return JSONResponse({"code": 0, "msg": "恭喜成为代理！", "data": {
        "agent_id": agent_id,
        "referral_code": referral_code
    }})

# ==================== API: 代理上游API ====================

@app.get("/api/agent/upstream_apis")
async def api_agent_upstream_apis(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    cur.execute("SELECT * FROM agent_upstream_apis WHERE agent_id=? ORDER BY created_at DESC", (agent["id"],))
    apis = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "data": apis})

@app.post("/api/agent/upstream_apis")
async def api_add_upstream_api(request: Request):
    user = require_auth(request)
    data = await request.json()
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    api_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO agent_upstream_apis (id, agent_id, name, provider, api_key, base_url, model, price_per_1k, markup_rate, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (api_id, agent["id"], data.get("name"), data.get("provider"), 
          data.get("api_key"), data.get("base_url"), data.get("model"),
          float(data.get("price_per_1k", 0)), float(data.get("markup_rate", 1.0)), now))
    
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "添加成功"})

@app.delete("/api/agent/upstream_apis/{api_id}")
async def api_delete_upstream_api(request: Request, api_id: str):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    cur.execute("DELETE FROM agent_upstream_apis WHERE id=? AND agent_id=?", (api_id, agent["id"]))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "删除成功"})

# ==================== API: 代理下线 ====================

@app.get("/api/agent/downlines")
async def api_agent_downlines(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    # 获取直接下线
    cur.execute("""
        SELECT u.id, u.username, u.phone, u.email, u.balance, u.api_quota, u.created_at,
               (SELECT SUM(commission) FROM agent_commissions WHERE agent_id=? AND user_id=u.id) as total_commission
        FROM users u
        WHERE u.agent_id = ?
        ORDER BY u.created_at DESC
    """, (agent["id"], agent["id"]))
    downlines = [dict(r) for r in cur.fetchall()]
    
    conn.close()
    
    return JSONResponse({"code": 0, "data": downlines})

# ==================== API: 代理佣金 ====================

@app.get("/api/agent/commissions")
async def api_agent_commissions(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    cur.execute("""
        SELECT ac.*, u.username, u.phone
        FROM agent_commissions ac
        JOIN users u ON ac.user_id = u.id
        WHERE ac.agent_id = ?
        ORDER BY ac.created_at DESC
        LIMIT 50
    """, (agent["id"],))
    commissions = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "data": commissions})

# ==================== API: 提现 ====================

@app.post("/api/agent/withdraw")
async def api_agent_withdraw(request: Request):
    user = require_auth(request)
    data = await request.json()
    
    amount = float(data.get("amount", 0))
    bank_name = data.get("bank_name", "")
    bank_account = data.get("bank_account", "")
    bank_holder = data.get("bank_holder", "")
    
    if amount < 100:
        return JSONResponse({"code": 1, "msg": "最低提现100元"})
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    if agent["withdrawable_balance"] < amount:
        conn.close()
        return JSONResponse({"code": 1, "msg": "可提现余额不足"})
    
    # 创建提现记录
    withdraw_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO withdrawals (id, agent_id, amount, bank_name, bank_account, bank_holder, status, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (withdraw_id, agent["id"], amount, bank_name, bank_account, bank_holder, "pending", now))
    
    # 冻结余额
    cur.execute("UPDATE agents SET withdrawable_balance = withdrawable_balance - ? WHERE id=?", 
                (amount, agent["id"]))
    
    conn.commit()
    conn.close()
    
    logger.info(f"[提现申请] 代理{agent['id']}申请提现{amount}元")
    
    return JSONResponse({"code": 0, "msg": "提现申请已提交"})

@app.get("/api/agent/withdrawals")
async def api_agent_withdrawals(request: Request):
    user = require_auth(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user["user_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return JSONResponse({"code": 1, "msg": "不是代理"})
    
    cur.execute("""
        SELECT * FROM withdrawals WHERE agent_id = ? ORDER BY created_at DESC LIMIT 20
    """, (agent["id"],))
    withdrawals = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "data": withdrawals})

# ==================== API: 订单 ====================

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

    order_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    cur.execute("""
        INSERT INTO orders (id, user_id, package_id, amount, status, created_at)
        VALUES (?,?,?,?,?,?)
    """, (order_id, user["user_id"], package_id, pkg["price"], "pending", now))
    conn.commit()
    conn.close()

    # 生成支付链接
    pay_url = generate_alipay_url(order_id, pkg["price"], pkg["name"])

    return JSONResponse({"code": 0, "data": {
        "order_id": order_id,
        "amount": pkg["price"],
        "pay_url": pay_url
    }})

# ==================== API: 充值 ====================

@app.post("/api/recharge")
async def api_recharge(request: Request):
    user = require_auth(request)
    data = await request.json()
    
    amount = float(data.get("amount", 0))
    if amount <= 0:
        return JSONResponse({"code": 1, "msg": "金额必须大于0"})
    
    conn = get_db()
    cur = conn.cursor()
    
    # 创建充值记录
    order_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO recharge_logs (user_id, amount, method, status, created_at)
        VALUES (?,?,?,?,?)
    """, (user["user_id"], amount, "alipay", "pending", now))
    
    conn.commit()
    conn.close()
    
    # 生成支付链接
    pay_url = generate_alipay_url(order_id, amount, f"充值{amount}元")
    
    return JSONResponse({"code": 0, "data": {
        "order_id": order_id,
        "amount": amount,
        "pay_url": pay_url
    }})

# ==================== API: 回调 ====================

@app.post("/api/callback/alipay")
async def api_alipay_callback(request: Request):
    """支付宝回调"""
    data = await request.json()
    order_id = data.get("order_id")
    trade_status = data.get("trade_status")
    
    if trade_status != "TRADE_SUCCESS":
        return JSONResponse({"code": 1, "msg": "支付失败"})
    
    conn = get_db()
    cur = conn.cursor()
    
    # 更新订单
    cur.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?", 
                (datetime.now().isoformat(), order_id))
    
    # 获取订单信息
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()
    
    if order:
        user_id = order["user_id"]
        amount = order["amount"]
        
        # 如果是套餐订单，添加额度
        if order["package_id"]:
            cur.execute("SELECT * FROM packages WHERE id=?", (order["package_id"],))
            pkg = cur.fetchone()
            if pkg:
                cur.execute("UPDATE users SET api_quota = api_quota + ? WHERE id=?", 
                           (pkg["credits"], user_id))
        
        # 给用户加余额（可用于API消费）
        cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        
        # 处理多级佣金
        process_commissions(user_id, order_id, amount)
    
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "success"})

# ==================== 多级佣金处理 ====================

def process_commissions(user_id: str, order_id: str, amount: float):
    """处理多级佣金分配"""
    conn = get_db()
    cur = conn.cursor()
    
    # 获取用户的代理信息
    cur.execute("SELECT agent_id FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()
    if not user or not user["agent_id"]:
        conn.close()
        return
    
    # 一级佣金
    cur.execute("SELECT * FROM agents WHERE id=?", (user["agent_id"],))
    agent = cur.fetchone()
    if not agent:
        conn.close()
        return
    
    now = datetime.now().isoformat()
    
    # 计算一级佣金
    l1_commission = round(amount * agent["commission_rate_l1"], 2)
    if l1_commission > 0:
        cur.execute("""
            INSERT INTO agent_commissions (agent_id, user_id, order_id, recharge_amount, commission, level, status, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (agent["id"], user_id, order_id, amount, l1_commission, 1, "pending", now))
        cur.execute("UPDATE agents SET total_commission = total_commission + ?, withdrawable_balance = withdrawable_balance + ? WHERE id=?",
                    (l1_commission, l1_commission, agent["id"]))
        cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (l1_commission, agent["user_id"]))
        
        logger.info(f"[佣金] L1 代理{agent['id']} 获得{l1_commission}元")
        
        # 二级佣金（上级代理）
        if agent["parent_agent_id"]:
            cur.execute("SELECT * FROM agents WHERE id=?", (agent["parent_agent_id"],))
            parent = cur.fetchone()
            if parent:
                l2_commission = round(amount * parent["commission_rate_l2"], 2)
                if l2_commission > 0:
                    cur.execute("""
                        INSERT INTO agent_commissions (agent_id, user_id, order_id, recharge_amount, commission, level, status, created_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (parent["id"], user_id, order_id, amount, l2_commission, 2, "pending", now))
                    cur.execute("UPDATE agents SET total_commission = total_commission + ?, withdrawable_balance = withdrawable_balance + ? WHERE id=?",
                                (l2_commission, l2_commission, parent["id"]))
                    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (l2_commission, parent["user_id"]))
                    
                    logger.info(f"[佣金] L2 代理{parent['id']} 获得{l2_commission}元")
    
    conn.commit()
    conn.close()

# ==================== API: 平台API密钥 ====================

@app.get("/api/platform/apis")
async def api_platform_apis(request: Request):
    """获取平台API（供C端用户使用）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM api_keys WHERE is_active=1")
    apis = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse({"code": 0, "data": apis})

# ==================== API代理转发 ====================

@app.post("/v1/chat/completions")
async def api_proxy_chat(request: Request, api_key: str = Form(None)):
    """API代理转发"""
    # 获取客户端IP
    client_ip = request.client.host if request.client else "unknown"
    
    # 验证API Key
    if not api_key:
        # 从Header获取
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            api_key = auth[7:]
    
    if not api_key:
        return JSONResponse({"error": {"message": "Missing API key", "type": "authentication_error"}}, status_code=401)
    
    conn = get_db()
    cur = conn.cursor()
    
    # 查找用户
    cur.execute("SELECT * FROM users WHERE api_key=?", (api_key,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
    
    # 检查额度
    if user["api_quota"] <= 0 and user["balance"] <= 0:
        conn.close()
        return JSONResponse({"error": {"message": "余额不足", "type": "insufficient_quota"}}, status_code=403)
    
    # 获取请求体
    body = await request.json()
    model = body.get("model", "")
    
    # 查找对应平台API
    cur.execute("SELECT * FROM api_keys WHERE is_active=1 LIMIT 1")
    platform_api = cur.fetchone()
    
    if not platform_api:
        conn.close()
        return JSONResponse({"error": {"message": "No upstream API available", "type": "server_error"}}, status_code=503)
    
    # 转发请求到上游
    upstream_url = f"{platform_api['base_url']}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {platform_api['api_key']}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(upstream_url, json=body, headers=headers)
            result = resp.json()
            
            # 计算费用（简化版）
            input_tokens = result.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = result.get("usage", {}).get("completion_tokens", 0)
            cost = (input_tokens + output_tokens) * platform_api["price_per_1k"] / 1000
            
            # 扣除额度
            if user["api_quota"] > 0:
                cur.execute("UPDATE users SET api_quota = api_quota - ? WHERE id=?", (max(0, cost), user["id"]))
            else:
                cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (cost, user["id"]))
            
            # 记录用量
            cur.execute("""
                INSERT INTO usage_logs (user_id, api_key_id, model, input_tokens, output_tokens, cost, called_at)
                VALUES (?,?,?,?,?,?,?)
            """, (user["id"], platform_api["id"], model, input_tokens, output_tokens, cost, datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
            
            return JSONResponse(result)
            
    except Exception as e:
        logger.error(f"[API转发失败] {e}")
        conn.close()
        return JSONResponse({"error": {"message": f"上游API错误: {str(e)}", "type": "server_error"}}, status_code=502)

# ==================== 管理后台 API ====================

def require_admin(request: Request) -> dict:
    """验证管理员权限"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

@app.get("/api/admin/overview")
async def api_admin_overview(request: Request):
    """管理后台概览数据"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    
    # 用户统计
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    total_users = cur.fetchone()["cnt"]
    
    # 订单统计
    cur.execute("SELECT COUNT(*) as cnt, SUM(amount) as total FROM orders WHERE status='paid'")
    order_stats = cur.fetchone()
    total_orders = order_stats["cnt"] or 0
    total_revenue = float(order_stats["total"] or 0)
    
    # 代理统计
    cur.execute("SELECT COUNT(*) as cnt FROM agents WHERE status='active'")
    total_agents = cur.fetchone()["cnt"]
    
    # 待处理订单
    cur.execute("SELECT COUNT(*) as cnt FROM orders WHERE status='pending'")
    pending_orders = cur.fetchone()["cnt"]
    
    # Token消耗
    cur.execute("SELECT SUM(input_tokens + output_tokens) as total FROM usage_logs")
    total_tokens = cur.fetchone()["total"] or 0
    
    conn.close()
    
    return JSONResponse({"code": 0, "data": {
        "total_users": total_users,
        "active_users_7d": total_users,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "pending_orders": pending_orders,
        "total_tokens": total_tokens,
        "total_agents": total_agents
    }})

@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    """获取所有用户"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    users = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    # 检查是否为代理
    for u in users:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM agents WHERE user_id=?", (u["id"],))
        agent = cur.fetchone()
        u["is_agent"] = agent is not None
        conn.close()
    
    return JSONResponse({"code": 0, "data": users})

@app.get("/api/admin/agents")
async def api_admin_agents(request: Request):
    """获取所有代理"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.*, u.username, u.phone, t.name as tier_name,
               (SELECT COUNT(*) FROM users WHERE agent_id = a.id) as total_downlines
        FROM agents a
        JOIN users u ON a.user_id = u.id
        LEFT JOIN agent_tiers t ON a.tier_id = t.id
        ORDER BY a.created_at DESC
    """)
    agents = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "agents": agents})

@app.put("/api/admin/agents/{agent_id}/status")
async def api_admin_toggle_agent(agent_id: str, request: Request, status: str = Form()):
    """修改代理状态"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE agents SET status=? WHERE id=?", (status, agent_id))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "状态已更新"})

@app.get("/api/admin/orders")
async def api_admin_orders(request: Request):
    """获取所有订单"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.*, u.username, p.name as package_name
        FROM orders o
        JOIN users u ON o.user_id = u.id
        LEFT JOIN packages p ON o.package_id = p.id
        ORDER BY o.created_at DESC
    """)
    orders = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "orders": orders})

@app.get("/api/admin/commissions")
async def api_admin_commissions(request: Request, limit: int = 50):
    """获取佣金记录"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ac.*, u.username, a.referral_code as agent_code, a.user_id as agent_user_id,
               (SELECT username FROM users WHERE id = a.user_id) as agent_username
        FROM agent_commissions ac
        JOIN users u ON ac.user_id = u.id
        JOIN agents a ON ac.agent_id = a.id
        ORDER BY ac.created_at DESC
        LIMIT ?
    """, (limit,))
    commissions = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "commissions": commissions})

@app.get("/api/admin/withdrawals")
async def api_admin_withdrawals(request: Request):
    """获取所有提现申请"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT w.*, a.user_id as agent_user_id,
               (SELECT username FROM users WHERE id = a.user_id) as agent_username
        FROM withdrawals w
        JOIN agents a ON w.agent_id = a.id
        ORDER BY w.created_at DESC
    """)
    withdrawals = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "withdrawals": withdrawals})

@app.post("/api/admin/withdrawals/{withdraw_id}/approve")
async def api_admin_approve_withdraw(withdraw_id: str, request: Request):
    """批准提现"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE withdrawals SET status='approved', updated_at=? WHERE id=?", 
                (datetime.now().isoformat(), withdraw_id))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "已批准"})

@app.post("/api/admin/withdrawals/{withdraw_id}/reject")
async def api_admin_reject_withdraw(withdraw_id: str, request: Request):
    """拒绝提现"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    
    # 获取提现金额并返还
    cur.execute("SELECT amount, agent_id FROM withdrawals WHERE id=?", (withdraw_id,))
    withdraw = cur.fetchone()
    if withdraw:
        cur.execute("UPDATE agents SET withdrawable_balance = withdrawable_balance + ? WHERE id=?",
                   (withdraw["amount"], withdraw["agent_id"]))
    
    cur.execute("UPDATE withdrawals SET status='rejected', updated_at=? WHERE id=?",
                (datetime.now().isoformat(), withdraw_id))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "已拒绝"})

@app.post("/api/admin/packages")
async def api_admin_create_package(request: Request):
    """创建套餐"""
    require_admin(request)
    
    data = await request.form()
    
    conn = get_db()
    cur = conn.cursor()
    
    package_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO packages (id, name, description, price, credits, validity_days, is_active, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (package_id, data.get("name"), data.get("description"), 
          float(data.get("price", 0)), int(data.get("credits", 0)),
          int(data.get("validity_days", 30)), 1, now))
    
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "套餐已创建", "data": {"id": package_id}})

@app.get("/api/admin/packages")
async def api_admin_list_packages(request: Request):
    """获取所有套餐"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM packages ORDER BY sort_order ASC, created_at DESC")
    packages = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "data": packages})

@app.delete("/api/admin/packages/{package_id}")
async def api_admin_delete_package(package_id: str, request: Request):
    """删除套餐"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE packages SET is_active=0 WHERE id=?", (package_id,))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "套餐已删除"})

@app.post("/api/admin/api-keys")
async def api_admin_create_api_key(request: Request):
    """创建API Key"""
    require_admin(request)
    
    data = await request.form()
    
    conn = get_db()
    cur = conn.cursor()
    
    key_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cur.execute("""
        INSERT INTO api_keys (id, name, provider, api_key, base_url, model, price_per_1k, is_active, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (key_id, data.get("name"), data.get("provider"), data.get("api_key"),
          data.get("base_url"), data.get("model"), float(data.get("price_per_1k", 0.001)),
          1, now))
    
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "API Key已添加", "data": {"id": key_id}})

@app.get("/api/admin/api-keys")
async def api_admin_list_api_keys(request: Request):
    """获取所有API Keys"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM api_keys ORDER BY created_at DESC")
    keys = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    return JSONResponse({"code": 0, "data": keys})

@app.delete("/api/admin/api-keys/{key_id}")
async def api_admin_delete_api_key(key_id: str, request: Request):
    """删除API Key"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "API Key已删除"})

@app.delete("/api/admin/tiers/{tier_id}")
async def api_admin_delete_tier(tier_id: str, request: Request):
    """删除代理等级"""
    require_admin(request)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE agent_tiers SET is_active=0 WHERE id=?", (tier_id,))
    conn.commit()
    conn.close()
    
    return JSONResponse({"code": 0, "msg": "等级已删除"})

# ==================== 健康检查 ====================

@app.get("/health")
async def health():
    return {"status": "ok", "service": "终点API中转站", "version": "2.0.0"}

# ==================== 初始化 ====================

if __name__ == "__main__":
    init_db()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
