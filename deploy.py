#!/usr/bin/env python3
"""API中转站 部署脚本 - 在服务器上运行"""
import os, subprocess, sys

SRV = "124.156.196.238"
PORT = 22
USER = "root"
PSWD = "Qqmima520589"

print(f"正在连接到 {SRV}...")

# 尝试用密码SSH
try:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SRV, port=PORT, username=USER, password=PSWD, timeout=15, banner_timeout=15)
    print("✅ 连接成功!")
    
    # 执行部署命令
    commands = [
        "dnf install -y python3-pip git",
        "pip3 install fastapi uvicorn httpx pydantic python-multipart --break-system-packages",
        "mkdir -p /root/api-proxy-platform/templates /root/api-proxy-platform/static/css",
        "cd /root/api-proxy-platform",
    ]
    
    for cmd in commands:
        print(f"执行: {cmd}")
        stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
        print(stdout.read().decode() + stderr.read().decode())
    
    client.close()
    print("✅ 依赖安装完成!")
except Exception as e:
    print(f"连接失败: {e}")
    print("请手动在服务器上执行以下命令:")
    print("dnf install -y python3-pip git")
    print("pip3 install fastapi uvicorn httpx pydantic python-multipart --break-system-packages")
