# PowerShell script to roll back to pre-OmniRoute configuration
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "RESTORING TALENTMATCH TO PRE-OMNIROUTE SETUP" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$backendDir = $PSScriptRoot
$backupDir = Join-Path $backendDir "backups\pre_omniroute"

if (-not (Test-Path $backupDir)) {
    Write-Host "[ERROR] Backup directory not found at $backupDir" -ForegroundColor Red
    exit 1
}

$files = @(
    @{ Src = "$backupDir\gemini_service.py.bak"; Dest = "$backendDir\apps\resumes\gemini_service.py" },
    @{ Src = "$backupDir\semantic_matcher.py.bak"; Dest = "$backendDir\apps\resumes\semantic_matcher.py" },
    @{ Src = "$backupDir\.env.bak"; Dest = "$backendDir\.env" },
    @{ Src = "$backupDir\.env.example.bak"; Dest = "$backendDir\.env.example" }
)

$count = 0
foreach ($item in $files) {
    if (Test-Path $item.Src) {
        Copy-Item -Path $item.Src -Destination $item.Dest -Force
        Write-Host "[RESTORED] $(Split-Path $item.Src -Leaf) -> $(Resolve-Path -Relative $item.Dest)" -ForegroundColor Green
        $count++
    } else {
        Write-Host "[WARNING] File missing: $($item.Src)" -ForegroundColor Yellow
    }
}

Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Rollback complete! Successfully restored $count files." -ForegroundColor Green
Write-Host "TalentMatch is 100% back to direct Gemini API." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
