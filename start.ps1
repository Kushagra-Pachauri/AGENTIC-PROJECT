<#
.SYNOPSIS
    RepoAgent Unified Startup Script for Windows PowerShell.
.DESCRIPTION
    Launches RepoAgent components:
      - Local Full Stack (Streamlit Dashboard + FastAPI Backend + ADK Agents)
      - Dashboard Only (Streamlit on port 8501)
      - API Only (FastAPI on port 8000)
      - ADK Agents Only (Planner on 8001, Reviewer on 8002)
      - Docker Compose Stack (Containers)
      - Interactive Menu (Default if no flag passed)
.PARAMETER Mode
    Execution mode: "local", "dashboard", "api", "agents", "docker", or "menu"
.PARAMETER DashboardPort
    Port for Streamlit dashboard (default: 8501)
.PARAMETER ApiPort
    Port for FastAPI backend (default: 8000)
.PARAMETER NoBrowser
    Do not automatically open the browser when starting dashboard
#>
param(
    [ValidateSet("local", "dashboard", "api", "agents", "docker", "menu")]
    [string]$Mode = "menu",
    [int]$DashboardPort = 8501,
    [int]$ApiPort = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Locate Python executable
$Python = "python"
if (Test-Path "$ScriptDir\.venv\Scripts\python.exe") {
    $Python = "$ScriptDir\.venv\Scripts\python.exe"
}

function Show-Banner {
    $bannerText = @'
=================================================================
  ____                      _                    _   
 |  _ \ ___ _ __   ___     / \   __ _  ___ _ __ | |_ 
 | |_) / _ \ '_ \ / _ \   / _ \ / _` |/ _ \ '_ \| __|
 |  _ <  __/ |_) | (_) | / ___ \ (_| |  __/ | | | |_ 
 |_| \_\___| .__/ \___/ /_/   \_\__, |\___|_| |_|\__|
           |_|                  |___/                
 Autonomous Requirement-to-PR Multi-Agent Engineering System
=================================================================
'@
    Write-Host $bannerText -ForegroundColor Cyan
    Write-Host " Python: $Python" -ForegroundColor DarkGray
    Write-Host "=================================================================" -ForegroundColor Cyan
}

function Start-LocalStack {
    Show-Banner
    Write-Host "`n[*] Starting RepoAgent Local Stack..." -ForegroundColor Green
    Write-Host "    - Google ADK Planner  : http://localhost:8001" -ForegroundColor Gray
    Write-Host "    - Google ADK Reviewer : http://localhost:8002" -ForegroundColor Gray
    Write-Host "    - FastAPI Backend     : http://localhost:$ApiPort" -ForegroundColor Gray
    Write-Host "    - Streamlit Dashboard : http://localhost:$DashboardPort`n" -ForegroundColor Gray

    $pids = @()

    # 1. Start ADK A2A Agent Server
    Write-Host "[1/3] Starting ADK A2A Agents (Planner 8001, Reviewer 8002)..." -ForegroundColor Yellow
    $agentProc = Start-Process -FilePath $Python -ArgumentList "-m repoagent.agents.adk.server --host 127.0.0.1 --planner-port 8001 --reviewer-port 8002 --workspace demo_repo" -PassThru -NoNewWindow
    $pids += $agentProc.Id

    # 2. Start FastAPI Backend
    Write-Host "[2/3] Starting FastAPI Backend on port $ApiPort..." -ForegroundColor Yellow
    $apiProc = Start-Process -FilePath $Python -ArgumentList "-m uvicorn repoagent.api.app:app --host 127.0.0.1 --port $ApiPort" -PassThru -NoNewWindow
    $pids += $apiProc.Id

    Start-Sleep -Seconds 2

    # 3. Open browser if requested
    if (-not $NoBrowser) {
        Start-Process "http://localhost:$DashboardPort"
    }

    # 4. Start Streamlit Dashboard (foreground)
    Write-Host "[3/3] Starting Streamlit Dashboard on port $DashboardPort..." -ForegroundColor Green
    Write-Host "`n>>> Press Ctrl+C in this console to stop all RepoAgent services cleanly <<<`n" -ForegroundColor Cyan

    try {
        & $Python -m streamlit run "$ScriptDir\src\repoagent\ui\dashboard.py" --server.port $DashboardPort --server.headless false
    } finally {
        Write-Host "`n[*] Shutting down background services..." -ForegroundColor Yellow
        foreach ($pidToKill in $pids) {
            try {
                Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
            } catch {}
        }
        Write-Host "[✓] All RepoAgent services stopped." -ForegroundColor Green
    }
}

