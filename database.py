import sqlite3
import uuid
import time
from datetime import datetime, timedelta

DB_PATH = "platform.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # 用户表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            balance REAL DEFAULT 0,
            role TEXT DEFAULT 'user',
            api_key TEXT,
            api_quota INTEGER DEFAULT 0,
            created_at TEXT,
            last_login TEXT
        )
    """)

    # 套餐表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS packages (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            credits INTEGER NOT NULL,
            validity_days INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    # 订单表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            package_id TEXT,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            pay_no TEXT,
            alipay_trade_no TEXT,
            created_at TEXT,
            paid_at TEXT,
            remark TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # API密钥表（管理员配置的上游key）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            api_key TEXT NOT NULL,
            base_url TEXT,
            model TEXT,
            price_per_1k REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    # 用量记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            api_key_id TEXT,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            called_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # 充值记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recharge_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT DEFAULT 'alipay',
            status TEXT DEFAULT 'success',
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # 代理表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            parent_agent_id TEXT,
            referral_code TEXT UNIQUE NOT NULL,
            commission_rate REAL DEFAULT 0.10,
            status TEXT DEFAULT 'active',
            total_commission REAL DEFAULT 0,
            total_downlines INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(parent_agent_id) REFERENCES agents(id)
        )
    """)

    # 代理佣金记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT,
            recharge_amount REAL DEFAULT 0,
            commission REAL NOT NULL,
            level INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(order_id) REFERENCES orders(id)
        )
    """)

    # 管理员配置
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # 初始化默认套餐
    cur.execute("SELECT COUNT(*) FROM packages")
    if cur.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        packages = [
            (str(uuid.uuid4()), "体验版", "适合个人开发者测试", 9.9, 100, 7, now),
            (str(uuid.uuid4()), "基础版", "适合轻度使用", 49.0, 1000, 30, now),
            (str(uuid.uuid4()), "进阶版", "适合创业团队", 199.0, 5000, 30, now),
            (str(uuid.uuid4()), "旗舰版", "适合企业级使用", 599.0, 20000, 30, now),
            (str(uuid.uuid4()), "定制版", "联系客服定制方案", 0, 0, 30, now),
        ]
        cur.executemany(
            "INSERT INTO packages VALUES (?,?,?,?,?,?,?,1)",
            packages
        )

    # 初始化管理员
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    if cur.fetchone()[0] == 0:
        import hashlib
        admin_id = str(uuid.uuid4())
        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        now = datetime.now().isoformat()
        cur.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (admin_id, "admin", pw_hash, "admin@apihub.com", "", 0, "admin",
             str(uuid.uuid4()), 0, now, None)
        )
        # 默认api key（示例）
        cur.execute("SELECT COUNT(*) FROM api_keys")
        if cur.fetchone()[0] == 0:
            now = datetime.now().isoformat()
            default_keys = [
                (str(uuid.uuid4()), "MiniMax Proxy", "minimax", "YOUR_MINIMAX_KEY",
                 "https://api.minimax.chat/v1", "MiniMax-Text-01", 0.001, 1, now),
                (str(uuid.uuid4()), "DeepSeek Proxy", "deepseek", "YOUR_DEEPSEEK_KEY",
                 "https://api.deepseek.com/v1", "deepseek-chat", 0.000, 1, now),
                (str(uuid.uuid4()), "火山引擎 Proxy", "volcengine", "YOUR_VOLCENGINE_KEY",
                 "https://ark.cn-beijing.volces.com/api/v3", "doubao-pro", 0.003, 1, now),
            ]
            cur.executemany(
                "INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,?,?)",
                default_keys
            )

    # 初始化配置
    cur.execute("SELECT COUNT(*) FROM config")
    if cur.fetchone()[0] == 0:
        configs = [
            ("platform_name", "API智 Hub"),
            ("platform_logo", ""),
            ("contact_wechat", "apihub001"),
            ("contact_email", "support@apihub.com"),
            ("alipay_app_id", ""),
            ("alipay_private_key", ""),
            ("alipay_public_key", ""),
            ("default_markup", "1.3"),
            ("agent_entry_fee", "299"),
            ("agent_default_commission_rate", "0.10"),
            ("agent_enabled", "true"),
        ]
        cur.executemany("INSERT INTO config VALUES (?,?)", configs)

    conn.commit()
    conn.close()
    print("[DB] 数据库初始化完成")

init_db()
