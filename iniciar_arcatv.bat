@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=py -3"
%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
    set "PYTHON_CMD=python"
    python --version >nul 2>&1
    if errorlevel 1 (
        echo No se encontro Python. Instala Python 3 y vuelve a ejecutar este archivo.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual local...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo No se pudieron instalar las dependencias.
    pause
    exit /b 1
)

for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=5000; while (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) { $p++ }; $p"') do set "PORT=%%P"
if "%PORT%"=="" set "PORT=5000"

echo.
echo ARCATV se abrira en http://127.0.0.1:%PORT%
echo Para cerrar la aplicacion, pulsa CTRL+C en esta ventana.
echo.
start "" "http://127.0.0.1:%PORT%"

".venv\Scripts\python.exe" -m flask --app arcatv run --host 127.0.0.1 --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo ARCATV se ha cerrado.
pause
exit /b %EXIT_CODE%
