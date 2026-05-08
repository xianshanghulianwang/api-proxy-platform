#!/bin/bash
set -e
echo "🚀 开始部署 API中转站..."

# 1. 安装依赖
echo "📦 安装系统依赖..."
yum install -y python3 python3-pip git 2>/dev/null || \
apt install -y python3-pip git 2>/dev/null || \
dnf install -y python3 python3-pip git 2>/dev/null

echo "📦 安装 Python 依赖..."
pip3 install fastapi "uvicorn[standard]" httpx pydantic python-multipart --break-system-packages

# 2. 创建项目目录
mkdir -p /root/api-proxy-platform
cd /root/api-proxy-platform

# 3. 写入 database.py
cat > database.py << 'DBEOF'
import sqlite3, os
DB_PATH = os.path.join(os.path.dirname(__file__), "platform.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, balance REAL DEFAULT 0, api_quota INTEGER DEFAULT 0, api_key TEXT UNIQUE, role TEXT DEFAULT 'user', created_at TEXT, last_login TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, package_id TEXT, amount REAL NOT NULL, status TEXT DEFAULT 'pending', alipay_trade_no TEXT, paid_at TEXT, created_at TEXT, remark TEXT DEFAULT '', FOREIGN KEY(user_id) REFERENCES users(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS recharge_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, amount REAL NOT NULL, method TEXT DEFAULT 'alipay', status TEXT DEFAULT 'success', created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL, parent_agent_id TEXT, referral_code TEXT UNIQUE NOT NULL, commission_rate REAL DEFAULT 0.10, status TEXT DEFAULT 'active', total_commission REAL DEFAULT 0, total_downlines INTEGER DEFAULT 0, created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS agent_commissions (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, user_id TEXT NOT NULL, order_id TEXT, recharge_amount REAL DEFAULT 0, commission REAL NOT NULL, level INTEGER DEFAULT 1, created_at TEXT, FOREIGN KEY(agent_id) REFERENCES agents(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS packages (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, price REAL NOT NULL, credits INTEGER DEFAULT 0, validity_days INTEGER DEFAULT 30, is_active INTEGER DEFAULT 1)")
    cur.execute("CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL, api_key TEXT NOT NULL, base_url TEXT, model TEXT, price_per_1k REAL DEFAULT 0, is_active INTEGER DEFAULT 1)")
    cur.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS usage_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, api_key_id TEXT, model TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, called_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id))")
    
    import uuid, hashlib
    hp = hashlib.sha256("admin".encode()).hexdigest()
    cur.execute("SELECT id FROM users WHERE username=?", ("admin",))
    if not cur.fetchone():
        cur.execute("INSERT INTO users VALUES (?,?,?,0,0,?,?,?,?)",
                    (str(uuid.uuid4()), "admin", hp, str(uuid.uuid4()), "admin", "2024-01-01T00:00:00", None))
    
    cur.execute("SELECT id FROM packages WHERE id='pkg_basic'")
    if not cur.fetchone():
        cur.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,?)", ("pkg_basic","基础套餐","适合轻度使用",49,1000,30,1))
        cur.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,?)", ("pkg_standard","标准套餐","适合日常使用",199,5000,90,1))
        cur.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,?)", ("pkg_pro","专业套餐","适合高强度使用",499,15000,180,1))
    
    configs = [("platform_name","API中转站"),("agent_entry_fee","299"),("agent_default_commission_rate","0.10"),("agent_enabled","true"),("default_markup","1.3")]
    for k, v in configs:
        cur.execute("INSERT OR IGNORE INTO config VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

if __name__ == "__main__":
    init_db()
DBEOF

echo "✅ database.py 创建完成"

# 4. 写入 main.py (完整版)
cat > main.py << 'MAINEOF'
import os, sys, json, time, uuid, httpx, hashlib, logging
from datetime import datetime
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from database import get_db, init_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

app = FastAPI(title="API中转站", version="1.0.0")

# 辅助函数
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

def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def verify_password(pwd: str, hashed: str) -> bool:
    return hashlib.sha256(pwd.encode()).hexdigest() == hashed

def create_token(user_id: str, username: str, role: str = "user") -> str:
    import base64, json as _json
    payload = _json.dumps({"user_id": user_id, "username": username, "role": role, "exp": time.time()+86400*7})
    return base64.b64encode(payload.encode()).decode()

def verify_token(token: str) -> Optional[dict]:
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
    return f"AGT{uuid.uuid4().hex[:8].upper()}"

# 代理辅助
def is_agent(user_id: str) -> bool:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id FROM agents WHERE user_id=? AND status='active'", (user_id,))
    row = cur.fetchone(); conn.close()
    return row is not None

def get_agent_by_user(user_id: str) -> Optional[dict]:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=?", (user_id,))
    row = cur.fetchone(); conn.close()
    return dict(row) if row else None

def get_agent_config() -> dict:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key, value FROM config WHERE key LIKE 'agent_%' OR key='agent_enabled'")
    rows = cur.fetchall(); conn.close()
    return {r["key"]: r["value"] for r in rows}

def process_agent_commission(user_id: str, order_id: str, recharge_amount: float):
    if recharge_amount <= 0:
        return
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE user_id=? AND status='active'", (user_id,))
    agent = cur.fetchone()
    if not agent:
        conn.close(); return
    cfg = get_agent_config()
    rate = float(cfg.get("agent_default_commission_rate", "0.10"))
    commission = round(recharge_amount * rate, 4)
    if commission <= 0:
        conn.close(); return
    cur.execute("INSERT INTO agent_commissions (agent_id, user_id, order_id, recharge_amount, commission, level, created_at) VALUES (?,?,?,?,?,?,?)",
        (agent["id"], user_id, order_id, recharge_amount, commission, 1, datetime.now().isoformat()))
    cur.execute("UPDATE agents SET total_commission=total_commission+?, total_downlines=total_downlines+1 WHERE id=?", (commission, agent["id"]))
    cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (commission, agent["user_id"]))
    conn.commit(); conn.close()
    logger.info(f"[代理佣金] 用户{user_id}充值{recharge_amount}元，代理{agent['id']}获得佣金{commission}元")

# 静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 首页
@app.get("/")
async def home(request: Request):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key, value FROM config")
    config = {r["key"]: r["value"] for r in cur.fetchall()}
    conn.close()
    return render("index.html", {"config": config}, request)

# 注册
@app.route("/register", methods=["GET", "POST"])
async def register(request: Request):
    if request.method == "POST":
        form = await request.form()
        username = form.get("username", "").strip()
        password = form.get("password", "")
        referral_code = form.get("referral_code", "").strip()
        if not username or not password:
            return JSONResponse({"success": False, "error": "请填写完整信息"})
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=?", (username,))
        if cur.fetchone():
            conn.close()
            return JSONResponse({"success": False, "error": "用户名已存在"})
        user_id = str(uuid.uuid4())
        cur.execute("INSERT INTO users (id, username, password, balance, api_quota, api_key, created_at) VALUES (?,?,?,0,0,?,?)",
                    (user_id, username, hash_password(password), generate_api_key(), datetime.now().isoformat()))
        # 推荐码关联代理
        if referral_code:
            cur.execute("SELECT id FROM agents WHERE referral_code=? AND status='active'", (referral_code,))
            agent = cur.fetchone()
            if agent:
                logger.info(f"用户{username}通过推荐码{referral_code}注册")
        conn.commit(); conn.close()
        resp = JSONResponse({"success": True, "redirect": "/dashboard"})
        resp.set_cookie("token", create_token(user_id, username, "user"), httponly=True, max_age=86400*7)
        return resp
    return render("register.html", {}, request)

