# Endpoint admin

**Covers** — who's logged on, profiles, disks, printers, services,
messaging a user, silent installs, and the WMI-to-CIM conversion you need
before any of it runs on PowerShell 7.

**Reach for it when** — you're working a ticket on someone else's machine
and want the answer without a remote session, or you're building a
report across a list of hosts.

**Verified** — every block parses under PowerShell 7.4.6. These are
Windows-target commands; the parser can't tell you WinRM was refused.

---

## Get-WmiObject is gone

`Get-WmiObject`, `Get-WmiObject -Class`, `Invoke-WmiMethod` and the rest
of the `*-Wmi*` family were removed in PowerShell 6 and do not exist in
7. They still work in Windows PowerShell 5.1, which is why old scripts
run on the console you've always used and fail the moment they're run
under `pwsh`.

| Old | New |
|---|---|
| `Get-WmiObject -Class X` | `Get-CimInstance -ClassName X` |
| `Get-WmiObject -Class X -ComputerName Y` | `Get-CimInstance -ClassName X -ComputerName Y` |
| `Get-WmiObject -Query "..."` | `Get-CimInstance -Query "..."` |
| `Get-WmiObject -Filter "..."` | `Get-CimInstance -Filter "..."` |
| `Invoke-WmiMethod -Path X -Name Y` | `Invoke-CimMethod -ClassName X -MethodName Y` |
| `Get-WmiObject -List` | `Get-CimClass` |
| `$obj.Delete()` | `$obj \| Remove-CimInstance` |
| DCOM by default | WinRM by default (`New-CimSessionOption -Protocol Dcom` to force DCOM) |

The class names and properties are identical. It is a mechanical
translation, not a rewrite — the one real change is that CIM instances
are inert data, so you call methods through `Invoke-CimMethod` rather
than on the object.

---

## Who is logged on

```powershell
Get-CimInstance -ClassName Win32_ComputerSystem |
    Select-Object Name, Domain, UserName
```

Across a list, wrapped so one unreachable host doesn't stop the run:

```powershell
function Get-LoggedOnUser {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromPipeline)]
        [ValidateNotNullOrEmpty()]
        [string[]] $ComputerName = $env:COMPUTERNAME
    )
    process {
        foreach ($computer in $ComputerName) {
            try {
                $cs = Get-CimInstance -ClassName Win32_ComputerSystem `
                    -ComputerName $computer -ErrorAction Stop
                [PSCustomObject]@{
                    ComputerName = $computer
                    UserName     = $cs.UserName
                    Reachable    = $true
                }
            }
            catch {
                [PSCustomObject]@{
                    ComputerName = $computer
                    UserName     = $null
                    Reachable    = $false
                }
            }
        }
    }
}
```

The old version of this used `[ValidateScript({ Test-Connection ... })]`
on the parameter. Don't: a validation attribute that does network I/O
makes the function slow, untestable offline, and it throws a validation
error rather than reporting the host as unreachable. Test inside the
function and return a row either way, so the caller can see the gaps.

`Win32_ComputerSystem.UserName` shows the *console* user, and is null if
nobody is logged on locally. It does not show RDP sessions — for those
you want `quser` or the `Win32_LogonSession` class.

---

## User profiles

```powershell
Get-CimInstance -ClassName Win32_UserProfile |
    Where-Object { -not $_.Special } |
    Select-Object LocalPath, LastUseTime, Loaded,
        @{ Name = 'SizeGB'; Expression = { $null } } |
    Sort-Object LastUseTime
```

`-not $_.Special` drops the system profiles, which are otherwise the
majority of what comes back.

Resolving the SID to a name, since `LocalPath` is only a folder name:

```powershell
Get-CimInstance -ClassName Win32_UserProfile |
    Where-Object { -not $_.Special } |
    ForEach-Object {
        $account = try {
            (New-Object System.Security.Principal.SecurityIdentifier($_.SID)).
                Translate([System.Security.Principal.NTAccount]).Value
        } catch { '(unresolved)' }

        [PSCustomObject]@{
            Account     = $account
            LocalPath   = $_.LocalPath
            LastUseTime = $_.LastUseTime
        }
    } | Sort-Object LastUseTime
```

`(unresolved)` is the normal result for a deleted account, which is
usually the profile you were looking for.

---

## Disk space

```powershell
Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType = 3' |
    Select-Object DeviceID, VolumeName,
        @{ Name = 'SizeGB';   Expression = { [math]::Round($_.Size / 1GB, 2) } },
        @{ Name = 'FreeGB';   Expression = { [math]::Round($_.FreeSpace / 1GB, 2) } },
        @{ Name = 'FreePct';  Expression = {
            if ($_.Size -gt 0) { [math]::Round($_.FreeSpace / $_.Size * 100, 1) } else { $null } } }
```

`DriveType = 3` is fixed local disks. The `if ($_.Size -gt 0)` guard
matters: a volume can report zero size (an empty card reader, a
dismounted mount point) and the division throws mid-report.

`1GB` is a PowerShell literal for 1073741824. Use it rather than the
number — it reads better and can't be mistyped.

Across servers, with a threshold:

```powershell
$servers  = 'HOST01', 'HOST02'
$warnPct  = 20

$report = foreach ($server in $servers) {
    try {
        Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType = 3' `
            -ComputerName $server -ErrorAction Stop |
            ForEach-Object {
                $pct = if ($_.Size -gt 0) { $_.FreeSpace / $_.Size * 100 } else { 0 }
                [PSCustomObject]@{
                    Server  = $server
                    Drive   = $_.DeviceID
                    FreeGB  = [math]::Round($_.FreeSpace / 1GB, 2)
                    FreePct = [math]::Round($pct, 1)
                    State   = if ($pct -lt 5) { 'CRITICAL' }
                              elseif ($pct -lt $warnPct) { 'WARNING' }
                              else { 'OK' }
                }
            }
    }
    catch {
        [PSCustomObject]@{
            Server = $server; Drive = $null; FreeGB = $null
            FreePct = $null; State = 'UNREACHABLE'
        }
    }
}

