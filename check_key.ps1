Write-Host "Checking SSH authorized_keys..." -ForegroundColor Cyan

$keyPath = 'C:\ProgramData\ssh\administrators_authorized_keys'

if (Test-Path $keyPath) {
    $content = Get-Content $keyPath -Raw
    Write-Host "File exists. Content:" -ForegroundColor Green
    Write-Host $content
    Write-Host ""
    Write-Host "Permissions:" -ForegroundColor Yellow
    icacls $keyPath
} else {
    Write-Host "File does NOT exist!" -ForegroundColor Red
    Write-Host "Creating it now..." -ForegroundColor Yellow

    $pubkey = Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"
    Set-Content -Path $keyPath -Value $pubkey -NoNewline

    # Fix permissions
    icacls $keyPath /inheritance:r
    icacls $keyPath /grant "SYSTEM:R"
    icacls $keyPath /grant "BUILTIN\Administrators:R"

    Write-Host "Created! Restarting SSH..." -ForegroundColor Green
    Restart-Service sshd
}

Pause