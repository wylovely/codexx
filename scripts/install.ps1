param([string]$Repository = $env:CODEXX_REPOSITORY)
$ErrorActionPreference = 'Stop'
if (-not $Repository) {
    Write-Host 'No GitHub repository configured; release installation skipped.'
    exit 0
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw 'Expected GitHub OWNER/REPO'
}
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($architecture -ne 'X64') { throw 'Windows release currently supports x64 only; use source installation on other architectures.' }
$asset = 'codexx-windows-x86_64.zip'
$baseUrl = "https://github.com/$Repository/releases/latest/download"
$work = Join-Path ([IO.Path]::GetTempPath()) ('codexx-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/SHA256SUMS" -OutFile (Join-Path $work 'SHA256SUMS')
    $archive = Join-Path $work $asset
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$asset" -OutFile $archive
    $matches = @(Get-Content -LiteralPath (Join-Path $work 'SHA256SUMS') | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '$') })
    if ($matches.Count -ne 1) { throw 'Missing/duplicate checksum' }
    $expected = ($matches[0] -split '\s+')[0]
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Archive checksum mismatch' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $entries = @($zip.Entries | Where-Object FullName -eq 'codexx.exe')
        if ($entries.Count -ne 1) { throw 'Invalid release archive' }
        $executable = Join-Path $work 'codexx.exe'
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $executable)
    } finally { $zip.Dispose() }
    & $executable --codexx-no-update --codexx-install --codexx-repository $Repository
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Exit running codexx sessions and retry.' }
} finally {
    # The target is an explicitly created, resolved child of the system temp directory.
    $resolvedWork = [IO.Path]::GetFullPath($work)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedWork.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolvedWork).StartsWith('codexx-install-')) {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}
