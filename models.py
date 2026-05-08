from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# ===== 用户相关 =====
class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    phone: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    id: str
    username: str
    email: Optional[str]
    phone: Optional[str]
    balance: float
    api_key: Optional[str]
    api_quota: int
    created_at: str

# ===== 套餐相关 =====
class PackageInfo(BaseModel):
    id: str
    name: str
    description: Optional[str]
    price: float
    credits: int
    validity_days: int
    is_active: bool

# ===== 订单相关 =====
class OrderCreate(BaseModel):
    package_id: str

class OrderInfo(BaseModel):
    id: str
    package_id: str
    package_name: Optional[str]
    amount: float
    status: str
    pay_url: Optional[str]
    created_at: str
    paid_at: Optional[str]

# ===== API Keys =====
class ApiKeyInfo(BaseModel):
    id: str
    name: str
    provider: str
    base_url: Optional[str]
    model: Optional[str]
    price_per_1k: float
    is_active: bool

# ===== 用量统计 =====
class UsageStats(BaseModel):
    total_calls: int
    total_cost: float
    input_tokens: int
    output_tokens: int
    balance: float
    quota: int

class UsageLog(BaseModel):
    id: int
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    called_at: str

# ===== 充值 =====
class RechargeCreate(BaseModel):
    amount: float

class RechargeInfo(BaseModel):
    id: int
    amount: float
    method: str
    status: str
    created_at: str
