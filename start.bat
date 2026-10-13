@echo off
chcp 65001 >nul
title EventLens
cd /d "%~dp0"

echo ============================================================
echo   EventLens - 投资事件证据追踪台
echo ============================================================
echo.

REM ---- 启动前探测：若服务已在运行，直接打开浏览器并退出（避免端口冲突） ----
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 2 } } catch { exit 1 }"
if not errorlevel 1 (
    echo [检测] EventLens 服务已在运行^(端口 8000^)，无需重复启动。
    echo [访问] 正在为您打开浏览器: http://127.0.0.1:8000
    echo [提示] 如需重启服务：请先关闭此前运行 EventLens 的命令行窗口，再运行本脚本。
    start http://127.0.0.1:8000
    pause
    exit /b 0
)

REM ---- 选择 Python 解释器：优先工程内置 .venv（须通过可运行性校验，防止跨机器失效） ----
set "PY="
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python。请安装 Python 3.10+ 后重试^(安装时勾选 "Add to PATH"^)。
        pause
        exit /b 1
    )
    set "PY=python"
)
echo [环境] Python 解释器: %PY%
echo.

REM ---- 依赖自检：缺失时自动安装（离线优先，失败回退在线镜像） ----
"%PY%" -c "import fastapi, uvicorn, httpx, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [安装] 首次运行，正在安装依赖^(优先使用工程内置离线包 vendor\wheels^)...
    "%PY%" -m pip install --no-index --find-links vendor\wheels -r backend\requirements.txt >nul 2>nul
    "%PY%" -c "import fastapi, uvicorn, httpx, dotenv" >nul 2>nul
    if errorlevel 1 (
        echo [安装] 离线安装未成功，改用在线镜像源安装...
        "%PY%" -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [错误] 依赖安装失败，请检查网络后重试。
            pause
            exit /b 1
        )
    )
    echo [安装] 依赖安装完成。
)

echo [启动] 正在启动服务，首次启动约需数秒...
echo [访问] 启动完成后浏览器会自动打开；也可手动访问: http://127.0.0.1:8000
echo [停止] 按 Ctrl+C 或直接关闭本窗口
echo.

start /min cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"

"%PY%" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
echo.
echo [提示] 若上方出现 "10048 端口占用" 错误：说明已有 EventLens 实例
echo        或其他程序占用了 8000 端口。请先关闭旧的 EventLens 命令行窗口；
echo        或修改本脚本中的 --port 8000 为其他端口^(如 8010^)后重试。
pause