# 登录
@app.route("/login", methods=["GET", "POST"])
async def login(request: Request):
    if request.method == "POST":
        form = await request.form()
        username = form.get("username", "").strip()
        password = form.get("password", "")
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cur.fetchone()
        if not user or not verify_password(password, user["password"]):
            conn.close()
            return JSONResponse({"success": False, "error": "用户名或密码错误"})
        cur.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(), user["id"]))
        conn.commit(); conn.close()
        resp = JSONResponse({"success": True, "redirect": "/dashboard"})
        resp.set_cookie("token", create_token(user["id"], user["username"], user.get("role","user")), httponly=True, max_age=86400*7)
        return resp
    return render("login.html", {}, request)

@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie("token")
    return resp

# 用户信息
@app.get("/api/user/info")
async def user_info(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"code": 401, "msg": "未登录"})
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user["user_id"],))
    u = cur.fetchone()
    conn.close()
    if not u:
        return JSONResponse({"code": 404, "msg": "用户不存在"})
    return JSONResponse({"code": 0, "data": {
        "id": u["id"], "username": u["username"], "balance": u["balance"],
        "api_quota": u["api_quota"], "api_key": u["api_key"], "role": u.get("role","user"),
        "is_agent": is_agent(u["id"]),
        "created_at": u["created_at"]
    }})

# 代理信息
@app.get("/api/agent/info")
async def api_agent_info(request: Request):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return JSONResponse({"is_agent": False})
    cfg = get_agent_config()
    return JSONResponse({"is_agent": True, "agent": {
        "referral_code": agent["referral_code"],
        "commission_rate": float(cfg.get("agent_default_commission_rate","0.10"))*100,
        "total_commission": agent["total_commission"],
        "total_downlines": agent["total_downlines"],
    }})

# 代理申请页
@app.get("/agent/register")
async def agent_register_page(request: Request):
    user = require_auth(request)
    cfg = get_agent_config()
    if cfg.get("agent_enabled") != "true":
        return render("message.html", {"msg": "代理系统已关闭", "title": "代理申请"}, request)
    agent = get_agent_by_user(user["user_id"])
    if agent:
        return RedirectResponse(url="/agent/dashboard", status_code=302)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id FROM orders WHERE user_id=? AND status='pending' AND remark LIKE '%代理入驻%'", (user["user_id"],))
    pending = cur.fetchone(); conn.close()
    entry_fee = float(cfg.get("agent_entry_fee", "299"))
    return render("agent_register.html", {"cfg": cfg, "pending": pending is not None, "entry_fee": entry_fee}, request)

# 代理申请API
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
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO orders (id, user_id, package_id, amount, status, remark, created_at) VALUES (?,?,NULL,?,?,?,?)",
                (order_id, user["user_id"], entry_fee, "pending", "代理入驻费", datetime.now().isoformat()))
    conn.commit(); conn.close()
    return JSONResponse({"success": True, "order_id": order_id, "pay_url": f"/payment/mock?order_id={order_id}&amount={entry_fee}"})

# 代理控制台
@app.get("/agent/dashboard")
async def agent_dashboard(request: Request):
    user = require_auth(request)
    agent = get_agent_by_user(user["user_id"])
    if not agent:
        return RedirectResponse(url="/agent/register", status_code=302)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT ac.*, u.username FROM agent_commissions ac LEFT JOIN users u ON ac.user_id=u.id WHERE ac.agent_id=? ORDER BY ac.created_at DESC LIMIT 50", (agent["id"],))
    commissions = [dict(r) for r in cur.fetchall()]
    cfg = get_agent_config()
    rate = float(cfg.get("agent_default_commission_rate","0.10"))*100
    conn.close()
    return render("agent_dashboard.html", {"agent": agent, "commissions": commissions[:20], "total_commission": agent["total_commission"], "commission_rate": rate}, request)

# 仪表盘
@app.get("/dashboard")
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user["user_id"],))
    u = cur.fetchone()
    cur.execute("SELECT * FROM packages WHERE is_active=1")
    packages = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not u:
        return RedirectResponse(url="/login", status_code=302)
    return render("dashboard.html", {"u": dict(u), "packages": packages}, request)

# 套餐管理
@app.get("/api/packages")
async def list_packages(request: Request):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM packages WHERE is_active=1")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse({"code": 0, "data": rows})

# 创建订单
@app.post("/api/orders/create")
async def create_order(request: Request, package_id: str = Form(...)):
    user = require_auth(request)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM packages WHERE id=?", (package_id,))
    pkg = cur.fetchone()
    if not pkg:
        conn.close()
        return JSONResponse({"success": False, "error": "套餐不存在"})
    order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    cur.execute("INSERT INTO orders (id, user_id, package_id, amount, status, created_at) VALUES (?,?,?,?,?,?)",
                (order_id, user["user_id"], package_id, pkg["price"], "pending", datetime.now().isoformat()))
    conn.commit(); conn.close()
    return JSONResponse({"success": True, "order_id": order_id, "pay_url": f"/payment/mock?order_id={order_id}&amount={pkg['price']}"})

# 模拟支付
@app.get("/payment/mock")
async def mock_payment(order_id: str, amount: float, request: Request):
    user = get_current_user(request)
    if not user:
        return HTMLResponse("<h2>请先登录</h2><a href='/login'>去登录</a>")
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()
    if order and order["status"] == "pending":
        cur.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?", (datetime.now().isoformat(), order_id))
        package_id_val = order["package_id"]
        recharge_amount = order["amount"]
        order_remark = order.get("remark","") or ""
        if package_id_val:
            cur.execute("SELECT * FROM packages WHERE id=?", (package_id_val,))
            pkg = cur.fetchone()
            if pkg and user["user_id"]:
                cur.execute("UPDATE users SET balance=balance+?, api_quota=api_quota+? WHERE id=?", (pkg["price"], pkg["credits"], user["user_id"]))
                if pkg["price"] > 0:
                    process_agent_commission(user["user_id"], order_id, pkg["price"])
        elif "代理入驻费" in order_remark:
            agent_id = f"AGT{uuid.uuid4().hex[:12].upper()}"
            referral_code = generate_referral_code()
            cur.execute("INSERT OR IGNORE INTO agents (id, user_id, referral_code, commission_rate, status, created_at) VALUES (?,?,?,?,?,?)",
                (agent_id, user["user_id"], referral_code, 0.10, "active", datetime.now().isoformat()))
            logger.info(f"[代理入驻] 用户{user['user_id']}成为代理，ID={agent_id}")
        else:
            if user["user_id"]:
                cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (recharge_amount, user["user_id"]))
                if recharge_amount > 0:
                    process_agent_commission(user["user_id"], order_id, recharge_amount)
        conn.commit()
    conn.close()
    return HTMLResponse("""
    <html><head><meta charset="utf-8"><style>
    body{font-family:'PingFang SC',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f0f9f4;}
    .box{text-align:center;background:white;padding:48px 64px;border-radius:24px;box-shadow:0 8px 32px rgba(0,0,0,0.1);}
    .icon{font-size:64px;margin-bottom:16px;}
    h2{color:#10B981;margin:0 0 12px;}
    p{color:#666;margin:0 0 24px;}
    a{background:#0A6E6E;color:white;padding:12px 32px;border-radius:24px;text-decoration:none;font-weight:600;}
    </style></head><body>
    <div class="box"><div class="icon">✅</div><h2>支付成功！</h2>
    <p>您的账户已充值，现在可以开始使用API服务了</p>
    <a href="/dashboard">去使用 →</a></div></body></html>""")

# 余额充值
@app.post("/api/recharge")
async def recharge(request: Request, amount: float = Form(...)):
    user = require_auth(request)
    order_id = f"RECH{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO orders (id, user_id, package_id, amount, status, created_at) VALUES (?,?,NULL,?,?,?)",
                (order_id, user["user_id"], amount, "pending", datetime.now().isoformat()))
    conn.commit(); conn.close()
    return JSONResponse({"success": True, "order_id": order_id, "pay_url": f"/payment/mock?order_id={order_id}&amount={amount}"})