$report | Sort-Object FreePct | Format-Table -AutoSize
```

For an HTML version, `ConvertTo-Html` does in one line what a page of
`Add-Content` string-building used to:

```powershell
$style = '<style>body{font-family:Segoe UI,Tahoma;font-size:12px}' +
         'table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px}</style>'

$report | ConvertTo-Html -Head $style -Title 'Disk space' -PreContent (
    '<h2>Disk space {0:yyyy-MM-dd}</h2>' -f (Get-Date)
) | Out-File -FilePath (Join-Path $env:USERPROFILE 'Desktop\diskspace.html') -Encoding utf8
```

Colouring rows by threshold means post-processing the HTML string, which
is where the old hand-built version earned its length. `ConvertTo-Html`
plus a CSS class per state is the tidier route if you need it.

---

## Printers

```powershell
Get-Printer | Select-Object Name, DriverName, PortName, Shared, Published
```

`Get-Printer` and `Remove-Printer` come from the `PrintManagement`
module and are much safer than the WMI equivalents.

Removing network printers pointing at one server — the operation worth
being careful with:

```powershell
$server = 'PRINTSRV01'

# Look first. Always.
$targets = Get-Printer | Where-Object { $_.ComputerName -eq $server -or $_.PortName -like "*$server*" }
$targets | Select-Object Name, ComputerName, PortName

# Then, once the list is right:
$targets | Remove-Printer -WhatIf
```

Three habits in there:

- Select the set into a variable and print it before acting on it.
- `-WhatIf` on the destructive call. Drop it only once the output reads
  correctly.
- `-and` between two comparisons, never `-and { ... }`. A scriptblock in
  a comparison is always truthy, so `$_.A -eq 'x' -and {$_.B -eq 'y'}`
  matches *everything* — a filter that looks careful and isn't.

---

## Services

```powershell
Get-Service -Name 'Spooler' | Select-Object Name, Status, StartType

Restart-Service -Name 'Spooler' -Force

Get-Service | Where-Object { $_.StartType -eq 'Automatic' -and $_.Status -ne 'Running' } |
    Select-Object Name, DisplayName, Status
```

That last one is the "what should be running and isn't" check.

Clearing the font cache, which needs the service down first:

```powershell
$cache = Join-Path $env:SystemRoot 'ServiceProfiles\LocalService\AppData\Local\FontCache'

Stop-Service -Name 'FontCache' -Force
Get-ChildItem -Path $cache -Filter '*.dat' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path (Split-Path $cache) -Filter 'FontCache*.dat' -ErrorAction SilentlyContinue |
    Remove-Item -Force
Start-Service -Name 'FontCache'
```

Target `FontCache*.dat` specifically. The version of this that deletes
`AppData\Local\*.*` empties a directory that holds more than the font
cache.

---

## Messaging a logged-on user

```powershell
$computer = Read-Host 'Computer name'
$message  = Read-Host 'Message'

Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -ComputerName $computer `
    -Arguments @{ CommandLine = "msg * $message" }
```

That's the CIM form of the old `Invoke-WmiMethod -Path Win32_Process`.
Note `-Arguments` takes a hashtable, where the WMI version took a
positional array — the commonest thing to get wrong in the conversion.

---

## Silent installs

```powershell
$installer = Join-Path $env:TEMP 'setup.exe'

$proc = Start-Process -FilePath $installer -ArgumentList '/S' -Wait -PassThru
"Exit code: $($proc.ExitCode)"
```

`-Wait -PassThru` is the pair that makes this scriptable: without `-Wait`
the next line runs while the installer is still going, and without
`-PassThru` you never see whether it worked.

MSI is more predictable than a vendor `.exe`:

```powershell
$msi = Join-Path $env:TEMP 'package.msi'
$log = Join-Path $env:TEMP 'package-install.log'

$args = @('/i', "`"$msi`"", '/qn', '/norestart', '/l*v', "`"$log`"")
$proc = Start-Process -FilePath 'msiexec.exe' -ArgumentList $args -Wait -PassThru

switch ($proc.ExitCode) {
    0     { 'Installed' }
    3010  { 'Installed -- reboot required' }
    1618  { 'Another install is in progress' }
    1603  { "Failed -- see $log" }
    default { "Exit code $($proc.ExitCode)" }
}
```

The silent switch varies by vendor: `/S`, `/s`, `/silent`, `/quiet`,
`/qn`, `-ms`. Check the vendor's documentation rather than guessing —
the wrong switch usually means a GUI opened on someone's machine.

---

## Gotchas

**Never put a password on a command line.** `psexec -u <user> -p <password>`
lands in the process list, your shell history, and the target's event
log. Use `Get-Credential` and pass the credential object:

```powershell
$cred = Get-Credential
Invoke-Command -ComputerName 'HOST01' -Credential $cred -ScriptBlock {
    Get-CimInstance -ClassName Win32_ComputerSystem | Select-Object Name, UserName
}
```

**BitLocker status has a real cmdlet.** `manage-bde -computername X
-status` still works, but returns text:

```powershell
Invoke-Command -ComputerName 'HOST01' -ScriptBlock {
    Get-BitLockerVolume | Select-Object MountPoint, VolumeStatus, ProtectionStatus, EncryptionPercentage
}
```

**`-ComputerName` on `Get-CimInstance` builds a session per call.** For
several queries against the same host, make one `New-CimSession` and
reuse it; it's markedly faster and only authenticates once.
