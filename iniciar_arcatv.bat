@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "APP_DIR=%~dp0"
pushd "%APP_DIR%" >nul 2>&1
if errorlevel 1 (
    echo No se pudo acceder a la carpeta de ARCATV: %APP_DIR%
    pause
    exit /b 1
)

set "HOST=0.0.0.0"
set "PORT=4990"
set "URL=http://%HOST%:%PORT%"

echo.
echo ========================================
echo   ARCATV - Servidor local
echo ========================================
echo.

:: Nota: ARCATV suele buscar la app dentro del directorio actual o un paquete específico,
:: si necesitas verificar un archivo concreto (como app\main.py en el otro), puedes añadirlo aquí.

if not exist ".env" (
    if exist ".env.example" (
        echo Creando .env desde .env.example...
        copy ".env.example" ".env" >nul
    )
)

set "PID_TO_CLOSE="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    set "PID_TO_CLOSE=%%P"
)

if defined PID_TO_CLOSE (
    echo El puerto %PORT% esta en uso por el proceso !PID_TO_CLOSE!.
    echo Cerrando proceso para liberar el puerto...
    taskkill /F /PID !PID_TO_CLOSE! >nul 2>&1
    timeout /t 2 /nobreak >nul
) else (
    echo Puerto %PORT% disponible.
)

set "PYTHON_CMD=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
)

set "PYTHONPATH=%CD%"

echo.
echo Arrancando servidor en %URL%
echo Puedes acceder localmente en http://127.0.0.1:%PORT%
echo Pulsa Ctrl+C para detenerlo.
echo.

"%PYTHON_CMD%" -m flask --app arcatv run --host %HOST% --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Servidor detenido.
popd
pause
exit /b %EXIT_CODE%