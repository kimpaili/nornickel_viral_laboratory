@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo    FABRIKA GIPOTEZ  -  START
echo    dir: %CD%
echo ============================================
echo.

REM --- 1) Docker ---
docker version >nul 2>nul
if errorlevel 1 goto no_docker
echo [ok] Docker is running.

REM --- 2) .env ---
if not exist ".env" copy /Y ".env.example" ".env" >nul

REM --- 3) Yandex keys check - warn only, needed for RAG tab ---
findstr /R /C:"^YANDEX_API_KEY=." /C:"^YANDEX_FOLDER_ID=." ".env" >nul 2>nul
if errorlevel 1 goto yandex_warn
echo [ok] Yandex Cloud keys found in .env.
goto up
:yandex_warn
echo [warn] YANDEX_API_KEY / YANDEX_FOLDER_ID not set in .env - the RAG tab (Literature)
echo        and LLM cards will fall back. Add the keys to .env to enable them.
echo        The scoring engine works without them.

:up
echo.
echo [run] Starting containers: db + api + frontend ...
docker compose up -d
if errorlevel 1 goto up_fail

echo.
echo [db] Seeding demo data: 2 plants, rules, loss matrices ...
docker compose --profile tools run --rm seed

echo.
echo [rag] Indexing corpus. First run may take 1-2 minutes ...
docker compose --profile tools run --rm corpus-index

echo.
echo [wait] Waiting for API to become ready ...
set /a tries=0
:waitapi
curl -s http://localhost:8000/health >nul 2>nul
if not errorlevel 1 goto apiok
set /a tries+=1
if %tries% GEQ 30 goto api_slow
timeout /t 2 /nobreak >nul
goto waitapi
:apiok
echo [ok] API is ready.
start "" http://localhost:8501
goto done

:no_docker
echo [ERROR] Docker is not running. Open Docker Desktop, wait for "Engine running", then retry.
goto end
:up_fail
echo [ERROR] docker compose up failed. See the error above.
goto end
:api_slow
echo [warn] API did not answer in ~60s. Check logs: docker compose logs api

:done
echo.
echo ============================================
echo    UI  for testing: http://localhost:8501
echo    API and Swagger:  http://localhost:8000/docs
echo ============================================
echo    Stop everything:  stop.bat   [data is kept]
echo ============================================
:end
echo.
pause
endlocal
