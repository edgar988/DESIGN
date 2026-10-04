<#
  AEQ Cinemark PH - one-time setup on the Revit desktop (safe to re-run; it also updates).

  Run in PowerShell (normal, not admin):
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # once, if scripts are blocked
    powershell -ExecutionPolicy Bypass -File setup_desktop.ps1

  Or, before the repo exists, download this one file from GitHub and run it; it clones the repo.

  What it does (nothing touches open Revit / AutoCAD / Inventor / Fusion sessions):
    1. Installs Git, Python 3.12 and pyRevit with winget if they are missing.
    2. Clones (or pulls) edgar988/DESIGN, branch cinemark-ph-revit-pipeline, into C:\AEQ\design.
    3. pip-installs the pipeline requirements and runs the tests.
    4. Registers the pyRevit extension folder (AEQ tab shows after Revit restarts or pyRevit Reload).
    5. Finds CINEMARK\PIZZA HUT on Google Drive for Desktop and writes the shared settings file.
    6. Registers the "AEQ Cinemark Watcher" scheduled task: runs at logon, hidden, below-normal priority.
#>
param(
  [string]$Root = "C:\AEQ",
  [string]$Branch = "cinemark-ph-revit-pipeline",
  [switch]$AutoRevit
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Step "1. Prerequisites"
if (-not (Have git))     { winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements }
if (-not (Have py))      { winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements }
if (-not (Have pyrevit)) {
  try { winget install -e --id pyRevitLabs.pyRevit --silent --accept-package-agreements --accept-source-agreements }
  catch { Write-Warning "Install pyRevit manually: https://github.com/pyrevitlabs/pyRevit/releases (close Revit first)" }
}
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

Step "2. Repository"
$Repo = Join-Path $Root "design"
New-Item -ItemType Directory -Force -Path $Root, (Join-Path $Root "work") | Out-Null
if (Test-Path (Join-Path $Repo ".git")) {
  git -C $Repo fetch origin $Branch
  git -C $Repo checkout $Branch
  git -C $Repo pull --ff-only origin $Branch
} else {
  git clone -b $Branch https://github.com/edgar988/DESIGN.git $Repo
}
$Proj = Join-Path $Repo "cinemark-pizza-hut"

Step "3. Python packages + tests"
$Py = (& py -3.12 -c "import sys; print(sys.executable)").Trim()
$PyW = Join-Path (Split-Path $Py) "pythonw.exe"
& $Py -m pip install --upgrade --quiet -r (Join-Path $Proj "requirements.txt")
Push-Location $Proj; & $Py -m pytest -q tests; Pop-Location

Step "4. pyRevit extension"
if (Have pyrevit) {
  pyrevit extensions paths add (Join-Path $Proj "revit")
  Write-Host "AEQ tab appears next time Revit starts (or pyRevit > Reload). Your open Revit is not touched."
} else { Write-Warning "pyRevit not found - add the folder later: pyRevit Settings > Custom Extension Directories > $Proj\revit" }

Step "5. Google Drive folder + settings"
$drive = $null
foreach ($d in (Get-PSDrive -PSProvider FileSystem).Root) {
  foreach ($base in @("My Drive", "Shared drives\*")) {
    $hit = Get-ChildItem -Path (Join-Path $d $base) -Directory -Filter "CINEMARK" -ErrorAction SilentlyContinue |
           ForEach-Object { Join-Path $_.FullName "PIZZA HUT" } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($hit) { $drive = $hit; break }
  }
  if ($drive) { break }
}
if (-not $drive) { $drive = Read-Host "Path to CINEMARK\PIZZA HUT on Google Drive (e.g. G:\Shared drives\...\CINEMARK\PIZZA HUT)" }
Write-Host "Drive folder: $drive"
$SetPath = Join-Path $env:APPDATA "pyRevit\aeq_cinemark.json"
New-Item -ItemType Directory -Force -Path (Split-Path $SetPath) | Out-Null
$s = @{}
if (Test-Path $SetPath) { (Get-Content $SetPath -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $s[$_.Name] = $_.Value } }
$s.repo_root     = $Proj
$s.drive_root    = $drive
$s.families_dir  = Join-Path $drive "CAD TEMPLATES"
$s.template_rte  = Join-Path $drive "CAD TEMPLATES\AEQ_FOODSERVICE_11X17_2026.rte"
$s.outputs_dir   = Join-Path $Root "work"
$s.work_dir      = Join-Path $Root "work"
$s.python_exe    = $Py
if (-not $s.ContainsKey("store_id")) { $s.store_id = "GA-263" }
if (-not $s.ContainsKey("rev"))      { $s.rev = "R0" }
$s.auto_revit    = [bool]$AutoRevit
if (-not $s.ContainsKey("revit_idle_minutes")) { $s.revit_idle_minutes = 10 }
$s | ConvertTo-Json | Set-Content -Encoding UTF8 $SetPath
Write-Host "Settings: $SetPath (auto_revit = $($s.auto_revit))"

Step "6. Background watcher (scheduled task)"
$name = "AEQ Cinemark Watcher"
$act  = New-ScheduledTaskAction -Execute $PyW -Argument "`"$(Join-Path $Proj 'tools\watch_stores.py')`"" -WorkingDirectory $Proj
$trg  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
          -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -Priority 7
$prn  = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Watcher running. Log: $env:LOCALAPPDATA\AEQ\watch_stores.log"

Step "Done"
Write-Host "Save a store DXF into its Drive folder -> quote, workbook and Gantt appear in '_AEQ OUTPUT' within ~2 minutes."
Write-Host "Revit drawings: AEQ tab > Cinemark PH (interactive). After GA-263 checks out, re-run with -AutoRevit for idle-time background builds."
