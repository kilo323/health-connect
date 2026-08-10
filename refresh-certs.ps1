New-Item -ItemType Directory -Path .\certs -Force -ErrorAction SilentlyContinue

# Find all certs matching the corporate firewall/proxy
$certs = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Issuer -match "csc1fw004" }
Write-Host "Found $($certs.Count) matching certificate(s)"

foreach ($cert in $certs) {
    $thumbprint = $cert.Thumbprint
    $derPath = ".\certs\$thumbprint.der"
    $pemPath = ".\certs\$thumbprint.crt"

    Export-Certificate -Cert $cert -FilePath $derPath -Type CERT | Out-Null
    certutil -encode $derPath $pemPath | Out-Null
    Remove-Item $derPath -ErrorAction SilentlyContinue

    Write-Host "Exported: $pemPath  ($($cert.Subject))"
}

# Also create a single combined file for Docker
$combined = ".\certs\corporate-ca.crt"
$certs | ForEach-Object -Begin { "" } -Process {
    $der = ".\certs\$($_.Thumbprint).der"
    $pem = ".\certs\$($_.Thumbprint).crt"
    Get-Content $pem
} | Set-Content $combined

Write-Host "`nCombined bundle: $combined ($($certs.Count) certs)"