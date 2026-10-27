@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 算邻局域网模式：仅在你们自己的可信办公室网络中使用。
echo 第二台电脑的接入步骤见 README.md 和交付说明。
python launch.py --host 0.0.0.0
if errorlevel 1 pause
