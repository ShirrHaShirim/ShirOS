#requires -Version 5.1
<#
ShirOS resident runtime controller for Windows.

Scope: portable PostgreSQL, the official tunnel-client managed runtime for the
existing ShirOS tunnel, the MCP bridge it supervises, and the OpenAI control
plane connection. The runtime API key lives in Windows Credential Manager and
is injected into the process environment only for the lifetime of a command.

Actions:
  start       Ensure database, migrations and managed tunnel runtime are up.
  stop        Stop tunnel runtime, workbench and (by default) the database.
  restart     Stop then start.
  refresh-mcp Restart only the managed ChatGPT MCP runtime; keep database and UI.
  status      Read-only snapshot; never recovers or changes anything.
  check       Health-check + tiered recovery. Designed for the 5-minute task.
  install     Register per-user scheduled tasks (logon + every 5 minutes).
  uninstall   Remove the scheduled tasks (runtime keeps running).
  store-key   Prompt (hidden input) and store the tunnel runtime API key.
  store-key-gui  Same, via a Windows credential dialog.
  import-key  Import the sk-... key from a local file into Credential Manager.
  key-info    Report only shape checks for the stored key (no characters shown).
  verify-key  Validate the stored key against the control plane (no secrets shown).
  doctor      Run the official tunnel-client doctor with the stored key (redacted).
  connect-verbose  Run managed connect in the foreground with full console output.
  clear-key   Remove the stored key.
  test-key    Report whether a key is stored.
  logs        Print recent runtime and incident log lines.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'start', 'stop', 'restart', 'refresh-mcp', 'status', 'check',
        'install', 'uninstall', 'store-key', 'store-key-gui', 'import-key', 'key-info', 'verify-key', 'doctor', 'connect-verbose',
        'clear-key', 'test-key', 'logs'
    )]
    [string]$Action = 'status',
    [switch]$Json,
    [switch]$KeepDatabase,
    [switch]$Force,
    [string]$KeyFile
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths and constants (all derived; no secrets below)
# ---------------------------------------------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Local = Join-Path $Root '.local'
$RuntimeDir = Join-Path $Local 'runtime'
$LogFile = Join-Path $RuntimeDir 'runtime.log'
$IncidentFile = Join-Path $RuntimeDir 'incidents.jsonl'
$StatusFile = Join-Path $RuntimeDir 'status.json'
$RecoveryFile = Join-Path $RuntimeDir 'recovery.json'
$DesiredStateFile = Join-Path $RuntimeDir 'desired-state'
$HiddenLauncher = Join-Path $PSScriptRoot 'run-hidden.vbs'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$TunnelClient = Join-Path $Local 'tunnel-client\bin\tunnel-client.exe'
$TunnelAlias = 'shiros'
$TunnelProfileFallback = 'shiros-chatgpt'
$TunnelIdFallback = 'tunnel_6ac52f8f597881919a45dfa07dde0b6e'
$ProfileDirFallback = Join-Path $env:APPDATA 'tunnel-client'
$HealthUrlFileFallback = Join-Path $env:USERPROFILE '.local\state\tunnel-client\health\shiros.url'
$CredentialTarget = 'ShirOS/TunnelRuntimeApiKey'
$CredentialUser = 'shiros-tunnel'
$TaskPath = '\ShirOS\'
$TaskLogon = 'Runtime'
$TaskMonitor = 'Health Monitor'

