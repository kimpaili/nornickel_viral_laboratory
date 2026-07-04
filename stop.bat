@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo    FABRIKA GIPOTEZ  -  STOP
echo ============================================
echo.

docker version >nul 2>nul
if errorlevel 1 goto no_docker

echo [stop] Stopping and removing containers. DB data and corpus are kept ...
docker compose down

echo.
echo Done. Data is stored in a Docker volume - next start.bat will be fast.
echo Full reset with data wipe:  docker compose down -v
goto end

:no_docker
echo [warn] Docker is not running - nothing to stop.

:end
echo.
pause
endlocal
