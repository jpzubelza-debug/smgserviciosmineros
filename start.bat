@echo off
setlocal

cd /d "C:\Dashboard_Operaciones"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] No existe el entorno virtual en C:\Dashboard_Operaciones\venv
    pause
    exit /b 1
)

REM Levanta API en otra ventana y deja visible cualquier error
start "API Dashboard" cmd /k "cd /d C:\Dashboard_Operaciones && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

REM Espera hasta 15s a que responda /docs
powershell -NoProfile -Command "$ok=$false; 1..30 | %% { try { Invoke-WebRequest 'http://127.0.0.1:8000/docs' -UseBasicParsing -TimeoutSec 1 | Out-Null; $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if ($ok) { Start-Process 'http://127.0.0.1:8000/dashboard'; exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo [ERROR] El servidor no respondio en http://127.0.0.1:8000
    echo Revisa la ventana "API Dashboard" (ahi aparece el traceback real).
    pause
)