function Start-DashboardOnly {
    Show-Banner
    Write-Host "`n[*] Starting Streamlit Dashboard on http://localhost:$DashboardPort..." -ForegroundColor Green
    if (-not $NoBrowser) {
        Start-Process "http://localhost:$DashboardPort"
    }
    & $Python -m streamlit run "$ScriptDir\src\repoagent\ui\dashboard.py" --server.port $DashboardPort
}

function Start-ApiOnly {
    Show-Banner
    Write-Host "`n[*] Starting FastAPI Backend on http://localhost:$ApiPort..." -ForegroundColor Green
    Write-Host "    API Docs available at: http://localhost:$ApiPort/docs`n" -ForegroundColor Gray
    & $Python -m uvicorn repoagent.api.app:app --host 127.0.0.1 --port $ApiPort --reload
}

function Start-AgentsOnly {
    Show-Banner
    Write-Host "`n[*] Starting Google ADK Agents (Planner 8001, Reviewer 8002)..." -ForegroundColor Green
    & $Python -m repoagent.agents.adk.server --host 127.0.0.1 --planner-port 8001 --reviewer-port 8002 --workspace demo_repo
}

function Start-DockerStack {
    Show-Banner
    Write-Host "`n[*] Checking Docker status..." -ForegroundColor Yellow
    try {
        $null = docker info 2>&1
    } catch {
        Write-Host "[!] Docker Desktop is not running or not accessible." -ForegroundColor Red
        Write-Host "    Please launch Docker Desktop first and try again." -ForegroundColor Yellow
        return
    }

    Write-Host "[*] Launching Docker Compose stack..." -ForegroundColor Green
    docker compose up
}

# Main routing logic
switch ($Mode) {
    "local"     { Start-LocalStack }
    "dashboard" { Start-DashboardOnly }
    "api"       { Start-ApiOnly }
    "agents"    { Start-AgentsOnly }
    "docker"    { Start-DockerStack }
    "menu" {
        Show-Banner
        Write-Host "`nPlease choose a startup mode:" -ForegroundColor White
        Write-Host "  [1] Start Full Local Stack (Dashboard + API + ADK Agents) [RECOMMENDED]" -ForegroundColor Green
        Write-Host "  [2] Start Streamlit Dashboard Only (Port $DashboardPort)" -ForegroundColor Cyan
        Write-Host "  [3] Start FastAPI Backend Only (Port $ApiPort)" -ForegroundColor Cyan
        Write-Host "  [4] Start ADK A2A Agents Only (Ports 8001/8002)" -ForegroundColor Cyan
        Write-Host "  [5] Start Docker Compose Stack (Containers)" -ForegroundColor Yellow
        Write-Host "  [6] Run Unit Test Suite (pytest)" -ForegroundColor White
        Write-Host "  [7] Exit" -ForegroundColor Red

        $choice = Read-Host "`nEnter selection (1-7) [Default: 1]"
        if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

        switch ($choice) {
            "1" { Start-LocalStack }
            "2" { Start-DashboardOnly }
            "3" { Start-ApiOnly }
            "4" { Start-AgentsOnly }
            "5" { Start-DockerStack }
            "6" {
                Write-Host "`n[*] Running pytest..." -ForegroundColor Green
                & $Python -m pytest
            }
            "7" { Write-Host "Goodbye!" -ForegroundColor Yellow }
            default { Write-Host "[!] Invalid selection." -ForegroundColor Red }
        }
    }
}
