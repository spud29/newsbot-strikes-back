Write-Host "Checking SSH authorized_keys permissions..." -ForegroundColor Cyan

$keyPath = 'C:\ProgramData\ssh\administrators_authorized_keys'

# Check current permissions
$acl = Get-Acl -Path $keyPath
Write-Host "Current ACL:" -ForegroundColor Yellow
$acl.Access | ForEach-Object {
    Write-Host "  $($_.IdentityReference) - $($_.FileSystemRights) - $($_.IsInherited)"
}

Write-Host ""
Write-Host "IsInherited: $($acl.AreAccessRulesProtected)" -ForegroundColor Yellow

# Fix if needed
if (-not $acl.AreAccessRulesProtected) {
    Write-Host "Fixing permissions (removing inheritance)..." -ForegroundColor Green
    icacls $keyPath /inheritance:r
    icacls $keyPath /grant "SYSTEM:R"
    icacls $keyPath /grant "BUILTIN\Administrators:R"
    Write-Host "Permissions fixed!" -ForegroundColor Green
} else {
    Write-Host "Inheritance already disabled - permissions look good" -ForegroundColor Green
}

Write-Host ""
Write-Host "Restarting SSH service..." -ForegroundColor Cyan
Restart-Service sshd
Write-Host "Done!" -ForegroundColor Green

Pause