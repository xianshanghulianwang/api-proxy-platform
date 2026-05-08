@echo off
chcp 65001 >nul
echo ========================================
echo   API中转站 - 一键部署脚本
echo ========================================
echo.

cd /d "%~dp0"

echo [1/5] 安装依赖...
dnf install -y python3-pip git

echo [2/5] 安装Python库...
pip3 install fastapi "uvicorn[standard]" httpx pydantic python-multipart --break-system-packages

echo [3/5] 创建目录结构...
if not exist "templates" mkdir templates
if not exist "static\css" mkdir static\css

echo [4/5] 初始化数据库...
python3 database.py

echo [5/5] 启动服务...
start /b python3 main.py > app.log 2>&1

echo.
echo ========================================
echo   部署完成!
echo ========================================
echo.
echo 访问地址: http://124.156.196.238:8000
echo 管理后台: http://124.156.196.196.238:8000/admin
echo 管理员账号: admin
echo 管理员密码: admin
echo.
echo 查看日志: type app.log
echo 停止服务: taskkill /f /im python3.exe
echo.
pause