# API代理转发
@app.post("/v1/chat/completions")
async def chat_completions(request: Request, Authorization: str = None):
    try:
        body = await request.json()
    except:
        return JSONResponse({"error": {"message": "Invalid JSON", "type": "invalid_request"}}, status_code=400)
    
    model = body.get("model", "")
    messages = body.get("messages", [])
    
    api_key = None
    if Authorization and Authorization.startswith("Bearer "):
        api_key = Authorization[7:]
    
    if not api_key:
        return JSONResponse({"error": {"message": "缺少API Key", "type": "authentication_error"}}, status_code=401)
    
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE api_key=?", (api_key,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return JSONResponse({"error": {"message": "无效API Key", "type": "authentication_error"}}, status_code=401)
    
    user_id = user["id"]
    user_balance = user["balance"] or 0
    user_quota = user["api_quota"] or 0
    
    provider_map = {"gpt-4":"openai","gpt-3.5":"openai","minimax":"minimax","MiniMax-Text":"minimax","deepseek":"deepseek","doubao":"volcengine"}
    provider = provider_map.get(model.split("-")[0], "minimax")
    cur.execute("SELECT * FROM api_keys WHERE is_active=1 AND provider=? LIMIT 1", (provider,))
    upstream_key = cur.fetchone()
    
    if not upstream_key:
        conn.close()
        return JSONResponse({"error": {"message": "No upstream API key configured", "type": "invalid_request"}}, status_code=503)
    
    if user_balance <= 0 and user_quota <= 0:
        conn.close()
        return JSONResponse({"error": {"message": "余额或配额不足，请先充值", "type": "insufficient_quota"}}, status_code=402)
    
    upstream_url = f"{upstream_key['base_url']}/chat/completions"
    headers = {"Authorization": f"Bearer {upstream_key['api_key']}", "Content-Type": "application/json"}
    input_text = " ".join(m.get("content","") for m in messages)
    est_input_tokens = len(input_text) // 4
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(upstream_url, json=body, headers=headers)
            resp_data = resp.json()
        
        output_text = ""
        if "choices" in resp_data:
            output_text = resp_data["choices"][0].get("message",{}).get("content","")
        elif "content" in resp_data:
            output_text = resp_data.get("content","")
        
        est_output_tokens = len(output_text) // 4
        cost = round((est_input_tokens + est_output_tokens) * upstream_key["price_per_1k"] / 1000, 6)
        cost = max(cost, 0.0001)
        
        if user_balance > cost:
            cur.execute("UPDATE users SET balance=balance-? WHERE id=?", (cost, user_id))
        else:
            cur.execute("UPDATE users SET api_quota=api_quota-1 WHERE id=?", (user_id,))
            cost = 0
        
        cur.execute("INSERT INTO usage_logs (user_id, api_key_id, model, input_tokens, output_tokens, cost, called_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, upstream_key["id"], model, est_input_tokens, est_output_tokens, cost, datetime.now().isoformat()))
        conn.commit(); conn.close()
        
        resp_data["_cost"] = cost
        resp_data["_user_balance"] = user_balance - cost if user_balance >= cost else user_balance
        return resp_data
    except httpx.TimeoutException:
        conn.close()
        return JSONResponse({"error": {"message": "上游API超时", "type": "upstream_timeout"}}, status_code=504)
    except Exception as e:
        logger.error(f"代理转发异常: {e}")
        conn.close()
        return JSONResponse({"error": {"message": f"代理异常: {str(e)}", "type": "proxy_error"}}, status_code=500)

# 管理员API
@app.get("/api/admin/overview")
async def admin_overview(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403, "msg": "权限不足"})
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM users"); total_users = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM orders WHERE status='paid'"); total_orders = cur.fetchone()["c"]
    cur.execute("SELECT SUM(amount) as s FROM orders WHERE status='paid'"); total_revenue = cur.fetchone()["s"] or 0
    cur.execute("SELECT COUNT(*) as c FROM orders WHERE status='pending'"); pending = cur.fetchone()["c"]
    cur.execute("SELECT SUM(input_tokens+output_tokens) as t FROM usage_logs"); tokens = cur.fetchone()["t"] or 0
    conn.close()
    return JSONResponse({"code": 0, "data": {"total_users": total_users, "total_orders": total_orders, "total_revenue": total_revenue, "pending_orders": pending, "total_tokens": tokens, "active_users_7d": 0}})

@app.get("/api/admin/users")
async def admin_users(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403, "msg": "权限不足"})
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, phone, balance, api_quota, role, created_at, last_login FROM users ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return JSONResponse({"code": 0, "data": rows})

@app.get("/api/admin/api-keys")
async def admin_api_keys(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403, "msg": "权限不足"})
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM api_keys ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return JSONResponse({"code": 0, "data": rows})

@app.post("/api/admin/api-keys")
async def admin_add_key(request: Request, name: str = Form(...), provider: str = Form(...), api_key: str = Form(...), base_url: str = Form(...), model: str = Form(""), price_per_1k: float = Form(0)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403})
    conn = get_db(); cur = conn.cursor()
    kid = str(uuid.uuid4())
    cur.execute("INSERT INTO api_keys (id, name, provider, api_key, base_url, model, price_per_1k, is_active) VALUES (?,?,?,?,?,?,?,1)",
                (kid, name, provider, api_key, base_url, model, price_per_1k))
    conn.commit(); conn.close()
    return JSONResponse({"code": 0})

@app.delete("/api/admin/api-keys/{kid}")
async def admin_del_key(request: Request, kid: str):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403})
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    conn.commit(); conn.close()
    return JSONResponse({"code": 0})

@app.get("/api/admin/packages")
async def admin_packages(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403})
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM packages ORDER BY price ASC")
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return JSONResponse({"code": 0, "data": rows})

@app.post("/api/admin/packages")
async def admin_add_package(request: Request, name: str = Form(...), price: float = Form(...), credits: int = Form(...), description: str = Form(""), validity_days: int = Form(30)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403})
    conn = get_db(); cur = conn.cursor()
    kid = f"pkg_{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO packages (id, name, description, price, credits, validity_days, is_active) VALUES (?,?,?,?,?,?,1)",
                (kid, name, description, price, credits, validity_days))
    conn.commit(); conn.close()
    return JSONResponse({"code": 0})

@app.delete("/api/admin/packages/{pkg_id}")
async def admin_del_package(request: Request, pkg_id: str):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"code": 403})
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE packages SET is_active=0 WHERE id=?", (pkg_id,))
    conn.commit(); conn.close()
    return JSONResponse({"code": 0})

# 代理管理API
@app.get("/api/admin/agent-config")
async def api_admin_agent_config(request: Request):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    cfg = get_agent_config()
    return JSONResponse({"agent_enabled": cfg.get("agent_enabled","false"), "agent_entry_fee": float(cfg.get("agent_entry_fee","299")), "agent_default_commission_rate": float(cfg.get("agent_default_commission_rate","0.10"))})

@app.post("/api/admin/agent-config")
async def api_admin_update_agent_config(request: Request, enabled: bool = Form(False), entry_fee: float = Form(299), commission_rate: float = Form(0.10)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "权限不足"}, status_code=403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('agent_enabled',?)", (str(enabled).lower(),))
    cur.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('agent_entry_fee',?)", (str(entry_fee),))
    cur.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('agent_default_commission_rate',?)", (str(commission_rate),))
    conn.commit(); conn.close()
    return JSONResponse({"success": True})

@app.get("/api/admin/agents")
async def api_admin_list_agents(request: Request, page: int = 1, limit: int = 50):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "权限不足"}, status_code=403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM agents")
    total = cur.fetchone()["total"]
    cur.execute("SELECT a.*, u.username FROM agents a JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT ? OFFSET ?", (limit, (page-1)*limit))
    agents = [dict(r) for r in cur.fetchall()]; conn.close()
    return JSONResponse({"agents": agents, "total": total, "page": page, "limit": limit})

