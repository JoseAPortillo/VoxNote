@echo off
rem VoxNote launcher - opens the app without a console window.
rem Uses the project virtualenv (pythonw.exe) and the project root as cwd.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m app.main
) else (
    rem Fallback: system python (requires installed deps)
    where pythonw >nul 2>nul
    if %errorlevel%==0 (
        start "" pythonw -m app.main
    ) else (
        echo Python venv not found. Run: python -m venv .venv ^&^& pip install -r requirements.txt
        pause
    )
)
endlocal