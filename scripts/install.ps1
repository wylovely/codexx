param(
    [string]$Repository = $env:CODEXX_REPOSITORY,
    [switch]$NoPath
)
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
$keepWork = $false
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
    $nativeArguments = @('--codexx-no-update', '--codexx-install', '--codexx-repository', $Repository)
    if ($NoPath) { $nativeArguments += '--codexx-no-path' }
    $previousErrorAction = $ErrorActionPreference
    try {
        # Windows PowerShell otherwise turns native stderr into a terminating error
        # before we can preserve the executable's actual failure message.
        $ErrorActionPreference = 'Continue'
        $nativeOutput = @(& $executable @nativeArguments 2>&1)
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $lines = @($nativeOutput | ForEach-Object { $_.ToString() })
    $lines | ForEach-Object { Write-Host $_ }
    $logPath = Join-Path $work 'install.log'
    [IO.File]::WriteAllLines($logPath, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
    if ($nativeExitCode -ne 0) {
        throw "Installation failed (exit code $nativeExitCode). See the actual error above and log: $logPath"
    }
} catch {
    $keepWork = $true
    Write-Warning "Installation files retained for diagnosis: $work"
    throw
} finally {
    # The target is an explicitly created, resolved child of the system temp directory.
    $resolvedWork = [IO.Path]::GetFullPath($work)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $keepWork -and $resolvedWork.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolvedWork).StartsWith('codexx-install-')) {
        try {
            Remove-Item -LiteralPath $resolvedWork -Recurse -Force
        } catch {
            Write-Warning "Installation completed; temporary files could not be removed: $resolvedWork"
        }
    }
}