@app.get("/api/admin/commissions")
async def api_admin_list_commissions(request: Request, page: int = 1, limit: int = 30):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "权限不足"}, status_code=403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM agent_commissions")
    total = cur.fetchone()["total"]
    cur.execute("SELECT ac.*, u.username, ag.id as agent_code FROM agent_commissions ac LEFT JOIN users u ON ac.user_id=u.id LEFT JOIN agents ag ON ac.agent_id=ag.id ORDER BY ac.created_at DESC LIMIT ? OFFSET ?", (limit,(page-1)*limit))
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return JSONResponse({"commissions": rows, "total": total})

@app.post("/api/admin/agents/{agent_id}/commission-rate")
async def api_admin_update_agent_rate(request: Request, agent_id: str, rate: float = Form(...)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "权限不足"}, status_code=403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE agents SET commission_rate=? WHERE id=?", (rate, agent_id))
    conn.commit(); conn.close()
    return JSONResponse({"success": True})

@app.post("/api/admin/agents/{agent_id}/status")
async def api_admin_toggle_agent_status(request: Request, agent_id: str, status: str = Form(...)):
    user = require_auth(request)
    if user.get("username") != "admin":
        return JSONResponse({"error": "权限不足"}, status_code=403)
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE agents SET status=? WHERE id=?", (status, agent_id))
    conn.commit(); conn.close()
    return JSONResponse({"success": True})

# 后台页面
@app.get("/admin")
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user or user.get("username") != "admin":
        return RedirectResponse(url="/login", status_code=302)
    return render("admin.html", {}, request)

# 启动
if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8000)
MAINEOF

echo "✅ main.py 创建完成"

# 5. 写入模板文件
mkdir -p /root/api-proxy-platform/templates
mkdir -p /root/api-proxy-platform/static/css

