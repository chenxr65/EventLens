@echo off
chcp 65001 >nul
title EventLens - 依赖安装
cd /d "%~dp0"

echo ============================================================
echo   EventLens - 依赖安装
echo   （可选步骤：start.bat 首次运行时也会自动完成安装）
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python。请安装 Python 3.10+^(安装时勾选 "Add to PATH"^)后重试。
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.10 及以上。
    pause
    exit /b 1
)

echo [1/2] 尝试离线安装^(工程内置 vendor\wheels，支持 Python 3.10~3.13^)...
python -m pip install --no-index --find-links vendor\wheels -r backend\requirements.txt
if errorlevel 1 (
    echo.
    echo [2/2] 离线安装失败^(可能是 Python 版本不匹配^)，改用在线镜像源...
    python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

echo.
echo [完成] 依赖安装成功。接下来双击 start.bat 启动产品。
pause
