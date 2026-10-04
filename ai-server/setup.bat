@echo off
REM =====================================================================
REM One-time setup for the LOCAL AI SERVER (Windows).
REM   1. creates a virtual environment
REM   2. installs dependencies (incl. ultralytics + supabase)
REM   3. creates .env from .env.example if missing
REM After this, fill in SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY in .env
REM and drop your trained weights into models\ (default: yolov8n.pt).
REM =====================================================================
setlocal
cd /d "%~dp0"

if not exist ".env" copy ".env.example" ".env"

if not exist "venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
) else (
    echo [1/3] Virtual environment already exists - skipping.
)

echo [2/3] Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

if not exist "models" mkdir "models"
if not exist "models\yolov8n.pt" (
    echo.
    echo NOTE: models\yolov8n.pt not found.
    echo       Copy your trained weights into models\ or set MODEL_PATH in .env
)

if not exist "storage\snapshots" mkdir "storage\snapshots"
if not exist "storage\recordings" mkdir "storage\recordings"
if not exist "storage\uploads"   mkdir "storage\uploads"
if not exist "data"              mkdir "data"

echo [3/3] Verifying the app imports...
venv\Scripts\python.exe -c "import app.main; print('Import OK')"

echo.
echo Setup complete.
echo   1. Edit .env - set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
echo   2. Run start.bat
endlocal
