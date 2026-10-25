<#
.SYNOPSIS
    Starts the RepoAgent Google ADK A2A Agents (Planner on 8001, Reviewer on 8002).
.PARAMETER PlannerPort
    Port for the Planner A2A server (default: 8001 or $env:REPOAGENT_PLANNER_PORT).
.PARAMETER ReviewerPort
    Port for the Reviewer A2A server (default: 8002 or $env:REPOAGENT_REVIEWER_PORT).
.PARAMETER HostAddress
    Host interface to bind (default: 127.0.0.1 or $env:REPOAGENT_A2A_HOST).
.PARAMETER Workspace
    Target workspace for repo-reader tools (default: demo_repo).
#>
param(
    [int]$PlannerPort = $(if ($env:REPOAGENT_PLANNER_PORT) { [int]$env:REPOAGENT_PLANNER_PORT } else { 8001 }),
    [int]$ReviewerPort = $(if ($env:REPOAGENT_REVIEWER_PORT) { [int]$env:REPOAGENT_REVIEWER_PORT } else { 8002 }),
    [string]$HostAddress = $(if ($env:REPOAGENT_A2A_HOST) { $env:REPOAGENT_A2A_HOST } else { "127.0.0.1" }),
    [string]$Workspace = $(if ($env:REPOAGENT_WORKSPACE) { $env:REPOAGENT_WORKSPACE } else { "demo_repo" })
)

$ErrorActionPreference = "Stop"

$Python = "python"
if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = ".\.venv\Scripts\python.exe"
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "Starting RepoAgent ADK Agents over A2A (Windows)" -ForegroundColor Cyan
Write-Host "  Planner Agent:   http://${HostAddress}:${PlannerPort}" -ForegroundColor Green
Write-Host "  Reviewer Agent:  http://${HostAddress}:${ReviewerPort}" -ForegroundColor Green
Write-Host "  Target Repo:     ${Workspace}" -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan

& $Python -m repoagent.agents.adk.server `
    --host $HostAddress `
    --planner-port $PlannerPort `
    --reviewer-port $ReviewerPort `
    --workspace $Workspace
