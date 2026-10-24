@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 用法：抓取整本.bat "书籍地址"
  echo 例如：抓取整本.bat "https://www.bilinovel.com/novel/3805/326116_2.html"
  pause
  exit /b 1
)
echo 开始抓取：%~1
python nh.py add "%~1"
pause
