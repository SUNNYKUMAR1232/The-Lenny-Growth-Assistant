#Requires -Version 5.1
<#
.SYNOPSIS
    Lenny Growth Assistant - one-command setup for Windows.

.DESCRIPTION
    Checks prerequisites, writes .env, pulls the local models, fetches the
    transcript archive, starts the Docker stack, and indexes the knowledge base.

    Idempotent by design: every step checks the current state before acting, so
    a re-run after a failure resumes rather than starting over.

.EXAMPLE
    .\scripts\setup.ps1
    Knowledge base of 25 episodes. Takes a few minutes.

.EXAMPLE
    .\scripts\setup.ps1 -Full
    All 303 episodes. Embeds ~21,700 chunks; takes a while.

.EXAMPLE
    .\scripts\setup.ps1 -Episodes 50 -Force
    50 episodes, re-chunking and re-embedding anything already indexed.

.NOTES
    If PowerShell refuses to run this file, it is the execution policy, not the
    script. Run it for this window only:
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>
[CmdletBinding()]
param(
    [int]$Episodes = 25,
    [switch]$Full,
    [switch]$Force,
    [switch]$SkipModels,
    [switch]$SkipTranscripts
)

$ErrorActionPreference = 'Stop'

$RepoRoot        = Split-Path -Parent $PSScriptRoot
$TranscriptRepo  = 'https://github.com/ChatPRD/lennys-podcast-transcripts.git'
$LlmModel        = 'llama3.1:8b'
$EmbedModel      = 'nomic-embed-text'
$OllamaHostUrl   = 'http://localhost:11434'
# The backend runs in a container and reaches the host's Ollama by this name.
$OllamaDockerUrl = 'http://host.docker.internal:11434'

if ($Full) { $Episodes = 0 }

Set-Location $RepoRoot

function Write-Step { param([string]$Text) Write-Host ''; Write-Host '==> ' -ForegroundColor Green -NoNewline; Write-Host $Text -ForegroundColor White }
function Write-Info { param([string]$Text) Write-Host "    $Text" }
function Write-Warn { param([string]$Text) Write-Host '    ! ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Stop-Setup { param([string]$Text) Write-Host ''; Write-Host 'failed: ' -ForegroundColor Red -NoNewline; Write-Host $Text; Write-Host ''; exit 1 }

function Test-Command { param([string]$Name) return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-Url {
    param([string]$Url, [int]$TimeoutSec = 5)
    try { Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

# --------------------------------------------------------------- 1. preflight
Write-Step 'Checking prerequisites'
if (-not (Test-Command 'docker')) { Stop-Setup 'docker not found - install Docker Desktop for Windows.' }
docker compose version *> $null
if (-not $?) { Stop-Setup "'docker compose' (v2) not available. Update Docker Desktop." }
docker info *> $null
if (-not $?) { Stop-Setup 'Docker is installed but not running. Start Docker Desktop and re-run.' }
if (-not (Test-Command 'git')) { Stop-Setup 'git not found - needed to fetch the transcript archive.' }
Write-Info 'docker + compose + git ok'

if (-not $SkipModels) {
    if (-not (Test-Command 'ollama')) {
        Stop-Setup 'ollama not found - install from https://ollama.com, or pass -SkipModels and configure a cloud model in the UI.'
    }
    if (-not (Test-Url "$OllamaHostUrl/api/tags")) {
        Write-Info 'Ollama installed but not serving - starting it in the background'
        Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
        foreach ($i in 1..20) {
            if (Test-Url "$OllamaHostUrl/api/tags" -TimeoutSec 2) { break }
            Start-Sleep -Seconds 1
        }
        if (-not (Test-Url "$OllamaHostUrl/api/tags" -TimeoutSec 2)) {
            Stop-Setup "could not reach Ollama at $OllamaHostUrl - check that the Ollama service is running."
        }
    }
    Write-Info "ollama reachable at $OllamaHostUrl"
}

# ------------------------------------------------------------------- 2. .env
Write-Step 'Writing .env'
$EnvPath = Join-Path $RepoRoot '.env'
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $RepoRoot '.env.example') $EnvPath
    Write-Info 'created .env from .env.example'
} else {
    Write-Info '.env exists - updating only the keys this script owns'
}

function Set-EnvLine {
    # Upsert one KEY=VALUE, preserving every other line and its comments.
    param([string]$Key, [string]$Value)
    $lines = @(Get-Content $EnvPath)
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" }
        else { $line }
    }
    if (-not $found) { $out = $out + "$Key=$Value" }
    Set-Content -Path $EnvPath -Value $out -Encoding utf8
}

# .env.example ships localhost, which is right for the no-Docker path and wrong
# for the container: compose substitutes this value in, so leaving it as
# localhost makes the backend look for Ollama inside its own container.
Set-EnvLine 'OLLAMA_BASE_URL'        $OllamaDockerUrl
Set-EnvLine 'OLLAMA_MODEL'           $LlmModel
Set-EnvLine 'OLLAMA_EMBEDDING_MODEL' $EmbedModel
Set-EnvLine 'LLM_PROVIDER'           'ollama'
Set-EnvLine 'EMBEDDING_PROVIDER'     'ollama'
Write-Info "provider=ollama  model=$LlmModel  embeddings=$EmbedModel"

# ----------------------------------------------------------------- 3. models
if (-not $SkipModels) {
    Write-Step 'Pulling local models (~5 GB on first run)'
    $present = @()
    try { $present = @(ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] -replace ':latest$', '' }) } catch { }
    foreach ($m in @($LlmModel, $EmbedModel)) {
        $bare = $m -replace ':latest$', ''
        if ($present -contains $bare) {
            Write-Info "$m already present"
        } else {
            Write-Info "pulling $m ..."
            ollama pull $m
            if (-not $?) { Stop-Setup "ollama pull $m failed" }
        }
    }
} else {
    Write-Step 'Skipping model pull (-SkipModels)'
}

