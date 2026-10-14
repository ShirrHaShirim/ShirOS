$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $root '.venv/Scripts/python.exe'
$helper = Join-Path $root 'scripts/connect_memory_tunnel.py'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Python environment is not installed.' }
if (-not (Test-Path -LiteralPath $helper)) { throw 'Tunnel connect helper is missing.' }

$tunnelId = 'tunnel_6ac52f8f597881919a45dfa07dde0b6e'
$profileDir = Join-Path $env:APPDATA 'tunnel-client'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# Direct api.openai.com access times out here; the local Clash proxy is required.
$proxy = if ($env:SHIROS_TUNNEL_PROXY) { $env:SHIROS_TUNNEL_PROXY } else { 'http://127.0.0.1:7890' }
$proxyUri = [Uri]$proxy
$probe = New-Object System.Net.Sockets.TcpClient
try {
    $probe.Connect($proxyUri.Host, $proxyUri.Port)
} catch {
    throw "Local proxy $proxy is not reachable. Start Clash, or set SHIROS_TUNNEL_PROXY."
} finally {
    $probe.Dispose()
}

$secure = Read-Host 'Enter the ShirOS tunnel runtime API key (hidden; not saved)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$previousKey = $env:CONTROL_PLANE_API_KEY
$previousHttps = $env:HTTPS_PROXY
$previousHttp = $env:HTTP_PROXY
$previousAll = $env:ALL_PROXY
$previousNo = $env:NO_PROXY
try {
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) { throw 'Empty key.' }
    $env:HTTPS_PROXY = $proxy
    $env:HTTP_PROXY = $proxy
    $env:ALL_PROXY = $proxy
    $env:NO_PROXY = '127.0.0.1,localhost,::1'
    Set-Location -LiteralPath $root
    & $pythonExe $helper --tunnel-id $tunnelId --profile-dir $profileDir
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel runtime connect failed.' }
} finally {
    $env:CONTROL_PLANE_API_KEY = $previousKey
    $env:HTTPS_PROXY = $previousHttps
    $env:HTTP_PROXY = $previousHttp
    $env:ALL_PROXY = $previousAll
    $env:NO_PROXY = $previousNo
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secure.Dispose()
}
