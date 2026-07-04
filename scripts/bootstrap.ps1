$ErrorActionPreference = "Stop"

docker compose --profile tools build
docker compose up -d
docker compose --profile tools run --rm seed

Write-Host "API:      http://localhost:8000/docs"
Write-Host "Frontend: http://localhost:8501"
