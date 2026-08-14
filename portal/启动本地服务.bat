@echo off
cd /d "%~dp0.."
echo 启动 Portal 本地服务...
echo 访问地址：http://localhost:7788
echo 按 Ctrl+C 停止
python portal/server.py
pause