# ------------------------------------------- 4. knowledge base: the transcripts
Write-Step 'Setting up the knowledge base (transcripts)'
$EpisodesDir = Join-Path $RepoRoot 'data\transcripts\episodes'
$ArchiveDir  = Join-Path $RepoRoot 'data\transcripts\_archive'
if ($SkipTranscripts) {
    Write-Info 'skipped (-SkipTranscripts)'
} elseif ((Test-Path $EpisodesDir) -and (Get-ChildItem $EpisodesDir -ErrorAction SilentlyContinue)) {
    $n = (Get-ChildItem $EpisodesDir).Count
    Write-Info "already present: $n episodes"
} else {
    Write-Info 'cloning the transcript archive (~26 MB) ...'
    if (Test-Path $ArchiveDir) { Remove-Item $ArchiveDir -Recurse -Force }
    New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'data\transcripts') | Out-Null
    git clone --depth 1 $TranscriptRepo $ArchiveDir *> $null
    if (-not $?) { Stop-Setup 'clone failed - check your network, or copy an existing archive into data\transcripts\episodes' }
    Move-Item (Join-Path $ArchiveDir 'episodes') $EpisodesDir
    Remove-Item $ArchiveDir -Recurse -Force
    $n = (Get-ChildItem $EpisodesDir).Count
    Write-Info "$n episodes on disk"
}

# ------------------------------------------------------------------ 5. stack
Write-Step 'Starting the stack (postgres + backend + frontend)'
docker compose up --build -d
if (-not $?) { Stop-Setup 'docker compose up failed' }

Write-Host '    waiting for the backend ' -NoNewline
$healthy = $false
foreach ($i in 1..60) {
    try {
        $h = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3 -ErrorAction Stop
        if ($h.status -eq 'ok') { $healthy = $true; break }
    } catch { }
    Write-Host '.' -NoNewline
    Start-Sleep -Seconds 3
}
Write-Host ''
if (-not $healthy) { Stop-Setup 'backend did not become healthy. Inspect with: docker compose logs backend' }
Write-Info 'backend healthy'

# ----------------------------------------------- 6. knowledge base: the index
Write-Step 'Indexing the knowledge base'
$ingestArgs = @('compose', 'exec', '-T', 'backend', 'python', '-m', 'app.scripts.ingest')
if ($Episodes -ne 0) { $ingestArgs += @('--limit', "$Episodes") }
if ($Force)          { $ingestArgs += '--force' }

if ($Episodes -eq 0) {
    Write-Warn 'indexing all 303 episodes - this embeds ~21,700 chunks and takes a while.'
} else {
    Write-Info "indexing $Episodes episodes (use -Full for all 303)"
}
& docker @ingestArgs
if (-not $?) { Stop-Setup 'ingestion failed. Inspect with: docker compose logs backend' }

# ------------------------------------------------------------------ 7. verify
Write-Step 'Verifying'
try {
    Invoke-RestMethod -Uri 'http://localhost:8000/api/ingestion/stats' -TimeoutSec 10 | ConvertTo-Json -Compress | ForEach-Object { Write-Info $_ }
    Invoke-RestMethod -Uri 'http://localhost:8000/api/model' -TimeoutSec 10 | ConvertTo-Json -Compress | ForEach-Object { Write-Info $_ }
} catch {
    Write-Warn "verification request failed: $_"
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host '  frontend   http://localhost:3000'
Write-Host '  backend    http://localhost:8000/health'
Write-Host ''
Write-Host '  Amber model badge means Ollama is unreachable from the container:' -ForegroundColor DarkGray
Write-Host "  check 'ollama list' and that OLLAMA_BASE_URL uses host.docker.internal." -ForegroundColor DarkGray
Write-Host ''
