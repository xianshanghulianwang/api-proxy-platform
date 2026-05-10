"""
终点平台 - 数据库模块
"""
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

    # ==================== 用户表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            email_verified INTEGER DEFAULT 0,
            phone TEXT,
            phone_verified INTEGER DEFAULT 0,
            balance REAL DEFAULT 0,
            role TEXT DEFAULT 'user',
            user_type TEXT DEFAULT 'customer',
            agent_id TEXT,
            api_key TEXT,
            api_quota INTEGER DEFAULT 0,
            created_at TEXT,
            last_login TEXT
        )
    """)

    # ==================== 代理等级表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_tiers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            commission_l1 REAL DEFAULT 0.10,
            commission_l2 REAL DEFAULT 0.05,
            commission_l3 REAL DEFAULT 0.02,
            max_agents INTEGER DEFAULT 0,
            current_agents INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # ==================== 代理商表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            parent_agent_id TEXT,
            referral_code TEXT UNIQUE NOT NULL,
            tier_id TEXT,
            store_name TEXT,
            commission_rate_l1 REAL DEFAULT 0.10,
            commission_rate_l2 REAL DEFAULT 0.05,
            commission_rate_l3 REAL DEFAULT 0.02,
            markup_rate REAL DEFAULT 1.5,
            total_commission REAL DEFAULT 0,
            withdrawable_balance REAL DEFAULT 0,
            total_withdrawal REAL DEFAULT 0,
            total_downlines INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(tier_id) REFERENCES agent_tiers(id)
        )
    """)

    # ==================== 代理上游API表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_upstream_apis (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            api_key TEXT NOT NULL,
            base_url TEXT,
            model TEXT,
            price_per_1k REAL DEFAULT 0,
            markup_rate REAL DEFAULT 1.0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)

    # ==================== 佣金规则表（后台配置）====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS commission_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tier_id TEXT,
            level INTEGER DEFAULT 1,
            rate REAL DEFAULT 0.10,
            created_at TEXT,
            FOREIGN KEY(tier_id) REFERENCES agent_tiers(id)
        )
    """)

    # ==================== 佣金记录表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT,
            recharge_amount REAL DEFAULT 0,
            commission REAL NOT NULL,
            level INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ==================== 提现记录表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            amount REAL NOT NULL,
            bank_name TEXT,
            bank_account TEXT,
            bank_holder TEXT,
            status TEXT DEFAULT 'pending',
            admin_remark TEXT,
            created_at TEXT,
            processed_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)

    # ==================== 邮箱验证码表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            purpose TEXT DEFAULT 'register',
            used INTEGER DEFAULT 0,
            expires_at TEXT,
            created_at TEXT
        )
    """)

    # ==================== 套餐表 ====================
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

    # ==================== 订单表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            package_id TEXT,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            pay_no TEXT,
            alipay_trade_no TEXT,
            paid_at TEXT,
            created_at TEXT,
            remark TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ==================== 充值记录表 ====================
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

    # ==================== 平台API密钥表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            api_key TEXT NOT NULL,
            base_url TEXT,
            model TEXT,
            price_per_1k REAL DEFAULT 0,
            markup_rate REAL DEFAULT 1.0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)

    # ==================== 用量记录表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            api_key_id TEXT,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            called_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)

    # ==================== 管理员配置表 ====================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ==================== 初始化数据 ====================
    
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

    # 初始化代理等级（饥饿营销）
    cur.execute("SELECT COUNT(*) FROM agent_tiers")
    if cur.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        tiers = [
            (str(uuid.uuid4()), "铜牌代理", 199.0, 0.10, 0.05, 0.02, 100, 0, 1, 1, now),
            (str(uuid.uuid4()), "银牌代理", 399.0, 0.15, 0.08, 0.03, 50, 0, 1, 2, now),
            (str(uuid.uuid4()), "金牌代理", 699.0, 0.20, 0.10, 0.05, 20, 0, 1, 3, now),
            (str(uuid.uuid4()), "钻石代理", 999.0, 0.25, 0.12, 0.05, 5, 0, 1, 4, now),
        ]
        cur.executemany(
            "INSERT INTO agent_tiers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tiers
        )

    # 初始化管理员
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    if cur.fetchone()[0] == 0:
        import hashlib
        admin_id = str(uuid.uuid4())
        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        now = datetime.now().isoformat()
        cur.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (admin_id, "admin", pw_hash, "admin@zhongdian.com", 1, "", 0, "admin", "customer", None,
             str(uuid.uuid4()), 0, now, None)
        )

    # 初始化平台API密钥（示例）
    cur.execute("SELECT COUNT(*) FROM api_keys WHERE agent_id IS NULL")
    if cur.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        default_keys = [
            (str(uuid.uuid4()), None, "MiniMax Proxy", "minimax", "YOUR_MINIMAX_KEY",
             "https://api.minimax.chat/v1", "MiniMax-Text-01", 0.001, 1.0, 1, now),
            (str(uuid.uuid4()), None, "DeepSeek Proxy", "deepseek", "YOUR_DEEPSEEK_KEY",
             "https://api.deepseek.com/v1", "deepseek-chat", 0.000, 1.0, 1, now),
            (str(uuid.uuid4()), None, "火山引擎 Proxy", "volcengine", "YOUR_VOLCENGINE_KEY",
             "https://ark.cn-beijing.volces.com/api/v3", "doubao-pro", 0.003, 1.0, 1, now),
        ]
        cur.executemany(
            "INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            default_keys
        )

    # 初始化配置
    cur.execute("SELECT COUNT(*) FROM config")
    if cur.fetchone()[0] == 0:
        configs = [
            ("platform_name", "终点"),
            ("platform_logo", ""),
            ("contact_wechat", "zhongdian001"),
            ("contact_email", "support@zhongdian.com"),
            ("alipay_app_id", ""),
            ("alipay_private_key", ""),
            ("alipay_public_key", ""),
            ("wechat_app_id", ""),
            ("wechat_mch_id", ""),
            ("wechat_api_key", ""),
            ("default_markup", "1.3"),
            ("agent_enabled", "true"),
            ("min_withdrawal", "100"),
            ("withdrawal_fee_rate", "0.01"),
        ]
        cur.executemany("INSERT INTO config VALUES (?,?)", configs)

    conn.commit()
    conn.close()
    print("[DB] 数据库初始化完成 - 终点平台 v2.0")

if __name__ == "__main__":
    init_db()
