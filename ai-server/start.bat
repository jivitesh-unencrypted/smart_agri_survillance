@echo off
REM =====================================================================
REM Start the LOCAL AI SERVER.
REM    Reads HOST / PORT / LOG_LEVEL from .env
REM    Set HOST=0.0.0.0 in .env once the frontend is on Cloudflare Pages
REM    so other devices on the LAN can reach the live stream.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo venv not found - run setup.bat first.
    pause
    exit /b 1
)

for /f "tokens=1,2" %%a in ('venv\Scripts\python.exe -c "from app.core.config import settings; print(settings.HOST, settings.PORT)"') do (
    set "HOST=%%a"
    set "PORT=%%b"
)

echo Starting AI server on !HOST!:!PORT! ...
venv\Scripts\python.exe -m uvicorn app.main:app --host !HOST! --port !PORT!
endlocal
