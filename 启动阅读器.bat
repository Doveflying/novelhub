@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 启动本地轻小说阅读器…
echo 浏览器打开： http://127.0.0.1:8899
start "" http://127.0.0.1:8899
python nh.py serve --port 8899
pause
