# Build one store's Revit model and sheet set headless, in a SEPARATE Revit instance (pyrevit run), from the
# takeoff the quote was priced from. Never touches the Revit session Edgar has open.
#
#   powershell -File revit\batch\run_revit_store.ps1 -StoreId TX-093 -DxfName "TX 093 MCALLEN HOLLYWOOD.dxf"
#
# Reads C:\AEQ\work\<store>\layout.json + takeoff.json (tools\run_store.py writes them) and the DXF in
# C:\AEQ\work\<store>\src\. Writes the model, the QF set PDF and the 3D image to C:\AEQ\work\<store>\, and
# REVIT_STATUS.txt + roughin_connections.json to C:\AEQ\work\<store>\_AEQ OUTPUT\.
param([string]$StoreId, [string]$DxfName, [string]$Rev = "")
$ErrorActionPreference = 'Continue'
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$store = Join-Path 'C:\AEQ\work' $StoreId
$out   = Join-Path $store '_AEQ OUTPUT'
$aeq   = Join-Path $env:LOCALAPPDATA 'AEQ'
New-Item -ItemType Directory -Force $out, $aeq | Out-Null
Copy-Item (Join-Path $store 'layout.json'), (Join-Path $store 'takeoff.json') $out -Force
$dxf = Join-Path $store ('src\' + $DxfName)
if (Test-Path -LiteralPath $dxf) { Copy-Item -LiteralPath $dxf -Destination $store -Force }
Remove-Item (Join-Path $out 'REVIT_STATUS.txt'), (Join-Path $aeq 'revit_build_error.txt') -ErrorAction SilentlyContinue
$job = @{ store_id = $StoreId; outputs_dir = $out; work_dir = 'C:\AEQ\work'; queued = (Get-Date -Format s) }
if ($Rev) { $job.rev = $Rev }
[IO.File]::WriteAllText((Join-Path $aeq 'revit_job_active.json'), ($job | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
$t0 = Get-Date
& pyrevit run (Join-Path $PSScriptRoot 'build_store.py') --revit=2027 | Out-Null
"pyrevit exit: $LASTEXITCODE   elapsed: $([int]((Get-Date) - $t0).TotalSeconds)s"
Get-Content (Join-Path $out 'REVIT_STATUS.txt') -ErrorAction SilentlyContinue
Get-Content (Join-Path $aeq 'revit_build_error.txt') -ErrorAction SilentlyContinue
