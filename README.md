# API智 Hub - AI API 中转平台

## 快速开始

### 1. 安装依赖
```bash
pip install fastapi uvicorn python-multipart httpx python-rsa pyjwt
```

### 2. 配置
编辑 `main.py` 底部的配置区域，设置:
- `ALIPAY_APP_ID` - 支付宝应用ID
- `ALIPAY_PRIVATE_KEY` - 应用私钥
- `ALIPAY_PUBLIC_KEY` - 支付宝公钥
- `SECRET_KEY` - JWT签名密钥

### 3. 运行
```bash
python main.py
# 访问 http://localhost:8000
```

### 4. 管理员登录
- 用户名: `admin`
- 密码: `admin123`
- 后台地址: http://localhost:8000/admin

## 功能模块

### 用户端
- 首页 / - 平台介绍和价格展示
- 注册/登录 - 用户账户管理
- 控制台 /dashboard - 余额、套餐、API Key管理
- 充值 - 支付宝扫码支付
- API文档 /docs - 调用说明

### 管理后台 /admin
- 数据概览 - 用户数、订单、收入统计
- 用户管理 - 查看/管理所有用户
- 上游Key管理 - 配置各大AI服务商API Key
- 套餐管理 - 添加/禁用充值套餐

### API代理调用
```
POST /v1/chat/completions
Headers:
  Authorization: Bearer <user_api_key>
  Content-Type: application/json

Body:
  {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "你好"}]
  }
```

## 数据库
SQLite (platform.db)，首次运行自动初始化。

## 目录结构
```
api-proxy-platform/
├── main.py          # FastAPI主程序
├── database.py      # 数据库操作
├── models.py        # 数据模型
├── auth.py          # 认证JWT
├── alipay.py        # 支付宝集成
├── templates/       # 前端HTML页面
└── platform.db      # SQLite数据库
```
