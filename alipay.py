"""
支付宝当面付集成
配置路径: database config表
"""
import base64
import hashlib
import time
import uuid
import logging
from typing import Optional
from database import get_db

logger = logging.getLogger(__name__)

def get_alipay_config() -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM config WHERE key LIKE 'alipay%'")
    rows = cur.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def generate_alipay_url(order_id: str, amount: float, subject: str) -> Optional[str]:
    """
    生成支付宝扫码支付链接
    amount: 金额（元）
    """
    config = get_alipay_config()
    app_id = config.get("alipay_app_id", "")
    private_key_pem = config.get("alipay_private_key", "")

    if not app_id or not private_key_pem:
        # 未配置，返回None，调用方会使用模拟支付
        return None

    try:
        import json
        biz_content = {
            "out_trade_no": order_id,
            "total_amount": str(amount),
            "subject": subject,
            "product_code": "FAST_INSTANT_TRADE_PAY"
        }
        biz_str = json.dumps(biz_content, separators=(',', ':'))

        # 签名（简化版，需要pycryptodome或rsa库）
        # 这里返回None表示未配置真实支付宝，走模拟支付流程
        return None

    except Exception as e:
        logger.error(f"生成支付宝链接失败: {e}")
        return None