# dashboard.html
cat > /root/api-proxy-platform/templates/dashboard.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>控制台 - API中转站</title>
<style>
body{font-family:'PingFang SC',sans-serif;background:#f0f9f4;margin:0;min-height:100vh}
.header{background:linear-gradient(135deg,#10B981,#059669);color:white;padding:24px 32px;display:flex;justify-content:space-between;align-items:center}
.header h1{margin:0;font-size:22px}.header a{color:white;text-decoration:none;background:rgba(255,255,255,0.2);padding:8px 20px;border-radius:20px;font-size:14px}
.container{max-width:1100px;margin:0 auto;padding:32px 24px}
.card{background:white;border-radius:16px;padding:28px;margin-bottom:24px;box-shadow:0 2px 8px rgba(0,0,0,0.05)}
.card h3{margin:0 0 16px;color:#1a2e1a;font-size:16px}
.balance-box{background:linear-gradient(135deg,#10B981,#059669);border-radius:16px;padding:28px;color:white;margin-bottom:24px;text-align:center}
.balance-amount{font-size:48px;font-weight:800}.balance-label{font-size:14px;opacity:0.9;margin-top:4px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.pkg{background:#f8faf8;border-radius:12px;padding:20px;border:2px solid transparent;cursor:pointer;transition:all 0.2s}
.pkg:hover{border-color:#10B981;transform:translateY(-2px)}
.pkg-name{font-weight:700;font-size:15px;color:#1a2e1a;margin-bottom:8px}
.pkg-price{font-size:28px;font-weight:800;color:#10B981}.pkg-credits{font-size:13px;color:#888;margin-top:4px}
.pkg-desc{font-size:13px;color:#666;margin:8px 0}
.btn-buy{width:100%;padding:10px;background:#10B981;color:white;border:none;border-radius:8px;cursor:pointer;font-weight:600;margin-top:12px}
.api-key-box{background:#f8faf8;border-radius:12px;padding:16px;margin-top:12px;display:flex;align-items:center;gap:12px}
.api-key{font-family:monospace;font-size:14px;color:#333;flex:1;word-break:break-all}
.copy-btn{background:#10B981;color:white;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px}
.agent-banner{background:linear-gradient(135deg,#10B981,#059669);border-radius:12px;padding:20px;color:white;margin-bottom:24px;display:flex;justify-content:space-between;align-items:center}
.agent-banner h3{margin:0 0 4px;font-size:16px}.agent-banner p{margin:0;font-size:13px;opacity:0.9}
.agent-banner a{background:rgba(255,255,255,0.25);color:white;padding:10px 24px;border-radius:10px;text-decoration:none;font-weight:600}
</style>
</head>
<body>
<div class="header">
  <h1>API中转站</h1>
  <div><a href="/">首页</a>&nbsp;<a href="/admin">管理后台</a>&nbsp;<a href="#" onclick="logout()">退出</a></div>
</div>
<div class="container">
  {% if u %}
  <div class="balance-box">
    <div class="balance-label">账户余额</div>
    <div class="balance-amount">¥{{ "%.2f"|format(u.balance) }}</div>
    <div style="font-size:14px;opacity:0.8;margin-top:8px">API配额: {{ u.api_quota }} 点</div>
  </div>
  <div class="card">
    <h3>🔑 你的 API Key</h3>
    <div class="api-key-box">
      <span class="api-key" id="apiKeyDisplay">{{ u.api_key or '无' }}</span>
      <button class="copy-btn" onclick="copyKey()">复制</button>
    </div>
    <div style="margin-top:12px;font-size:13px;color:#888">使用方式: 请求头 Authorization: Bearer 你的API Key</div>
  </div>
  {% endif %}
  <div id="agentBanner" class="agent-banner" style="display:none">
    <div><h3>🌿 代理合作伙伴</h3><p id="agentInfo">查看你的代理数据</p></div>
    <a href="/agent/dashboard">代理控制台 →</a>
  </div>
  <div id="noAgentBanner" class="card" style="display:none">
    <h3>🌿 成为代理</h3>
    <p style="color:#666">发展下线用户，获得佣金收益。<a href="/agent/register" style="color:#10B981">立即申请 →</a></p>
  </div>
  <div class="card">
    <h3>📦 选择套餐</h3>
    <div class="grid" id="pkgGrid"></div>
  </div>
</div>
<script>
function copyKey(){navigator.clipboard.writeText(document.getElementById('apiKeyDisplay').textContent).then(()=>{alert('已复制')})}
async function logout(){await fetch('/api/auth/logout',{method:'POST'});location.href='/login'}
fetch('/api/user/info').then(r=>r.json()).then(d=>{
  if(d.code===0){
    document.getElementById('apiKeyDisplay').textContent=d.data.api_key||'无';
  }
});
fetch('/api/agent/info').then(r=>r.json()).then(d=>{
  if(d.is_agent){
    document.getElementById('agentBanner').style.display='flex';
    document.getElementById('agentInfo').textContent='累计佣金: ¥'+(d.agent.total_commission||0).toFixed(2)+' | 下线: '+(d.agent.total_downlines||0)+'人';
  }else{
    document.getElementById('noAgentBanner').style.display='block';
  }
});
fetch('/api/packages').then(r=>r.json()).then(d=>{
  if(d.code===0){
    document.getElementById('pkgGrid').innerHTML=d.data.map(p=>`
      <div class="pkg" onclick="buyPkg('${p.id}')">
        <div class="pkg-name">${p.name}</div>
        <div class="pkg-price">¥${p.price}</div>
        <div class="pkg-credits">${p.credits} 点</div>
        <div class="pkg-desc">${p.description||''}</div>
        <button class="btn-buy">立即购买</button>
      </div>`).join('');
  }
});
async function buyPkg(id){
  let amount=prompt('输入充值金额（最低'+50+'元）:','100');
  if(!amount||isNaN(amount)||amount<50){alert('金额无效');return}
  const fd=new FormData();fd.append('package_id',id);
  const r=await fetch('/api/orders/create',{method:'POST',body:fd});
  const j=await r.json();
  if(j.success){if(confirm('充值 ¥'+amount+' 购买此套餐，确认支付？')){location.href=j.pay_url;}}
  else{alert(j.error||'创建订单失败');}
}
</script>
</body>
</html>
TMPL_EOF

echo "✅ dashboard.html 创建完成"

# login.html
cat > /root/api-proxy-platform/templates/login.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 - API中转站</title>
<style>
body{font-family:'PingFang SC',sans-serif;background:#f0f9f4;min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0}
.box{background:white;border-radius:20px;padding:48px;max-width:420px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.08)}
h2{margin:0 0 8px;color:#1a2e1a;text-align:center}
.sub{color:#888;text-align:center;margin-bottom:32px;font-size:14px}
.form{margin-bottom:16px}
.form input{width:100%;padding:14px 16px;border:2px solid #e5e7eb;border-radius:12px;font-size:15px;box-sizing:border-box;outline:none;transition:border-color 0.2s}
.form input:focus{border-color:#10B981}
.btn{width:100%;padding:14px;background:linear-gradient(135deg,#10B981,#059669);color:white;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer}
.btn:hover{box-shadow:0 4px 16px rgba(16,185,129,0.4)}
.link{text-align:center;margin-top:16px;font-size:14px}
.link a{color:#10B981;text-decoration:none}
</style></head>
<body>
<div class="box">
  <h2>🔑 登录</h2>
  <p class="sub">欢迎回来</p>
  <div class="form"><input type="text" id="username" placeholder="用户名" autocomplete="off"></div>
  <div class="form"><input type="password" id="password" placeholder="密码"></div>
  <button class="btn" onclick="login()">登录</button>
  <p class="link">没有账号？<a href="/register">立即注册</a></p>
</div>
<script>
async function login(){
  const u=document.getElementById('username').value.trim();
  const p=document.getElementById('password').value;
  if(!u||!p){alert('请填写完整');return}
  const fd=new FormData();fd.append('username',u);fd.append('password',p);
  const r=await fetch('/login',{method:'POST',body:fd});
  const j=await r.json();
  if(j.success){location.href=j.redirect||'/dashboard'}
  else{alert(j.error||'登录失败')}
}
document.getElementById('password').onkeydown=e=>{if(e.key==='Enter')login()}
</script>
</body>
</html>
TMPL_EOF

echo "✅ login.html 创建完成"

# register.html
cat > /root/api-proxy-platform/templates/register.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>注册 - API中转站</title>
<style>
body{font-family:'PingFang SC',sans-serif;background:#f0f9f4;min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0}
.box{background:white;border-radius:20px;padding:48px;max-width:420px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.08)}
h2{margin:0 0 8px;color:#1a2e1a;text-align:center}
.sub{color:#888;text-align:center;margin-bottom:32px;font-size:14px}
.form{margin-bottom:16px}
.form input{width:100%;padding:14px 16px;border:2px solid #e5e7eb;border-radius:12px;font-size:15px;box-sizing:border-box;outline:none;transition:border-color 0.2s}
.form input:focus{border-color:#10B981}
.btn{width:100%;padding:14px;background:linear-gradient(135deg,#10B981,#059669);color:white;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer}
.link{text-align:center;margin-top:16px;font-size:14px}
.link a{color:#10B981;text-decoration:none}
</style></head>
<body>
<div class="box">
  <h2>✨ 注册</h2>
  <p class="sub">创建你的账户</p>
  <div class="form"><input type="text" id="username" placeholder="用户名" autocomplete="off"></div>
  <div class="form"><input type="password" id="password" placeholder="密码"></div>
  <div class="form"><input type="text" id="referral" placeholder="推荐码（选填）" autocomplete="off"></div>
  <button class="btn" onclick="register()">注册</button>
  <p class="link">已有账号？<a href="/login">立即登录</a></p>
</div>
<script>
async function register(){
  const u=document.getElementById('username').value.trim();
  const p=document.getElementById('password').value;
  const ref=document.getElementById('referral').value.trim();
  if(!u||!p){alert('请填写完整');return}
  const fd=new FormData();fd.append('username',u);fd.append('password',p);fd.append('referral_code',ref);
  const r=await fetch('/register',{method:'POST',body:fd});
  const j=await r.json();
  if(j.success){location.href=j.redirect||'/dashboard'}
  else{alert(j.error||'注册失败')}
}
</script>
</body>
</html>
TMPL_EOF

echo "✅ register.html 创建完成"

# index.html (首页)
cat > /root/api-proxy-platform/templates/index.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>API中转站</title>
<style>
body{font-family:'PingFang SC',sans-serif;background:#f0f9f4;margin:0;min-height:100vh}
.hero{background:linear-gradient(135deg,#10B981,#059669);color:white;padding:80px 32px;text-align:center}
.hero h1{font-size:48px;margin:0 0 16px}
.hero p{font-size:18px;opacity:0.9;margin:0 0 32px}
.hero a{display:inline-block;background:white;color:#10B981;padding:14px 40px;border-radius:30px;text-decoration:none;font-weight:700;font-size:16px}
.content{max-width:1100px;margin:0 auto;padding:64px 24px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-bottom:48px}
.grid>div{background:white;border-radius:16px;padding:28px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,0.05)}
.grid h3{color:#1a2e1a;margin:12px 0 8px}
.grid p{color:#888;font-size:14px;margin:0}
.footer{text-align:center;padding:32px;color:#888;font-size:14px}
</style></head>
<body>
<div class="hero">
  <h1>🚀 API中转站</h1>
  <p>低价格调用各大AI模型，稳定、快速、便宜</p>
  <a href="/register">免费注册 →</a>
</div>
<div class="content">
  <div class="grid">
    <div><h3>⚡ 极速响应</h3><p>优化的转发链路，保证API调用响应速度</p></div>
    <div><h3>💰 价格实惠</h3><p>比官方更低的价格，充值越多越便宜</p></div>
    <div><h3>🔒 安全可靠</h3><p>账户余额实时查询，API调用透明计费</p></div>
  </div>
  <div style="text-align:center">
    <a href="/login" style="color:#10B981;text-decoration:none;margin:0 16px">登录</a>
    <a href="/register" style="color:#10B981;text-decoration:none;margin:0 16px">注册</a>
  </div>
</div>
<div class="footer">© 2024 API中转站</div>
</body>
</html>
TMPL_EOF

echo "✅ index.html 创建完成"

# agent_register.html
cat > /root/api-proxy-platform/templates/agent_register.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>代理申请 - API中转站</title>
<style>
body{background:#f0f9f4;min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0;font-family:'PingFang SC',sans-serif}
.card{background:white;border-radius:20px;padding:48px;max-width:520px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.08)}
.badge{background:linear-gradient(135deg,#10B981,#059669);color:white;padding:4px 16px;border-radius:20px;font-size:13px;font-weight:600;display:inline-block;margin-bottom:16px}
h1{margin:0 0 8px;color:#1a2e1a;font-size:28px}
.sub{color:#666;margin-bottom:32px;font-size:15px}
.fee-box{background:linear-gradient(135deg,#ECFDF5,#D1FAE5);border-radius:16px;padding:24px;text-align:center;margin-bottom:32px;border:2px solid #10B981}
.fee-label{font-size:14px;color:#065f46;font-weight:600;margin-bottom:4px}
.fee-amount{font-size:48px;font-weight:800;color:#10B981}
.fee-unit{font-size:16px;color:#065f46}
.btn{width:100%;padding:16px;background:linear-gradient(135deg,#10B981,#059669);color:white;border:none;border-radius:12px;font-size:17px;font-weight:700;cursor:pointer}
</style></head>
<body>
<div class="card">
  <div class="badge">🌿 代理计划</div>
  <h1>成为代理合作伙伴</h1>
  <p class="sub">发展下线用户，赚取持续佣金收益</p>
  <div class="fee-box">
    <div class="fee-label">入驻费用</div>
    <div class="fee-amount">¥{{ entry_fee }}<span class="fee-unit"> 元</span></div>
  </div>
  <button class="btn" id="applyBtn" onclick="applyAgent()">立即申请代理资格 →</button>
</div>
<script>
async function applyAgent(){
  const btn=document.getElementById('applyBtn');
  btn.disabled=true;btn.textContent='创建订单中...';
  const fd=new FormData();
  const r=await fetch('/api/agent/apply',{method:'POST',body:fd});
  const d=await r.json();
  if(d.success){location.href=d.pay_url}
  else{alert(d.error||'申请失败');btn.disabled=false;btn.textContent='立即申请代理资格 →'}
}
</script>
</body>
</html>
TMPL_EOF

echo "✅ agent_register.html 创建完成"

# agent_dashboard.html
cat > /root/api-proxy-platform/templates/agent_dashboard.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>代理控制台 - API中转站</title>
<style>
body{background:#f0f9f4;min-height:100vh;margin:0;font-family:'PingFang SC',sans-serif}
.header{background:linear-gradient(135deg,#10B981,#059669);color:white;padding:24px 32px;display:flex;justify-content:space-between;align-items:center}
.header h1{margin:0;font-size:22px;font-weight:700}
.header a{color:white;text-decoration:none;background:rgba(255,255,255,0.2);padding:8px 20px;border-radius:20px;font-size:14px}
.container{max-width:1100px;margin:0 auto;padding:32px 24px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:32px}
.stat{background:white;border-radius:16px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.05)}
.stat-label{font-size:13px;color:#888;margin-bottom:8px;font-weight:500}
.stat-value{font-size:28px;font-weight:800;color:#1a2e1a}
.stat-value.green{color:#10B981}
.stat-value.blue{color:#3B82F6}
.referral-box{background:linear-gradient(135deg,#10B981,#059669);border-radius:16px;padding:28px;color:white;margin-bottom:32px;display:flex;justify-content:space-between;align-items:center}
.referral-code{font-size:32px;font-weight:800;letter-spacing:4px;font-family:'Courier New',monospace;margin:8px 0}
.copy-btn{background:rgba(255,255,255,0.25);border:2px solid rgba(255,255,255,0.5);color:white;padding:12px 28px;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer}
table{width:100%;border-collapse:collapse;background:white;border-radius:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.05)}
th{text-align:left;padding:14px 16px;background:#f8faf8;color:#666;font-weight:600;font-size:13px}
td{padding:14px 16px;border-bottom:1px solid #f0f0f0;color:#374151;font-size:14px}
</style></head>
<body>
<div class="header">
  <h1>🌿 代理控制台</h1>
  <a href="/dashboard">← 返回用户面板</a>
</div>
<div class="container">
  <div class="stats">
    <div class="stat"><div class="stat-label">累计佣金收益</div><div class="stat-value green">¥{{ "%.2f"|format(total_commission) }}</div></div>
    <div class="stat"><div class="stat-label">佣金比例</div><div class="stat-value blue">{{ commission_rate }}%</div></div>
    <div class="stat"><div class="stat-label">代理推荐码</div><div class="stat-value" style="font-size:20px;letter-spacing:2px">{{ agent.referral_code }}</div></div>
  </div>
  <div class="referral-box">
    <div>
      <h3>🔗 你的专属推荐码</h3>
      <div class="referral-code">{{ agent.referral_code }}</div>
      <div style="font-size:13px;opacity:0.8">新用户注册时填入此推荐码，自动成为你的下线</div>
    </div>
    <button class="copy-btn" onclick="copyCode()">📋 复制推荐码</button>
  </div>
  <div style="background:white;border-radius:16px;padding:28px;margin-bottom:24px">
    <h3 style="margin:0 0 16px">💰 佣金记录</h3>
    {% if commissions %}
    <table><thead><tr><th>序号</th><th>下线用户</th><th>充值金额</th><th>佣金收益</th><th>时间</th></tr></thead>
    <tbody>
    {% for c in commissions %}
    <tr><td>{{ loop.index }}</td><td>{{ c.username or c.user_id }}</td><td>¥{{ "%.2f"|format(c.recharge_amount) }}</td><td style="color:#10B981;font-weight:700">+¥{{ "%.4f"|format(c.commission) }}</td><td style="color:#aaa;font-size:13px">{{ c.created_at[:16] }}</td></tr>
    {% endfor %}
    </tbody></table>
    {% else %}
    <p style="color:#aaa;text-align:center;padding:32px">暂无佣金记录，<a href="/dashboard" style="color:#10B981">去发展下线 →</a></p>
    {% endif %}
  </div>
</div>
<script>
function copyCode(){navigator.clipboard.writeText("{{ agent.referral_code }}").then(()=>{const b=document.querySelector('.copy-btn');b.textContent='✅ 已复制!';setTimeout(()=>b.textContent='📋 复制推荐码',2000)})}
</script>
</body></html>
TMPL_EOF

echo "✅ agent_dashboard.html 创建完成"

# admin.html
cat > /root/api-proxy-platform/templates/admin.html << 'TMPL_EOF'
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>管理后台 - API中转站</title>
<style>
:root{--p:#6366F1;--pd:#4F46E5;--s:#10B981;--w:#F59E0B;--d:#EF4444;--bg:#F1F5F9;--srf:#fff;--t:#1E293B;--tl:#64748B;--b:#E2E8F0}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--t)}
nav{background:var(--srf);border-bottom:1px solid var(--b);padding:0 24px;height:64px;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:1.2rem;font-weight:800;color:var(--p);text-decoration:none}
.nav-right{display:flex;align-items:center;gap:16px}
.nav-right a{color:var(--tl);text-decoration:none;font-size:0.9rem}
.nav-right a:hover{color:var(--p)}
.layout{display:flex;min-height:calc(100vh - 64px)}
.sidebar{width:220px;background:var(--srf);border-right:1px solid var(--b);padding:24px 0;flex-shrink:0}
.sidebar-item{display:block;padding:11px 24px;color:var(--tl);text-decoration:none;font-size:0.9rem;font-weight:500;transition:all 0.2s;border-left:3px solid transparent}
.sidebar-item:hover,.sidebar-item.active{background:#EEF2FF;color:var(--p);border-left-color:var(--p)}
.main{flex:1;padding:32px;overflow:auto}
.page{display:none}.page.active{display:block}
.page-header{margin-bottom:24px}.page-header h2{font-size:1.4rem;font-weight:800}
.card{background:var(--srf);border-radius:16px;padding:28px;border:1px solid var(--b);margin-bottom:24px}
.card-title{font-size:1.05rem;font-weight:700;margin-bottom:20px}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.stat-card{background:var(--srf);border-radius:16px;padding:20px;border:1px solid var(--b)}
.stat-label{font-size:0.82rem;color:var(--tl);margin-bottom:6px}
.stat-value{font-size:1.8rem;font-weight:800;color:var(--p)}
.stat-value.green{color:var(--s)}
.stat-value.orange{color:var(--w)}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:0.82rem;color:var(--tl);font-weight:600;padding:10px 12px;border-bottom:2px solid var(--b)}
td{padding:12px;font-size:0.88rem;border-bottom:1px solid var(--b)}
tr:hover td{background:#F8FAFC}
.badge{padding:3px 10px;border-radius:12px;font-size:0.78rem;font-weight:600}
.badge-green{background:#DCFCE7;color:#166534}
.badge-red{background:#FEE2E2;color:#991B1B}
.form-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}
.form-item{display:flex;flex-direction:column;gap:6px}
.form-item label{font-size:0.82rem;font-weight:600;color:#374151}
.form-item input{width:100%;padding:10px 14px;border:2px solid var(--b);border-radius:10px;font-size:0.9rem;font-family:inherit;outline:none}
.form-item input:focus{border-color:var(--p)}
.btn{padding:10px 20px;border-radius:10px;font-size:0.88rem;font-weight:700;cursor:pointer;font-family:inherit;border:none;transition:all 0.2s}
.btn-primary{background:var(--p);color:white}
.btn-primary:hover{background:var(--pd)}
.del-btn{background:none;border:1px solid var(--b);padding:4px 10px;border-radius:6px;cursor:pointer;font-size:0.8rem;color:var(--d);font-family:inherit}
.del-btn:hover{background:#FEE2E2;border-color:var(--d)}
.alert{padding:14px 18px;border-radius:12px;margin-bottom:20px;font-size:0.9rem}
.alert-red{background:#FEE2E2;border:1px solid #FECACA;color:#991B1B}
.alert-green{background:#DCFCE7;border:1px solid #BBF7D0;color:#166534}
</style></head>
<body>
<nav><a href="/" class="logo">⚡ API中转站 - 管理后台</a>
  <div class="nav-right"><a href="/dashboard">← 返回用户端</a><a href="#" onclick="logout()">退出</a></div>
</nav>
<div class="layout">
  <div class="sidebar">
    <a href="#" class="sidebar-item active" onclick="showPage('overview')">📊 数据概览</a>
    <a href="#" class="sidebar-item" onclick="showPage('users')">👥 用户管理</a>
    <a href="#" class="sidebar-item" onclick="showPage('apikeys')">🔑 上游Key管理</a>
    <a href="#" class="sidebar-item" onclick="showPage('packages')">📦 套餐管理</a>
    <a href="#" class="sidebar-item" onclick="showPage('agents')">🌿 代理管理</a>
    <a href="#" class="sidebar-item" onclick="showPage('agent-config')">⚙️ 代理配置</a>
  </div>
  <div class="main">
    <div class="page active" id="page-overview">
      <div class="page-header"><h2>数据概览</h2></div>
      <div class="stats-grid">
        <div class="stat-card"><div class="stat-label">总用户数</div><div class="stat-value" id="sUsers">-</div></div>
        <div class="stat-card"><div class="stat-label">总收入(元)</div><div class="stat-value green" id="sRevenue">-</div></div>
        <div class="stat-card"><div class="stat-label">总订单数</div><div class="stat-value orange" id="sOrders">-</div></div>
        <div class="stat-card"><div class="stat-label">待处理订单</div><div class="stat-value orange" id="sPending">-</div></div>
      </div>
    </div>
    <div class="page" id="page-users">
      <div class="page-header"><h2>用户管理</h2></div>
      <div class="card"><table id="usersTable"><thead><tr><th>用户名</th><th>余额</th><th>配额</th><th>角色</th><th>注册时间</th></tr></thead><tbody></tbody></table></div>
    </div>
    <div class="page" id="page-apikeys">
      <div class="page-header"><h2>上游 API Key 管理</h2></div>
      <div class="card alert alert-red" style="margin-bottom:20px">⚠️ 上游API Key 用于实际转发请求，请妥善保管</div>
      <div class="card">
        <div class="card-title">添加上游 Key</div>
        <div class="form-grid" id="keyForm">
          <div class="form-item"><label>名称</label><input name="name" placeholder="如：MiniMax主Key"></div>
          <div class="form-item"><label>供应商</label><input name="provider" placeholder="如：minimax"></div>
          <div class="form-item"><label>API Key</label><input name="api_key" placeholder="sk-xxxx"></div>
          <div class="form-item"><label>Base URL</label><input name="base_url" placeholder="https://api.minimax.chat/v1"></div>
          <div class="form-item"><label>默认模型</label><input name="model" placeholder="MiniMax-Text-01"></div>
          <div class="form-item"><label>单价(元/千Token)</label><input name="price_per_1k" type="number" step="0.001" value="0"></div>
        </div>
        <button class="btn btn-primary" onclick="addApiKey()">添加 Key</button>
        <div id="keyMsg" class="alert" style="display:none;margin-top:16px"></div>
      </div>
      <div class="card"><table id="keysTable"><thead><tr><th>名称</th><th>供应商</th><th>模型</th><th>单价</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table></div>
    </div>
    <div class="page" id="page-packages">
      <div class="page-header"><h2>套餐管理</h2></div>
      <div class="card">
        <div class="card-title">添加套餐</div>
        <div class="form-grid" id="pkgForm">
          <div class="form-item"><label>套餐名称</label><input name="name" placeholder="如：基础版"></div>
          <div class="form-item"><label>价格(元)</label><input name="price" type="number" step="0.01"></div>
          <div class="form-item"><label>额度点数</label><input name="credits" type="number"></div>
          <div class="form-item"><label>描述</label><input name="description" placeholder="适合轻度使用"></div>
          <div class="form-item"><label>有效期(天)</label><input name="validity_days" type="number" value="30"></div>
        </div>
        <button class="btn btn-primary" onclick="addPackage()">添加套餐</button>
        <div id="pkgMsg" class="alert" style="display:none;margin-top:16px"></div>
      </div>
      <div class="card"><table id="pkgsTable"><thead><tr><th>名称</th><th>价格</th><th>额度</th><th>有效期</th><th>操作</th></tr></thead><tbody></tbody></table></div>
    </div>
    <div class="page" id="page-agents">
      <div class="page-header"><h2>🌿 代理管理</h2></div>
      <div class="card"><table id="agentsTable"><thead><tr><th>用户名</th><th>推荐码</th><th>佣金比例</th><th>累计佣金</th><th>下线数</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody></tbody></table></div>
      <div class="card"><div class="card-title">💰 佣金记录</div><table id="commissionsTable"><thead><tr><th>代理</th><th>下线</th><th>充值金额</th><th>佣金</th><th>时间</th></tr></thead><tbody></tbody></table></div>
    </div>
    <div class="page" id="page-agent-config">
      <div class="page-header"><h2>⚙️ 代理系统配置</h2></div>
      <div class="card">
        <div class="card-title">基础设置</div>
        <div class="form-grid">
          <div class="form-item"><label>代理入驻费（元）</label><input id="cfg-entry-fee" type="number" step="1" value="299"></div>
          <div class="form-item"><label>佣金比例（0.10=10%）</label><input id="cfg-commission-rate" type="number" step="0.01" value="0.10"></div>
          <div class="form-item"><label>开启代理系统</label>
            <select id="cfg-enabled"><option value="true">✅ 开启</option><option value="false">❌ 关闭</option></select>
          </div>
        </div>
        <button class="btn btn-primary" onclick="saveAgentConfig()">保存配置</button>
        <div id="cfgMsg" class="alert" style="display:none;margin-top:16px"></div>
      </div>
      <div class="card alert alert-green" style="line-height:1.8">
        <strong>代理入驻费：</strong>用户缴费后才能成为代理<br>
        <strong>佣金比例：</strong>下线充值时代理获得的分成比例
      </div>
    </div>
  </div>
</div>
<script>
async function checkAdmin(){const r=await fetch('/api/user/info');const j=await r.json();if(j.code!==0||j.data.role!=='admin')location.href='/login'}
async function loadOverview(){const r=await fetch('/api/admin/overview');const j=await r.json();if(j.code!==0)return;const d=j.data;document.getElementById('sUsers').textContent=d.total_users;document.getElementById('sRevenue').textContent='¥'+d.total_revenue.toFixed(2);document.getElementById('sOrders').textContent=d.total_orders;document.getElementById('sPending').textContent=d.pending_orders}
async function loadUsers(){const r=await fetch('/api/admin/users');const j=await r.json();if(j.code!==0)return;document.querySelector('#usersTable tbody').innerHTML=j.data.map(u=>'<tr><td><strong>'+u.username+'</strong></td><td>¥'+parseFloat(u.balance||0).toFixed(2)+'</td><td>'+u.api_quota+'</td><td><span class="badge '+(u.role==='admin'?'badge-red':'badge-green')+'">'+u.role+'</span></td><td>'+(u.created_at||'').substring(0,10)+'</td></tr>').join('')}
async function loadApiKeys(){const r=await fetch('/api/admin/api-keys');const j=await r.json();if(j.code!==0)return;document.querySelector('#keysTable tbody').innerHTML=j.data.map(k=>'<tr><td>'+k.name+'</td><td>'+k.provider+'</td><td>'+(k.model||'-')+'</td><td>¥'+parseFloat(k.price_per_1k||0).toFixed(4)+'</td><td><span class="badge '+(k.is_active?'badge-green':'badge-red')+'">'+(k.is_active?'启用':'禁用')+'</span></td><td><button class="del-btn" onclick="delApiKey(\''+k.id+'\')">删除</button></td></tr>').join('')}
async function addApiKey(){const fd=new FormData();['name','provider','api_key','base_url','model'].forEach(n=>fd.append(n,document.querySelector('[name="'+n+'"]').value));fd.append('price_per_1k',parseFloat(document.querySelector('[name="price_per_1k"]').value||0));const r=await fetch('/api/admin/api-keys',{method:'POST',body:fd});const j=await r.json();const m=document.getElementById('keyMsg');m.textContent=j.code===0?'添加成功':(j.msg||'失败');m.className='alert '+(j.code===0?'alert-green':'alert-red');m.style.display='block';if(j.code===0)loadApiKeys();setTimeout(()=>m.style.display='none',3000)}
async function delApiKey(id){if(confirm('确认删除?')){await fetch('/api/admin/api-keys/'+id,{method:'DELETE'});loadApiKeys()}}
async function loadPackages(){const r=await fetch('/api/admin/packages');const j=await r.json();if(j.code!==0)return;document.querySelector('#pkgsTable tbody').innerHTML=j.data.map(p=>'<tr><td><strong>'+p.name+'</strong></td><td>¥'+parseFloat(p.price).toFixed(2)+'</td><td>'+p.credits+'</td><td>'+p.validity_days+'天</td><td><button class="del-btn" onclick="delPackage(\''+p.id+'\')">禁用</button></td></tr>').join('')}
async function addPackage(){const fd=new FormData();['name','description'].forEach(n=>fd.append(n,document.querySelector('#pkgForm [name="'+n+'"]').value));fd.append('price',parseFloat(document.querySelector('#pkgForm [name="price"]').value||0));fd.append('credits',parseInt(document.querySelector('#pkgForm [name="credits"]').value||0));fd.append('validity_days',parseInt(document.querySelector('#pkgForm [name="validity_days"]').value||30));const r=await fetch('/api/admin/packages',{method:'POST',body:fd});const j=await r.json();const m=document.getElementById('pkgMsg');m.textContent=j.code===0?'添加成功':(j.msg||'失败');m.className='alert '+(j.code===0?'alert-green':'alert-red');m.style.display='block';if(j.code===0)loadPackages();setTimeout(()=>m.style.display='none',3000)}
async function delPackage(id){if(confirm('确认禁用此套餐?')){await fetch('/api/admin/packages/'+id,{method:'DELETE'});loadPackages()}}
async function loadAgentConfig(){const r=await fetch('/api/admin/agent-config');const j=await r.json();if(j.error)return;document.getElementById('cfg-entry-fee').value=j.agent_entry_fee;document.getElementById('cfg-commission-rate').value=j.agent_default_commission_rate;document.getElementById('cfg-enabled').value=j.agent_enabled}
async function saveAgentConfig(){const fd=new FormData();fd.append('enabled',document.getElementById('cfg-enabled').value==='true');fd.append('entry_fee',parseFloat(document.getElementById('cfg-entry-fee').value));fd.append('commission_rate',parseFloat(document.getElementById('cfg-commission-rate').value));const r=await fetch('/api/admin/agent-config',{method:'POST',body:fd});const j=await r.json();const m=document.getElementById('cfgMsg');m.textContent=j.success?'配置已保存':(j.error||'保存失败');m.className='alert '+(j.success?'alert-green':'alert-red');m.style.display='block';setTimeout(()=>m.style.display='none',3000)}
async function loadAgents(){const r=await fetch('/api/admin/agents?limit=50');const j=await r.json();document.querySelector('#agentsTable tbody').innerHTML=(j.agents||[]).map(a=>'<tr><td><strong>'+a.username+'</strong></td><td><span style="font-family:monospace;color:#10B981;font-weight:700">'+a.referral_code+'</span></td><td>'+((a.commission_rate||0.10)*100).toFixed(0)+'%</td><td style="color:#10B981;font-weight:700">¥'+(a.total_commission||0).toFixed(4)+'</td><td>'+(a.total_downlines||0)+'</td><td><span class="badge '+(a.status==='active'?'badge-green':'badge-red')+'">'+(a.status==='active'?'正常':'禁用')+'</span></td><td>'+(a.created_at||'').substring(0,10)+'</td><td><button class="del-btn" onclick="toggleAgent(\''+a.id+'\',\''+a.status+'\')">'+(a.status==='active'?'禁用':'启用')+'</button></td></tr>').join('')||'<tr><td colspan="8" style="text-align:center;color:#aaa">暂无代理</td></tr>'}
async function loadCommissions(){const r=await fetch('/api/admin/commissions?limit=30');const j=await r.json();document.querySelector('#commissionsTable tbody').innerHTML=(j.commissions||[]).map(c=>'<tr><td>'+(c.agent_code||c.agent_id||'').substring(0,12)+'</td><td>'+(c.username||c.user_id||'-')+'</td><td>¥'+(c.recharge_amount||0).toFixed(2)+'</td><td style="color:#10B981;font-weight:700">+¥'+(c.commission||0).toFixed(4)+'</td><td style="color:#aaa;font-size:12px">'+(c.created_at||'').substring(0,16)+'</td></tr>').join('')||'<tr><td colspan="5" style="text-align:center;color:#aaa">暂无记录</td></tr>'}
function toggleAgent(id,status){if(!confirm('确认'+(status==='active'?'禁用':'启用')+'?'))return;fetch('/api/admin/agents/'+id+'/status',{method:'POST',body:new URLSearchParams({status:status==='active'?'disabled':'active'})}).then(()=>loadAgents())}
function showPage(name){document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));document.querySelectorAll('.sidebar-item').forEach(a=>a.classList.remove('active'));document.getElementById('page-'+name).classList.add('active');if(event&&event.target)event.target.classList.add('active');if(name==='overview')loadOverview();if(name==='users')loadUsers();if(name==='apikeys')loadApiKeys();if(name==='packages')loadPackages();if(name==='agents'){loadAgents();loadCommissions()};if(name==='agent-config')loadAgentConfig()}
async function logout(){await fetch('/api/auth/logout',{method:'POST'});location.href='/login'}
checkAdmin().then(()=>{loadOverview();loadUsers();loadApiKeys();loadPackages()});
</script>
</body></html>
TMPL_EOF

echo "✅ admin.html 创建完成"

# 6. 启动服务
echo ""
echo "🚀 启动服务..."
cd /root/api-proxy-platform
nohup python3 main.py > /root/api-proxy-platform/app.log 2>&1 &
sleep 3
if pgrep -f "python3 main.py" > /dev/null; then
    echo "✅ 服务启动成功!"
    echo "🌐 访问地址: http://124.156.196.238:8000"
    echo "🔐 管理后台: http://124.156.196.238:8000/admin"
    echo "👤 管理员账号: admin / admin"
else
    echo "❌ 服务启动失败，查看日志:"
    tail -30 /root/api-proxy-platform/app.log
fi
EOF

chmod +x /root/api-proxy-platform/setup.sh

echo ""
echo "========================================="
echo "✅ 部署脚本已创建!"
echo ""
echo "下一步，在服务器上执行："
echo ""
echo "  cd /root/api-proxy-platform && bash setup.sh"
echo ""
echo "========================================="
