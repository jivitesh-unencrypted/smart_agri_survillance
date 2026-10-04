@echo off
REM One-time setup for Windows: installs frontend dependencies.
npm install
if not exist ".env" copy .env.example .env
echo.
echo Setup complete. Run start.bat to launch the dev server.
