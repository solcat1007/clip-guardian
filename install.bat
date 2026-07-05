@echo off
chcp 65001 >nul
echo ========================================
echo   剪辑守护 v1.0 - 安装脚本
echo ========================================
echo.
echo [1/3] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python 已就绪
echo.
echo [2/3] 安装依赖包...
pip install psutil -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo [3/3] 安装完成！
echo.
echo 启动命令：python clip_guardian.py
pause