function Initialize-RuntimeDir {
    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Logging (defense-in-depth redaction; secrets never reach files or console)
# ---------------------------------------------------------------------------
$script:SecretPatterns = @(
    '(?i)(sk-[A-Za-z0-9_\-]{8,})',
    '(?i)((?:api[_-]?key|authorization|bearer|token|secret|password)\s*[:=]\s*)\S+'
)

function Remove-SensitiveText {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    $clean = $Text
    foreach ($pattern in $script:SecretPatterns) {
        $clean = [regex]::Replace($clean, $pattern, '$1<redacted>')
    }
    return $clean
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = (& $FilePath @Arguments 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ Output = $output; ExitCode = $code }
}

function Invoke-TunnelClientRaw {
    param([Parameter(Mandatory = $true)][string]$ArgumentLine)
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $TunnelClient
    $startInfo.Arguments = $ArgumentLine
    $startInfo.UseShellExecute = $false
    $startInfo.WorkingDirectory = $Root
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    try {
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $exitCode = $process.ExitCode
    } finally {
        $process.Dispose()
    }
    return [pscustomobject]@{ Output = ($stdout + "`r`n" + $stderr); ExitCode = $exitCode }
}

function Get-ShirosConnectArgumentLine {
    $config = Get-ShirosTunnelConfig
    # Escape the inner quotes of the MCP command for CommandLineToArgvW.
    $escapedMcp = $config.McpCommand.Replace('"', '\"')
    return 'runtimes connect --alias {0} --profile "{1}" --profile-dir "{2}" --tunnel-id {3} --runtime-api-key env:CONTROL_PLANE_API_KEY --mcp-command "{4}"' -f `
        $config.Alias, $config.ProfileName, $config.ProfileDir, $config.TunnelId, $escapedMcp
}

function Write-ShirosLog {
    param(
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO',
        [string]$Message,
        [switch]$PassThru
    )
    Initialize-RuntimeDir
    $safe = Remove-SensitiveText $Message
    $line = '{0} [{1}] {2}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'), $Level, $safe
    if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 2097152) {
        Move-Item -LiteralPath $LogFile -Destination ($LogFile + '.1') -Force
    }
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    if ($PassThru) { return $line }
}

function Write-ShirosIncident {
    param(
        [string]$Component,
        [ValidateSet('failure', 'recovered', 'suppressed')][string]$Event,
        [string]$Detail,
        [double]$DurationSeconds = -1
    )
    Initialize-RuntimeDir
    $record = [ordered]@{
        ts        = (Get-Date).ToString('o')
        component = $Component
        event     = $Event
        detail    = (Remove-SensitiveText $Detail)
    }
    if ($DurationSeconds -ge 0) { $record['duration_seconds'] = [math]::Round($DurationSeconds, 1) }
    Add-Content -LiteralPath $IncidentFile -Value ($record | ConvertTo-Json -Compress -Depth 4) -Encoding UTF8
}

function Get-DesiredState {
    if (Test-Path -LiteralPath $DesiredStateFile) {
        $value = (Get-Content -LiteralPath $DesiredStateFile -Raw).Trim()
        if ($value -in @('running', 'stopped')) { return $value }
    }
    return 'running'
}

function Set-DesiredState {
    param([ValidateSet('running', 'stopped')][string]$State)
    Initialize-RuntimeDir
    Set-Content -LiteralPath $DesiredStateFile -Value $State -Encoding ASCII
}

# ---------------------------------------------------------------------------
# Recovery bookkeeping (rate limits + incident durations)
# ---------------------------------------------------------------------------
function Get-RecoveryState {
    if (Test-Path -LiteralPath $RecoveryFile) {
        try {
            $raw = Get-Content -LiteralPath $RecoveryFile -Raw
            if ($raw.Trim()) { return ($raw | ConvertFrom-Json) }
        } catch { }
    }
    return [pscustomobject]@{ recoveries = [pscustomobject]@{}; active = [pscustomobject]@{} }
}

function Save-RecoveryState {
    param($State)
    Initialize-RuntimeDir
    $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $RecoveryFile -Encoding UTF8
}

function Test-RecoveryAllowed {
    param(
        [string]$Component,
        [int]$WindowMinutes = 30,
        [int]$MaxAttempts = 3
    )
    $state = Get-RecoveryState
    $recoveries = [ordered]@{}
    if ($state.recoveries) {
        foreach ($property in $state.recoveries.PSObject.Properties) {
            $recoveries[$property.Name] = @($property.Value)
        }
    }
    $attempts = @()
    if ($recoveries.Contains($Component)) { $attempts = @($recoveries[$Component]) }
    $now = Get-Date
    $recent = @($attempts | Where-Object { $_ -and ([datetime]$_).ToUniversalTime() -gt $now.ToUniversalTime().AddMinutes(-$WindowMinutes) })
    if ($recent.Count -ge $MaxAttempts) {
        Write-ShirosLog WARN ("Recovery for {0} suppressed: {1} attempts within {2} minutes" -f $Component, $recent.Count, $WindowMinutes)
        Write-ShirosIncident -Component $Component -Event suppressed -Detail ("rate limit reached: {0} attempts in {1} minutes" -f $recent.Count, $WindowMinutes)
        return $false
    }
    $recent += $now.ToString('o')
    $recoveries[$Component] = $recent
    $state.recoveries = [pscustomobject]$recoveries
    Save-RecoveryState $state
    return $true
}

function Register-ComponentFailure {
    param([string]$Component, [string]$Detail)
    $state = Get-RecoveryState
    $active = [ordered]@{}
    if ($state.active) {
        foreach ($property in $state.active.PSObject.Properties) { $active[$property.Name] = $property.Value }
    }
    if (-not $active.Contains($Component)) {
        $active[$Component] = (Get-Date).ToString('o')
        Write-ShirosIncident -Component $Component -Event failure -Detail $Detail
    }
    $state.active = [pscustomobject]$active
    Save-RecoveryState $state
}

function Resolve-ComponentFailure {
    param([string]$Component, [string]$Detail)
    $state = Get-RecoveryState
    $active = [ordered]@{}
    if ($state.active) {
        foreach ($property in $state.active.PSObject.Properties) { $active[$property.Name] = $property.Value }
    }
    if ($active.Contains($Component)) {
        $started = [datetime]$active[$Component]
        $duration = ((Get-Date) - $started).TotalSeconds
        $active.Remove($Component) | Out-Null
        Write-ShirosIncident -Component $Component -Event recovered -Detail $Detail -DurationSeconds $duration
        Write-ShirosLog INFO ("Recovered {0} after {1:N0}s: {2}" -f $Component, $duration, $Detail)
    }
    $state.active = [pscustomobject]$active
    Save-RecoveryState $state
}

# ---------------------------------------------------------------------------
# Windows Credential Manager (DPAPI-protected, current user)
# ---------------------------------------------------------------------------
if (-not ('ShirosCredentialStore' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class ShirosCredentialStore
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct CREDENTIAL
    {
        public uint Flags;
        public uint Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredWriteW")]
    private static extern bool CredWrite(ref CREDENTIAL credential, uint flags);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredReadW")]
    private static extern bool CredRead(string target, uint type, uint reservedFlag, out IntPtr credentialPtr);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredDeleteW")]
    private static extern bool CredDelete(string target, uint type, uint flags);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern void CredFree(IntPtr buffer);

    private const uint CRED_TYPE_GENERIC = 1;
    private const uint CRED_PERSIST_LOCAL_MACHINE = 2;
    private const int ERROR_NOT_FOUND = 1168;

    public static void Write(string target, string userName, string secret)
    {
        byte[] secretBytes = System.Text.Encoding.Unicode.GetBytes(secret);
        CREDENTIAL credential = new CREDENTIAL();
        credential.Type = CRED_TYPE_GENERIC;
        credential.TargetName = target;
        credential.UserName = userName;
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
        credential.CredentialBlobSize = (uint)secretBytes.Length;
        credential.CredentialBlob = Marshal.AllocHGlobal(secretBytes.Length);
        try
        {
            Marshal.Copy(secretBytes, 0, credential.CredentialBlob, secretBytes.Length);
            if (!CredWrite(ref credential, 0))
            {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            Marshal.Copy(new byte[secretBytes.Length], 0, credential.CredentialBlob, secretBytes.Length);
            Marshal.FreeHGlobal(credential.CredentialBlob);
            Array.Clear(secretBytes, 0, secretBytes.Length);
        }
    }

    public static string Read(string target)
    {
        IntPtr credentialPtr;
        if (!CredRead(target, CRED_TYPE_GENERIC, 0, out credentialPtr))
        {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        try
        {
            CREDENTIAL credential = (CREDENTIAL)Marshal.PtrToStructure(credentialPtr, typeof(CREDENTIAL));
            if (credential.CredentialBlobSize == 0) { return string.Empty; }
            byte[] secretBytes = new byte[credential.CredentialBlobSize];
            Marshal.Copy(credential.CredentialBlob, secretBytes, 0, secretBytes.Length);
            string result = System.Text.Encoding.Unicode.GetString(secretBytes);
            Array.Clear(secretBytes, 0, secretBytes.Length);
            return result;
        }
        finally
        {
            CredFree(credentialPtr);
        }
    }

    public static bool Exists(string target)
    {
        IntPtr credentialPtr;
        if (!CredRead(target, CRED_TYPE_GENERIC, 0, out credentialPtr)) { return false; }
        CredFree(credentialPtr);
        return true;
    }

    public static void Delete(string target)
    {
        if (!CredDelete(target, CRED_TYPE_GENERIC, 0))
        {
            int error = Marshal.GetLastWin32Error();
            if (error != ERROR_NOT_FOUND)
            {
                throw new System.ComponentModel.Win32Exception(error);
            }
        }
    }
}
'@
}

function Read-ShirosTunnelKey {
    if (-not [ShirosCredentialStore]::Exists($CredentialTarget)) {
        throw 'Tunnel runtime API key is not stored. Run: ShirOS.cmd store-key'
    }
    $key = [ShirosCredentialStore]::Read($CredentialTarget)
    $key = $key.Trim()
    if ([string]::IsNullOrWhiteSpace($key)) {
        throw 'Stored tunnel runtime API key is empty. Run: ShirOS.cmd store-key'
    }
    return $key
}

# ---------------------------------------------------------------------------
# Alias metadata: keep the existing tunnel id, profile and workspace identity
# ---------------------------------------------------------------------------
function Get-ShirosAliasMetadata {
    $file = Join-Path $env:USERPROFILE '.local\state\tunnel-client\aliases.yaml'
    if (Test-Path -LiteralPath $file) {
        try {
            $data = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
            if ($data.$TunnelAlias) { return $data.$TunnelAlias }
        } catch { }
    }
    return $null
}

function Get-ShirosTunnelConfig {
    $meta = Get-ShirosAliasMetadata
    $profileName = $TunnelProfileFallback
    $profileDir = $ProfileDirFallback
    $tunnelId = $TunnelIdFallback
    $healthFile = $HealthUrlFileFallback
    if ($meta) {
        if ($meta.profile_name) { $profileName = $meta.profile_name }
        if ($meta.profile_dir) { $profileDir = $meta.profile_dir }
        if ($meta.tunnel_id) { $tunnelId = $meta.tunnel_id }
        if ($meta.health_url_file) { $healthFile = $meta.health_url_file }
    }
    return [pscustomobject]@{
        Alias       = $TunnelAlias
        ProfileName = $profileName
        ProfileDir  = $profileDir
        TunnelId    = $tunnelId
        HealthFile  = $healthFile
        McpCommand  = ('"{0}" -m shiros.memory_bridge serve --client chatgpt' -f ($Pythonw -replace '\\', '/'))
    }
}

# ---------------------------------------------------------------------------
# Proxy detection (Clash Party / Clash Verge / mihomo, system proxy)
# ---------------------------------------------------------------------------
function Test-ShirosTcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMilliseconds = 1500)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-ShirosProxyState {
    $url = $env:SHIROS_TUNNEL_PROXY
    $source = 'override'
    if ([string]::IsNullOrWhiteSpace($url)) {
        $source = 'system'
        $settings = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
        if ($settings -and $settings.ProxyEnable -eq 1 -and $settings.ProxyServer) {
            $url = [string]$settings.ProxyServer
        }
    }
    if ([string]::IsNullOrWhiteSpace($url)) {
        $source = 'localhost-default'
        $url = 'http://127.0.0.1:7890'
    }
    if ($url -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') { $url = 'http://' + $url }
    $url = ($url -split ';')[0].Trim()
    $hostName = ''
    $port = 0
    try {
        $uri = [Uri]$url
        $hostName = $uri.Host
        $port = $uri.Port
    } catch { }
    $clash = @(Get-Process -Name 'Clash Party', 'mihomo', 'clash-verge', 'verge-mihomo', 'clash-verge-service' -ErrorAction SilentlyContinue)
    $reachable = $false
    if ($hostName -and $port -gt 0) { $reachable = Test-ShirosTcpPort -HostName $hostName -Port $port }
    return [pscustomobject]@{
        Url          = $url
        Host         = $hostName
        Port         = $port
        Source       = $source
        Reachable    = $reachable
        ClashRunning = ($clash.Count -gt 0)
        ClashNames   = @($clash | ForEach-Object { $_.ProcessName } | Select-Object -Unique)
    }
}

function Test-ShirosControlPlaneNetwork {
    param([string]$ProxyUrl)
    $result = [ordered]@{ reachable = $false; status = 0; error = '' }
    try {
        $request = [System.Net.HttpWebRequest]::Create('https://api.openai.com/v1/models')
        $request.Method = 'GET'
        $request.Timeout = 8000
        $request.ReadWriteTimeout = 8000
        $request.UserAgent = 'ShirOS-Runtime-Monitor/1.0'
        if ($ProxyUrl) {
            $request.Proxy = New-Object System.Net.WebProxy($ProxyUrl, $false)
        } else {
            $request.Proxy = $null
        }
        $response = $request.GetResponse()
        $result.reachable = $true
        $result.status = [int]$response.StatusCode
        $response.Close()
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $result.reachable = $true
            $result.status = [int]$_.Exception.Response.StatusCode
        } else {
            $result.error = Remove-SensitiveText $_.Exception.Message
        }
    } catch {
        $result.error = Remove-SensitiveText $_.Exception.Message
    }
    return [pscustomobject]$result
}

# ---------------------------------------------------------------------------
# Component probes
# ---------------------------------------------------------------------------
function Get-ShirosDbState {
    $result = [ordered]@{
        ready     = $false
        reachable = $false
        migration = ''
        vector    = ''
        error     = ''
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        $result.error = 'Python environment missing'
        return [pscustomobject]$result
    }
    $output = ''
    $capture = Invoke-NativeCapture -FilePath $Python -Arguments @('-m', 'shiros.cli', 'db-health')
    $output = $capture.Output
    $exitCode = $capture.ExitCode
    $jsonLine = ($output -split "`r?`n" | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1)
    if ($jsonLine) {
        try {
            $parsed = $jsonLine | ConvertFrom-Json
            $result.ready = [bool]$parsed.ready
            $result.reachable = $true
            if ($parsed.migration) { $result.migration = [string]$parsed.migration }
            if ($parsed.vector_version) { $result.vector = [string]$parsed.vector_version }
        } catch { }
    }
    if (-not $result.reachable) {
        $settings = Get-Content -LiteralPath (Join-Path $Root '.env') -ErrorAction SilentlyContinue
        $port = 55432
        $portLine = $settings | Where-Object { $_ -match '^SHIROS_DB_PORT=(\d+)' } | Select-Object -First 1
        if ($portLine -and $portLine -match '^SHIROS_DB_PORT=(\d+)') { $port = [int]$matches[1] }
        $result.reachable = Test-ShirosTcpPort -HostName 127.0.0.1 -Port $port
        if ($exitCode -ne 0) { $result.error = 'db-health failed' }
    }
    return [pscustomobject]$result
}

function Get-ShirosTunnelState {
    $config = Get-ShirosTunnelConfig
    $state = [ordered]@{
        process_running    = $false
        pids               = @()
        runtime_state      = 'unknown'
        ready              = $false
        healthy            = $false
        control_plane_poll = $false
        control_plane_note = ''
        local_healthz      = $false
        local_readyz       = $false
        error              = ''
    }
    if (-not (Test-Path -LiteralPath $TunnelClient)) {
        $state.error = 'tunnel-client binary missing'
        return [pscustomobject]$state
    }
    $processes = @(Get-Process -Name 'tunnel-client' -ErrorAction SilentlyContinue)
    if ($processes.Count -gt 0) {
        $state.process_running = $true
        $state.pids = @($processes | ForEach-Object { $_.Id })
    }
    try {
        $statusCapture = Invoke-NativeCapture -FilePath $TunnelClient -Arguments @('runtimes', 'status', $config.Alias, '--json')
        $statusRaw = $statusCapture.Output
        if ($statusRaw.Trim()) {
            $status = $statusRaw | ConvertFrom-Json
            if ($null -ne $status.process_running) {
                # A detached `run --config` process can outlive stale managed
                # runtime metadata. Its actual health endpoint remains authoritative.
                $state.process_running = $state.process_running -or [bool]$status.process_running
            }
            $state.ready = [bool]$status.ready
            $state.healthy = [bool]$status.healthy
            $state.runtime_state = [string]$status.runtime_state
            if ($status.local -and $status.local.control_plane_poll_health) {
                $pollState = [string]$status.local.control_plane_poll_health.state
                if ($pollState) { $state.control_plane_note = $pollState }
                if ($pollState -eq 'healthy') { $state.control_plane_poll = $true }
            }
        }
    } catch {
        $state.error = Remove-SensitiveText $_.Exception.Message
    }
    if ($state.process_running) {
        try {
            $healthCapture = Invoke-NativeCapture -FilePath $TunnelClient -Arguments @('health', '--url-file', $config.HealthFile, '--json', '--require-control-plane-poll')
            $healthRaw = $healthCapture.Output
            $healthExit = $healthCapture.ExitCode
            if ($healthRaw.Trim()) {
                $health = $healthRaw | ConvertFrom-Json
                $state.local_healthz = [bool]$health.healthz.ok
                $state.local_readyz = [bool]$health.readyz.ok
                if ($health.control_plane_poll) {
                    $state.control_plane_poll = [bool]$health.control_plane_poll.ok
                    if ($health.control_plane_poll.error) {
                        $state.control_plane_note = Remove-SensitiveText ([string]$health.control_plane_poll.error)
                    }
                }
                $state.ready = ($healthExit -eq 0)
            }
        } catch {
            if (-not $state.error) { $state.error = Remove-SensitiveText $_.Exception.Message }
        }
    }
    return [pscustomobject]$state
}

function Get-ShirosMcpState {
    $bridges = @()
    try {
        $bridges = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'shiros\.memory_bridge\s+serve' -and $_.CommandLine -match '--client\s+chatgpt' })
    } catch { }
    return [pscustomobject]@{
        bridge_processes = @($bridges | ForEach-Object { $_.ProcessId })
        ok               = ($bridges.Count -gt 0)
    }
}

function Get-ShirosWorkbenchState {
    $state = [ordered]@{ running = $false; port = 0; error = '' }
    $sessionFile = Join-Path $Local 'browser-session\session.json'
    $port = 0
    if (Test-Path -LiteralPath $sessionFile) {
        try {
            $session = Get-Content -LiteralPath $sessionFile -Raw | ConvertFrom-Json
            if ($session.port) { $port = [int]$session.port }
        } catch { }
    }
    if ($port -le 0) { $port = 8001 }
    $state.port = $port
    try {
        $request = [System.Net.HttpWebRequest]::Create(('http://127.0.0.1:{0}/workbench-health' -f $port))
        $request.Timeout = 2000
        $request.Proxy = $null
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Close()
        $response.Close()
        $state.running = ($body -match 'ShirOS Workbench')
    } catch {
        $state.error = 'not reachable'
    }
    return [pscustomobject]$state
}

# ---------------------------------------------------------------------------
# Environment injection for tunnel commands (key + proxy, process scope only)
# ---------------------------------------------------------------------------
function Invoke-WithShirosTunnelEnvironment {
    param([Parameter(Mandatory = $true)][scriptblock]$Body)
    $names = @('CONTROL_PLANE_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    try {
        $env:CONTROL_PLANE_API_KEY = Read-ShirosTunnelKey
        $proxy = Get-ShirosProxyState
        if ($proxy.Url) {
            $env:HTTP_PROXY = $proxy.Url
            $env:HTTPS_PROXY = $proxy.Url
            $env:ALL_PROXY = $proxy.Url
            $env:NO_PROXY = '127.0.0.1,localhost,::1'
        }
        & $Body
    } finally {
        foreach ($name in $names) {
            if ($null -eq $saved[$name]) {
                Remove-Item -Path ('Env:' + $name) -ErrorAction SilentlyContinue
            } else {
                Set-Item -Path ('Env:' + $name) -Value $saved[$name]
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Recovery operations
# ---------------------------------------------------------------------------
function Start-ShirosDatabase {
    param([switch]$Migrate)
    $state = Get-ShirosDbState
    if ($state.ready) { return $true }
    if (-not $state.reachable) {
        Write-ShirosLog INFO 'PostgreSQL is not accepting connections; starting portable runtime'
        $startCapture = Invoke-NativeCapture -FilePath $Python -Arguments @((Join-Path $PSScriptRoot 'local_db.py'), 'start')
        if ($startCapture.ExitCode -ne 0) {
            $tail = ($startCapture.Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' | '
            Write-ShirosLog WARN ('local_db start exited with code {0}: {1}' -f $startCapture.ExitCode, $tail)
        }
        $state = Get-ShirosDbState
    }
    if (($Migrate -or -not $state.ready) -and $state.reachable) {
        Write-ShirosLog INFO 'Applying ShirOS migrations'
        $migrateCapture = Invoke-NativeCapture -FilePath $Python -Arguments @('-m', 'shiros.cli', 'migrate')
        if ($migrateCapture.ExitCode -ne 0) {
            $tail = ($migrateCapture.Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' | '
            Write-ShirosLog WARN ('migrate exited with code {0}: {1}' -f $migrateCapture.ExitCode, $tail)
        }
    }
    # Cold start after a full stop can take well over a minute on Windows.
    $deadline = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ShirosDbState).ready) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Remove-ShirosOrphanBridges {
    $tunnelPids = @(Get-Process -Name 'tunnel-client' -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $bridges = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'shiros\.memory_bridge\s+serve' -and $_.CommandLine -match '--client\s+chatgpt' })
    foreach ($bridge in $bridges) {
        $ancestor = [int]$bridge.ParentProcessId
        $depth = 0
        $belongs = $false
        while ($depth -lt 4 -and $ancestor -gt 0) {
            if ($tunnelPids -contains $ancestor) { $belongs = $true; break }
            $parent = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ancestor) -ErrorAction SilentlyContinue
            if (-not $parent) { break }
            $ancestor = [int]$parent.ParentProcessId
            $depth++
        }
        if (-not $belongs) {
            Stop-Process -Id $bridge.ProcessId -Force -ErrorAction SilentlyContinue
            Write-ShirosLog WARN ('Removed orphaned MCP bridge process pid {0}' -f $bridge.ProcessId)
        }
    }
}

function Stop-ShirosTunnelProcesses {
    $config = Get-ShirosTunnelConfig
    $targets = @(Get-CimInstance Win32_Process -Filter "Name = 'tunnel-client.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($config.ProfileName) })
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-ShirosTunnel {
    param([switch]$ForceKill)
    $state = Get-ShirosTunnelState
    if (-not $state.process_running) {
        Write-ShirosLog INFO 'Managed tunnel runtime already stopped'
        return
    }
    Write-ShirosLog INFO 'Stopping managed tunnel runtime'
    $stopCapture = Invoke-NativeCapture -FilePath $TunnelClient -Arguments @('runtimes', 'stop', $TunnelAlias)
    if ($stopCapture.ExitCode -ne 0) {
        Write-ShirosLog WARN ('runtimes stop exited with code {0}' -f $stopCapture.ExitCode)
    }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not @(Get-Process -Name 'tunnel-client' -ErrorAction SilentlyContinue).Count) { return }
        Start-Sleep -Seconds 1
    }
    $state = Get-ShirosTunnelState
    if ($state.process_running) {
        Write-ShirosLog WARN 'Managed stop did not exit in time; terminating only the ShirOS runtime process'
        Stop-ShirosTunnelProcesses
        Start-Sleep -Seconds 2
    }
}

function Start-ShirosTunnel {
    param([string]$Reason = 'manual')
    $config = Get-ShirosTunnelConfig
    $state = Get-ShirosTunnelState
    if ($state.process_running -and $state.ready -and $state.control_plane_poll) {
        Write-ShirosLog INFO 'Managed tunnel runtime already running with a live control-plane poll'
        Remove-ShirosOrphanBridges
        return $true
    }
    if ($state.process_running) {
        Write-ShirosLog WARN ("Managed tunnel runtime is unhealthy (ready={0}, poll={1}); reconnecting via managed stop/connect" -f $state.ready, $state.control_plane_poll)
        Stop-ShirosTunnel
    }
    $connectOutput = ''
    $connectExit = 1
    Invoke-WithShirosTunnelEnvironment {
        $argumentLine = Get-ShirosConnectArgumentLine
        Write-ShirosLog INFO ("Starting managed tunnel runtime ({0})" -f $Reason)
        $capture = Invoke-TunnelClientRaw -ArgumentLine $argumentLine
        $script:ConnectOutput = $capture.Output
        $script:ConnectExit = $capture.ExitCode
    }
    $connectExit = $script:ConnectExit
    $connectOutput = $script:ConnectOutput
    $debugFile = Join-Path $RuntimeDir 'last-connect-output.txt'
    $debugText = 'exit code: {0}' -f $connectExit
    if ($connectOutput.Trim()) {
        $debugText += "`r`n" + (($connectOutput -split "`r?`n" | Select-Object -First 100) -join "`r`n")
    }
    (Remove-SensitiveText $debugText) | Set-Content -LiteralPath $debugFile -Encoding UTF8
    if ($connectOutput.Trim()) {
        $tail = ($connectOutput -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '
        Write-ShirosLog INFO ('runtimes connect: ' + $tail)
    }
    if ($connectExit -ne 0) {
        Write-ShirosLog ERROR ("runtimes connect exited with code {0}" -f $connectExit)
        if ($connectOutput -match 'too many levels of symbolic links') {
            # Some Windows tunnel-client builds cannot rewrite an existing regular
            # profile. Reuse it through the existing detached run helper instead.
            Write-ShirosLog INFO 'Reconnecting through the existing profile without regenerating it'
            Invoke-WithShirosTunnelEnvironment {
                $fallback = Invoke-NativeCapture -FilePath $Python -Arguments @(
                    (Join-Path $PSScriptRoot 'connect_memory_tunnel.py'),
                    '--alias', $config.Alias, '--profile', $config.ProfileName,
                    '--profile-dir', $config.ProfileDir, '--tunnel-id', $config.TunnelId
                )
                $script:FallbackConnectExit = $fallback.ExitCode
            }
            if ($script:FallbackConnectExit -ne 0) {
                Write-ShirosLog ERROR 'Existing-profile reconnect failed'
            }
        }
    }
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        $probe = Get-ShirosTunnelState
        if ($probe.process_running -and $probe.ready -and $probe.control_plane_poll) {
            Write-ShirosLog INFO 'Managed tunnel runtime is ready with a successful control-plane poll'
            Remove-ShirosOrphanBridges
            return $true
        }
        Start-Sleep -Seconds 3
    }
    Write-ShirosLog ERROR 'Managed tunnel runtime did not reach a live control-plane poll within 90s'
    return $false
}

function Stop-ShirosWorkbench {
    $targets = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'shiros\.cli\s+web' })
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
    return ($targets.Count)
}

# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------
function Get-ShirosSnapshot {
    $config = Get-ShirosTunnelConfig
    $proxy = Get-ShirosProxyState
    $network = [pscustomobject]@{ reachable = $false; status = 0; error = 'proxy unreachable' }
    if ($proxy.Reachable) {
        $network = Test-ShirosControlPlaneNetwork -ProxyUrl $proxy.Url
    }
    $db = Get-ShirosDbState
    $tunnel = Get-ShirosTunnelState
    $mcp = Get-ShirosMcpState
    $workbench = Get-ShirosWorkbenchState
    $desired = Get-DesiredState
    $recovery = Get-RecoveryState
    $activeComponents = @()
    if ($recovery.active) {
        $activeComponents = @($recovery.active.PSObject.Properties | ForEach-Object { $_.Name })
    }

    $overall = 'healthy'
    $networkDown = (-not $proxy.Reachable) -or (-not $network.reachable)
    if ($desired -eq 'stopped') {
        $overall = 'stopped'
    } elseif ($networkDown -and (-not $db.ready -or -not $tunnel.process_running -or -not $tunnel.control_plane_poll)) {
        $overall = 'environment-degraded'
    } elseif ($networkDown) {
        $overall = 'environment-degraded'
    } elseif (-not $db.ready -or -not $tunnel.process_running -or -not $tunnel.control_plane_poll) {
        $overall = 'degraded'
    }

    return [pscustomobject][ordered]@{
        timestamp          = (Get-Date).ToString('o')
        desired_state      = $desired
        overall            = $overall
        proxy              = $proxy
        control_plane_net  = $network
        database           = $db
        tunnel             = $tunnel
        mcp                = $mcp
        workbench          = $workbench
        active_incidents   = $activeComponents
        tunnel_identity    = [pscustomobject]@{ alias = $config.Alias; tunnel_id = $config.TunnelId; profile = $config.ProfileName }
    }
}

function Save-ShirosSnapshot {
    param($Snapshot)
    Initialize-RuntimeDir
    $Snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatusFile -Encoding UTF8
}

function Show-ShirosSnapshot {
    param($Snapshot)
    $proxyText = '{0} ({1})' -f $Snapshot.proxy.Url, $Snapshot.proxy.Source
    if (-not $Snapshot.proxy.Reachable) { $proxyText += ' [unreachable]' }
    $netText = if ($Snapshot.control_plane_net.reachable) { 'reachable (HTTP {0})' -f $Snapshot.control_plane_net.status } else { 'unreachable' }
    $dbText = if ($Snapshot.database.ready) { 'ready (migration {0}, vector {1})' -f $Snapshot.database.migration, $Snapshot.database.vector } else { 'not ready' }
    $tunnelText = if ($Snapshot.tunnel.process_running) {
        'running pid={0} ready={1} control-plane-poll={2}' -f (($Snapshot.tunnel.pids) -join ','), $Snapshot.tunnel.ready, $Snapshot.tunnel.control_plane_poll
    } else { 'stopped' }
    $mcpText = if ($Snapshot.mcp.ok) { 'bridge pid={0}' -f (($Snapshot.mcp.bridge_processes) -join ',') } else { 'no chatgpt bridge process' }
    $wbText = if ($Snapshot.workbench.running) { 'running on 127.0.0.1:{0}' -f $Snapshot.workbench.port } else { 'not running' }
    $lines = @(
        ('ShirOS runtime status: {0} (desired: {1})' -f $Snapshot.overall, $Snapshot.desired_state),
        ('  proxy         : {0}' -f $proxyText),
        ('  control plane : {0}' -f $netText),
        ('  database      : {0}' -f $dbText),
        ('  tunnel        : {0}' -f $tunnelText),
        ('  mcp bridge    : {0}' -f $mcpText),
        ('  workbench     : {0}' -f $wbText),
        ('  tunnel id     : {0}' -f $Snapshot.tunnel_identity.tunnel_id)
    )
    if ($Snapshot.active_incidents.Count -gt 0) {
        $lines += ('  incidents     : ' + ($Snapshot.active_incidents -join ', '))
    }
    $lines | ForEach-Object { Write-Host $_ }
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
function Invoke-ShirosStart {
    Initialize-RuntimeDir
    Set-DesiredState running
    Write-ShirosLog INFO 'Start requested'
    $snapshot = Get-ShirosSnapshot
    if (-not $snapshot.database.ready) {
        if (-not (Start-ShirosDatabase -Migrate)) {
            Register-ComponentFailure -Component 'database' -Detail 'database did not become ready during start'
            $snapshot = Get-ShirosSnapshot
            Save-ShirosSnapshot $snapshot
            Show-ShirosSnapshot $snapshot
            Write-ShirosLog ERROR 'Database did not become ready'
            return 1
        }
        Resolve-ComponentFailure -Component 'database' -Detail 'database ready after start'
    }
    $proxy = Get-ShirosProxyState
    if (-not $proxy.Reachable) {
        Write-ShirosLog WARN ('Proxy {0} is not reachable; deferred tunnel start (Clash may still be starting)' -f $proxy.Url)
        $snapshot = Get-ShirosSnapshot
        Save-ShirosSnapshot $snapshot
        Show-ShirosSnapshot $snapshot
        return 1
    }
    if (Start-ShirosTunnel -Reason 'start') {
        Resolve-ComponentFailure -Component 'tunnel' -Detail 'tunnel ready after start'
    } else {
        Register-ComponentFailure -Component 'tunnel' -Detail 'tunnel did not become ready during start'
        $snapshot = Get-ShirosSnapshot
        Save-ShirosSnapshot $snapshot
        Show-ShirosSnapshot $snapshot
        return 1
    }
    $snapshot = Get-ShirosSnapshot
    Save-ShirosSnapshot $snapshot
    Show-ShirosSnapshot $snapshot
    if ($snapshot.overall -eq 'healthy') { return 0 }
    return 1
}

function Invoke-ShirosStop {
    Initialize-RuntimeDir
    Set-DesiredState stopped
    Write-ShirosLog INFO 'Stop requested'
    Stop-ShirosTunnel
    $webStopped = Stop-ShirosWorkbench
    if ($webStopped -gt 0) { Write-ShirosLog INFO ('Stopped workbench processes: {0}' -f $webStopped) }
    if (-not $KeepDatabase) {
        try {
            & $Python (Join-Path $PSScriptRoot 'local_db.py') stop | Out-Null
            Write-ShirosLog INFO 'PostgreSQL stopped'
        } catch {
            Write-ShirosLog WARN ('PostgreSQL stop failed: ' + (Remove-SensitiveText $_.Exception.Message))
        }
    }
    Resolve-ComponentFailure -Component 'tunnel' -Detail 'runtime stopped by operator'
    Resolve-ComponentFailure -Component 'database' -Detail 'database stopped by operator'
    $snapshot = Get-ShirosSnapshot
    Save-ShirosSnapshot $snapshot
    Show-ShirosSnapshot $snapshot
    return 0
}

function Invoke-ShirosCheck {
    Initialize-RuntimeDir
    $desired = Get-DesiredState
    $snapshot = Get-ShirosSnapshot
    $actions = @()
    $script:CheckExitCode = 1

    if ($desired -eq 'stopped') {
        Write-ShirosLog INFO 'Check skipped recovery: operator set desired state to stopped'
        Save-ShirosSnapshot $snapshot
        $script:CheckExitCode = 0
        return
    }

    # 1. Database
    if (-not $snapshot.database.ready) {
        Register-ComponentFailure -Component 'database' -Detail 'db-health not ready'
        if (Test-RecoveryAllowed -Component 'db') {
            if (Start-ShirosDatabase -Migrate) {
                $actions += 'database-restarted'
                Resolve-ComponentFailure -Component 'database' -Detail 'database ready after recovery'
            } else {
                Write-ShirosLog ERROR 'Database recovery failed'
                $actions += 'database-recovery-failed'
            }
        } else {
            $actions += 'database-recovery-suppressed'
        }
    } else {
        Resolve-ComponentFailure -Component 'database' -Detail 'database healthy'
    }

    # 2. Network path: proxy and control-plane reachability
    $proxy = $snapshot.proxy
    $networkOk = $proxy.Reachable -and $snapshot.control_plane_net.reachable
    if (-not $proxy.Reachable) {
        Write-ShirosLog WARN ('Proxy {0} unreachable; environment condition, no tunnel restart' -f $proxy.Url)
        $actions += 'proxy-unreachable'
    } elseif (-not $snapshot.control_plane_net.reachable) {
        Write-ShirosLog WARN ('OpenAI control plane unreachable through proxy {0}; environment condition, no tunnel restart' -f $proxy.Url)
        $actions += 'control-plane-unreachable'
    }

    # 3. Tunnel and control-plane poll (only restart when the network path is usable)
    if ($networkOk) {
        if (-not $snapshot.tunnel.process_running) {
            Register-ComponentFailure -Component 'tunnel' -Detail 'managed runtime not running'
            if (Test-RecoveryAllowed -Component 'tunnel') {
                if (Start-ShirosTunnel -Reason 'monitor') {
                    $actions += 'tunnel-started'
                    Resolve-ComponentFailure -Component 'tunnel' -Detail 'tunnel started by monitor'
                } else {
                    $actions += 'tunnel-start-failed'
                }
            } else {
                $actions += 'tunnel-recovery-suppressed'
            }
        } elseif (-not ($snapshot.tunnel.ready -and $snapshot.tunnel.control_plane_poll)) {
            Register-ComponentFailure -Component 'tunnel' -Detail ('process running but ready={0} poll={1}' -f $snapshot.tunnel.ready, $snapshot.tunnel.control_plane_poll)
            if (Test-RecoveryAllowed -Component 'tunnel') {
                if (Start-ShirosTunnel -Reason 'monitor:unhealthy') {
                    $actions += 'tunnel-reconnected'
                    Resolve-ComponentFailure -Component 'tunnel' -Detail 'tunnel reconnected by monitor'
                } else {
                    $actions += 'tunnel-reconnect-failed'
                }
            } else {
                $actions += 'tunnel-recovery-suppressed'
            }
        } else {
            Resolve-ComponentFailure -Component 'tunnel' -Detail 'tunnel healthy with live control-plane poll'
            Remove-ShirosOrphanBridges
        }
    } else {
        if ($snapshot.tunnel.process_running -and $snapshot.tunnel.control_plane_poll) {
            Write-ShirosLog INFO 'Tunnel is running; environment path issue is transient, leaving runtime untouched'
        } elseif (-not $snapshot.tunnel.process_running) {
            Register-ComponentFailure -Component 'tunnel' -Detail 'runtime stopped while network path unavailable; start deferred'
            $actions += 'tunnel-start-deferred'
        }
    }

    $snapshot = Get-ShirosSnapshot
    $snapshot | Add-Member -NotePropertyName actions -NotePropertyValue $actions -Force
    Save-ShirosSnapshot $snapshot
    if (-not $Json) {
        Show-ShirosSnapshot $snapshot
        if ($actions.Count -gt 0) { Write-Host ('  actions       : ' + ($actions -join ', ')) }
    } else {
        $snapshot | ConvertTo-Json -Depth 8
    }
    Write-ShirosLog INFO ('Check complete: overall={0} actions={1}' -f $snapshot.overall, ($(if ($actions.Count) { $actions -join ',' } else { 'none' })))
    if ($snapshot.overall -eq 'healthy' -or $snapshot.overall -eq 'stopped') {
        $script:CheckExitCode = 0
    } else {
        $script:CheckExitCode = 1
    }
}

function Invoke-ShirosInstall {
    Initialize-RuntimeDir
    $config = Get-ShirosTunnelConfig
    if (-not (Test-Path -LiteralPath $HiddenLauncher)) { throw 'Hidden launcher script is missing.' }
    $userId = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    $actionStart = New-ScheduledTaskAction -Execute 'wscript.exe' `
        -Argument ('//B //NoLogo "{0}" "{1}" start' -f $HiddenLauncher, (Join-Path $PSScriptRoot 'ShirosRuntime.ps1'))
    $actionCheck = New-ScheduledTaskAction -Execute 'wscript.exe' `
        -Argument ('//B //NoLogo "{0}" "{1}" check' -f $HiddenLauncher, (Join-Path $PSScriptRoot 'ShirosRuntime.ps1'))

    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    try { $logonTrigger.Delay = 'PT30S' } catch { Write-ShirosLog WARN 'Could not set logon trigger delay' }

    # Task Scheduler rejects TimeSpan.MaxValue; 3650 days is effectively indefinite.
    $repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)

    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskLogon -Action $actionStart -Trigger $logonTrigger `
        -Settings $settings -Principal $principal -Description ('Start the resident ShirOS runtime at logon. Tunnel {0}.' -f $config.TunnelId) -Force | Out-Null
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskMonitor -Action $actionCheck -Trigger $repeatTrigger `
        -Settings $settings -Principal $principal -Description 'Check ShirOS database, MCP bridge, tunnel and OpenAI control-plane connection every 5 minutes; recover tiered failures.' -Force | Out-Null

    Set-DesiredState running
    Write-ShirosLog INFO ('Scheduled tasks installed for {0}' -f $userId)
    Write-Host 'Scheduled tasks installed:'
    Get-ScheduledTask -TaskPath $TaskPath | Select-Object TaskName, State | Format-Table -AutoSize | Out-Host
    Write-Host 'Logon task starts the runtime 30s after sign-in; monitor task runs every 5 minutes.'
    return 0
}

function Invoke-ShirosUninstall {
    foreach ($name in @($TaskLogon, $TaskMonitor)) {
        $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $name -Confirm:$false
            Write-ShirosLog INFO ('Removed scheduled task {0}{1}' -f $TaskPath, $name)
        }
    }
    Write-Host 'Scheduled tasks removed. The runtime itself keeps running until stopped.'
    return 0
}

function Invoke-ShirosStoreKey {
    param([switch]$Gui)
    if ($Gui) {
        $credential = Get-Credential -UserName $CredentialUser -Message 'ShirOS tunnel runtime API key. Stored in Windows Credential Manager; never written to files or logs.'
        if ($null -eq $credential) { Write-Host 'Cancelled; no change.'; return 1 }
        $plain = $credential.GetNetworkCredential().Password
        $plain = $plain.Trim()
    } else {
        $secure = Read-Host -Prompt 'ShirOS tunnel runtime API key (hidden input, not saved to files)' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $plain = $plain.Trim()
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secure.Dispose()
        }
    }
    if ([string]::IsNullOrWhiteSpace($plain)) {
        Write-Host 'Empty key; no change.'
        return 1
    }
    [ShirosCredentialStore]::Write($CredentialTarget, $CredentialUser, $plain)
    $plain = $null
    [GC]::Collect()
    Initialize-RuntimeDir
    Write-ShirosLog INFO 'Tunnel runtime API key stored in Windows Credential Manager'
    Write-Host ('Key stored in Windows Credential Manager (target: {0}, current user, DPAPI-protected).' -f $CredentialTarget)
    return 0
}

function Invoke-ShirosImportKey {
    param([string]$Path)
    if (-not $Path) { Write-Host 'Provide -KeyFile <path>.'; return 1 }
    if (-not (Test-Path -LiteralPath $Path)) { Write-Host 'Key file not found.'; return 1 }
    $content = Get-Content -LiteralPath $Path -Raw
    $match = [regex]::Match($content, 'sk-[A-Za-z0-9_-]{12,}')
    if (-not $match.Success) {
        Write-Host 'No runtime API key pattern (sk-...) found in the file.'
        return 1
    }
    [ShirosCredentialStore]::Write($CredentialTarget, $CredentialUser, $match.Value)
    $content = $null
    Initialize-RuntimeDir
    Write-ShirosLog INFO 'Tunnel runtime API key imported from a local file into Credential Manager'
    Write-Host 'Key imported into Windows Credential Manager (value not printed).'
    Write-Host ('After verifying with verify-key/doctor, delete the plaintext file: {0}' -f $Path)
    return 0
}

function Invoke-ShirosKeyInfo {
    $key = Read-ShirosTunnelKey
    $length = $key.Length
    $validShape = [bool]([regex]::IsMatch($key, '^sk-[A-Za-z0-9_-]{12,}$'))
    $hasWhitespace = [bool]([regex]::IsMatch($key, '\s'))
    Write-Host ('Stored key length: {0}' -f $length)
    Write-Host ('Matches sk-[A-Za-z0-9_-]{{12,}}: {0}' -f $validShape)
    Write-Host ('Contains whitespace: {0}' -f $hasWhitespace)
    $key = $null
    if ($validShape) { return 0 }
    return 1
}

function Invoke-ShirosVerifyKey {
    $config = Get-ShirosTunnelConfig
    $proxy = Get-ShirosProxyState
    $key = Read-ShirosTunnelKey
    try {
        $request = [System.Net.HttpWebRequest]::Create(('https://api.openai.com/v1/tunnels/{0}' -f $config.TunnelId))
        $request.Method = 'GET'
        $request.Timeout = 10000
        $request.ReadWriteTimeout = 10000
        $request.UserAgent = 'ShirOS-Runtime-Monitor/1.0'
        $request.Headers['Authorization'] = 'Bearer ' + $key
        if ($proxy.Url) { $request.Proxy = New-Object System.Net.WebProxy($proxy.Url, $false) }
        $response = $request.GetResponse()
        $status = [int]$response.StatusCode
        $response.Close()
        Initialize-RuntimeDir
        Write-ShirosLog INFO ('verify-key: control plane accepted the stored key (HTTP {0})' -f $status)
        Write-Host ('Stored key accepted by the control plane (HTTP {0}) for tunnel {1}.' -f $status, $config.TunnelId)
        return 0
    } catch [System.Net.WebException] {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        $message = Remove-SensitiveText $_.Exception.Message
        Initialize-RuntimeDir
        Write-ShirosLog WARN ('verify-key failed: HTTP {0} {1}' -f $status, $message)
        Write-Host ('Stored key check failed (HTTP {0}): {1}' -f $status, $message)
        return 1
    } finally {
        $key = $null
    }
}

function Invoke-ShirosDoctor {
    $config = Get-ShirosTunnelConfig
    $script:DoctorOutput = ''
    $script:DoctorExit = 1
    Invoke-WithShirosTunnelEnvironment {
        $capture = Invoke-NativeCapture -FilePath $TunnelClient -Arguments @(
            'doctor', '--profile', $config.ProfileName, '--profile-dir', $config.ProfileDir, '--explain'
        )
        $script:DoctorOutput = $capture.Output
        $script:DoctorExit = $capture.ExitCode
    }
    $text = Remove-SensitiveText $script:DoctorOutput
    Initialize-RuntimeDir
    $text | Set-Content -LiteralPath (Join-Path $RuntimeDir 'doctor-output.txt') -Encoding UTF8
    Write-Host $text
    Write-ShirosLog INFO ('doctor exit code {0}' -f $script:DoctorExit)
    return $script:DoctorExit
}

function Invoke-ShirosConnectVerbose {
    Invoke-WithShirosTunnelEnvironment {
        $argumentLine = Get-ShirosConnectArgumentLine
        Write-Host ('command: runtimes connect --alias {0} --profile {1} (mcp command hidden)' -f $TunnelAlias, (Get-ShirosTunnelConfig).ProfileName)
        $capture = Invoke-TunnelClientRaw -ArgumentLine $argumentLine
        Write-Host $capture.Output
        Write-Host ('connect exit code: {0}' -f $capture.ExitCode)
    }
    return 0
}

function Invoke-ShirosClearKey {
    [ShirosCredentialStore]::Delete($CredentialTarget)
    Initialize-RuntimeDir
    Write-ShirosLog INFO 'Stored tunnel runtime API key removed'
    Write-Host 'Stored key removed. Automated tunnel start will fail until a new key is stored.'
    return 0
}

function Invoke-ShirosLogs {
    foreach ($file in @($LogFile, $IncidentFile)) {
        Write-Host ('===== {0} =====' -f $file)
        if (Test-Path -LiteralPath $file) {
            Get-Content -LiteralPath $file -Tail 40 | ForEach-Object { Write-Host $_ }
        } else {
            Write-Host '(no entries yet)'
        }
    }
    return 0
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
function Invoke-ShirosDispatch {
    $script:DispatchExitCode = 0
    switch ($Action) {
        'start' { $script:DispatchExitCode = Invoke-ShirosStart }
        'stop' { $script:DispatchExitCode = Invoke-ShirosStop }
        'restart' {
            Invoke-ShirosStop | Out-Null
            $script:DispatchExitCode = Invoke-ShirosStart
        }
        'refresh-mcp' {
            Write-ShirosLog INFO 'Refreshing MCP runtime; retaining database and workbench'
            Stop-ShirosTunnel
            if (Start-ShirosTunnel -Reason 'refresh-mcp') {
                Resolve-ComponentFailure -Component 'tunnel' -Detail 'MCP runtime refreshed'
                $snapshot = Get-ShirosSnapshot
                Save-ShirosSnapshot $snapshot
                Show-ShirosSnapshot $snapshot
            } else {
                $script:DispatchExitCode = 1
            }
        }
        'status' {
            $snapshot = Get-ShirosSnapshot
            Save-ShirosSnapshot $snapshot
            if ($Json) { $snapshot | ConvertTo-Json -Depth 8 } else { Show-ShirosSnapshot $snapshot }
            if ($snapshot.overall -eq 'healthy' -or $snapshot.overall -eq 'stopped') {
                $script:DispatchExitCode = 0
            } else {
                $script:DispatchExitCode = 1
            }
        }
        'check' {
            Invoke-ShirosCheck
            $script:DispatchExitCode = $script:CheckExitCode
        }
        'install' { $script:DispatchExitCode = Invoke-ShirosInstall }
        'uninstall' { $script:DispatchExitCode = Invoke-ShirosUninstall }
        'store-key' { $script:DispatchExitCode = Invoke-ShirosStoreKey }
        'store-key-gui' { $script:DispatchExitCode = Invoke-ShirosStoreKey -Gui }
        'import-key' { $script:DispatchExitCode = Invoke-ShirosImportKey -Path $KeyFile }
        'key-info' { $script:DispatchExitCode = Invoke-ShirosKeyInfo }
        'verify-key' { $script:DispatchExitCode = Invoke-ShirosVerifyKey }
        'doctor' { $script:DispatchExitCode = Invoke-ShirosDoctor }
        'connect-verbose' { $script:DispatchExitCode = Invoke-ShirosConnectVerbose }
        'clear-key' { $script:DispatchExitCode = Invoke-ShirosClearKey }
        'test-key' {
            if ([ShirosCredentialStore]::Exists($CredentialTarget)) {
                Write-Host 'A tunnel runtime API key is stored for this Windows user.'
                $script:DispatchExitCode = 0
            } else {
                Write-Host 'No tunnel runtime API key is stored. Run: ShirOS.cmd store-key'
                $script:DispatchExitCode = 1
            }
        }
        'logs' { $script:DispatchExitCode = Invoke-ShirosLogs }
    }
}

Initialize-RuntimeDir
$script:DispatchExitCode = 0
$mutatingActions = @('start', 'stop', 'restart', 'refresh-mcp', 'check')
if ($mutatingActions -contains $Action) {
    # One operator at a time: logon start, the 5-minute monitor and manual
    # commands must not fight over the same database or tunnel runtime.
    $controlMutex = New-Object System.Threading.Mutex($false, 'Local\ShirosRuntimeControl')
    $acquired = $false
    $waitMilliseconds = if ($Action -eq 'check') { 150000 } else { 240000 }
    try {
        $acquired = $controlMutex.WaitOne($waitMilliseconds)
        if ($acquired) {
            Invoke-ShirosDispatch
        } else {
            Write-ShirosLog WARN ('Another ShirOS runtime operation is still in progress; {0} skipped' -f $Action)
            if ($Action -ne 'check') { $script:DispatchExitCode = 1 }
        }
    } finally {
        if ($acquired) { $controlMutex.ReleaseMutex() }
        $controlMutex.Dispose()
    }
} else {
    Invoke-ShirosDispatch
}
exit $script:DispatchExitCode
