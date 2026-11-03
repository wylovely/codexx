# Run after building and packaging the Windows binary. Downloads are replaced
# with copies of the local release artifact; no user PATH/profile is modified.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $root 'scripts/install.ps1'
$installerFixtureArchive = Join-Path $root 'release/codexx-windows-x86_64.zip'
if (-not (Test-Path -LiteralPath $installerFixtureArchive)) { throw 'Build and package the Windows binary first.' }
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('codexx-script-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$previousHome = $env:CODEXX_HOME
$failedWork = $null
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    if ($Uri.EndsWith('/SHA256SUMS')) {
        $digest = (Get-FileHash -LiteralPath $installerFixtureArchive -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($OutFile, "$digest  codexx-windows-x86_64.zip`n")
    } elseif ($Uri.EndsWith('/codexx-windows-x86_64.zip')) {
        Copy-Item -LiteralPath $installerFixtureArchive -Destination $OutFile
    } else { throw 'Unexpected download in installer test.' }
}
try {
    $env:CODEXX_HOME = Join-Path $testRoot 'success'
    & $installer -Repository owner/repo -NoPath
    if (-not (Test-Path -LiteralPath (Join-Path $env:CODEXX_HOME 'codexx.exe'))) {
        throw 'NoPath installation did not create the executable.'
    }
    $env:CODEXX_HOME = Join-Path $testRoot 'failure'
    New-Item -ItemType Directory -Path $env:CODEXX_HOME | Out-Null
    [IO.File]::WriteAllText((Join-Path $env:CODEXX_HOME 'config.json'), 'invalid-json')
    $caught = $false
    try {
        & $installer -Repository owner/repo -NoPath
    } catch {
        $message = $_.Exception.Message
        if ($message -notmatch 'exit code 1.*log: (.+)$') { throw }
        $log = $Matches[1]
        $failedWork = Split-Path -Parent $log
        $detail = Get-Content -Raw -LiteralPath $log
        if ($detail -notmatch 'Expecting value') { throw 'Original native error was not preserved.' }
        if ($message -match 'Exit running') { throw 'Installer incorrectly assumed process contention.' }
        $caught = $true
    }
    if (-not $caught) { throw 'Invalid configuration unexpectedly installed successfully.' }
    Write-Host 'Installer success, NoPath, native stderr and retained failure log: passed'
} finally {
    $env:CODEXX_HOME = $previousHome
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    foreach ($candidate in @($testRoot, $failedWork)) {
        if ($candidate) {
            $resolved = [IO.Path]::GetFullPath($candidate)
            $name = [IO.Path]::GetFileName($resolved)
            if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
                ($name.StartsWith('codexx-script-test-') -or $name.StartsWith('codexx-install-'))) {
                for ($attempt = 0; $attempt -lt 20; $attempt++) {
                    try {
                        Remove-Item -LiteralPath $resolved -Recurse -Force
                        break
                    } catch {
                        if ($attempt -eq 19) { Write-Warning "Test temporary files retained: $resolved" }
                        else { Start-Sleep -Milliseconds 100 }
                    }
                }
            }
        }
    }
}
