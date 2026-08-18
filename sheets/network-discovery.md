# Network discovery

**Covers** — reachability, sweeping a range, name and MAC resolution,
ports, and reading those things off a remote machine.

**Reach for it when** — you're auditing what's live on a subnet, chasing
a device you only have one identifier for, or proving whether a thing is
a network problem before anyone blames the network.

**Verified** — every block parses under PowerShell 7.4.6. Addresses use
`192.0.2.0/24`, which is TEST-NET-1 and reserved for documentation:
substitute your own range, and don't paste a real one back into a repo.

---

## Test-Connection changed in PowerShell 7

This is the single biggest gotcha in this sheet, because the same command
means different things on 5.1 and 7:

| | Windows PowerShell 5.1 | PowerShell 7 |
|---|---|---|
| Target | `-ComputerName` | `-TargetName` (`-ComputerName` still aliased) |
| Just true/false | `-Quiet` | `-Quiet` |
| Timeout | none — hangs on dead hosts | `-TimeoutSeconds` |
| Returns | WMI objects, `.Address` | `TestConnectionCommand+PingStatus`, `.Address` |
| Port test | not supported | `-TcpPort` |
| Traceroute | not supported | `-Traceroute` |

A 5.1 sweep with no timeout blocks for roughly 4 seconds per dead host.
On a /24 that's most of twenty minutes spent waiting for nothing.

```powershell
Test-Connection -TargetName '192.0.2.10' -Count 1 -Quiet -TimeoutSeconds 1
```

---

## Sweeping a range

```powershell
$prefix = '192.0.2'

1..254 | ForEach-Object {
    $ip = "$prefix.$_"
    if (Test-Connection -TargetName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        [PSCustomObject]@{ IPAddress = $ip; Status = 'Up' }
    }
}
```

Serially that is still ~254 seconds of worst case. `ForEach-Object
-Parallel` (PowerShell 7 only) turns it into seconds:

```powershell
$prefix = '192.0.2'

$live = 1..254 | ForEach-Object -ThrottleLimit 32 -Parallel {
    $ip = "$using:prefix.$_"
    if (Test-Connection -TargetName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        [PSCustomObject]@{ IPAddress = $ip; Status = 'Up' }
    }
}

$live | Sort-Object { [version]$_.IPAddress }
```

Two things that catch people:

- Variables from outside the loop need `$using:`. Without it `$prefix` is
  simply empty inside the parallel block, and every test silently fails.
- `Sort-Object` on an IP string sorts `.10` before `.9`. Casting to
  `[version]` sorts it numerically, which is a cheap trick that works
  because a dotted quad is a valid version string.

Resolve names as you go, so the output is usable by a human:

```powershell
$prefix = '192.0.2'

1..254 | ForEach-Object -ThrottleLimit 32 -Parallel {
    $ip = "$using:prefix.$_"
    if (Test-Connection -TargetName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        $name = try {
            (Resolve-DnsName -Name $ip -Type PTR -ErrorAction Stop).NameHost
        } catch { $null }

        [PSCustomObject]@{ IPAddress = $ip; Hostname = $name }
    }
}
```

A silent host is not necessarily a dead host — ICMP is blocked by default
on a stock Windows firewall profile. Absence of a ping reply is evidence
of nothing much on its own.

---

## Name resolution

```powershell
Resolve-DnsName -Name 'host.example.com'
Resolve-DnsName -Name '192.0.2.10' -Type PTR
```

Ask a specific server when you suspect the resolver rather than the
record, and skip the cache when you're chasing a change that should have
propagated:

```powershell
Resolve-DnsName -Name 'host.example.com' -Server '192.0.2.53' -DnsOnly -Type A
```

`Resolve-DnsName` comes from the `DnsClient` module and is Windows-only.
The cross-platform equivalent, useful from a Linux box:

```powershell
[System.Net.Dns]::GetHostEntry('example.com') | Select-Object HostName, AddressList
```

---

## Ports

Reachability on ICMP and reachability on the port you actually care about
are different questions:

```powershell
Test-Connection -TargetName 'host.example.com' -TcpPort 443 -TimeoutSeconds 2
```

`Test-NetConnection` is the 5.1-era version and still the more informative
one, since it reports the route and the interface too:

```powershell
Test-NetConnection -ComputerName 'host.example.com' -Port 443 -InformationLevel Detailed
```

Several ports at once:

```powershell
$target = 'host.example.com'

80, 443, 445, 3389, 5985 | ForEach-Object {
    [PSCustomObject]@{
        Port = $_
        Open = Test-Connection -TargetName $target -TcpPort $_ -TimeoutSeconds 2
    }
}
```

---

## MAC addresses and the local ARP table

Locally:

```powershell
Get-NetNeighbor -AddressFamily IPv4 |
    Where-Object { $_.State -in 'Reachable', 'Stale' } |
    Select-Object IPAddress, LinkLayerAddress, State |
    Sort-Object { [version]$_.IPAddress }
```

ARP only sees your own broadcast domain, so this is a same-subnet answer
only. Anything beyond the router shows the router's MAC.

Off a remote host, over WinRM:

```powershell
$live = '192.0.2.10', '192.0.2.11'

Get-CimInstance -ComputerName $live -ClassName Win32_NetworkAdapterConfiguration `
        -Filter 'IPEnabled = True' |
    Select-Object PSComputerName, DNSHostName, IPAddress, MACAddress
```

That is the modern form of the old `Get-WmiObject
win32_networkadapterconfiguration` one-liner. `Get-CimInstance` uses
WinRM (5985/5986) rather than DCOM; `Get-WmiObject` does not exist in
PowerShell 7 at all. If you're stuck against a host that only speaks
DCOM:

```powershell
$opt     = New-CimSessionOption -Protocol Dcom
$session = New-CimSession -ComputerName '192.0.2.10' -SessionOption $opt

Get-CimInstance -CimSession $session -ClassName Win32_ComputerSystem |
    Select-Object Name, Domain, UserName

Remove-CimSession -CimSession $session
```

---

## Timestamped continuous ping

For handing to someone who says "it drops out sometimes". The point is
the timestamp — a log without one proves nothing:

```powershell
$target = '192.0.2.10'
$log    = Join-Path $env:USERPROFILE "Desktop\ping-$target.log"

while ($true) {
    $ok = Test-Connection -TargetName $target -Count 1 -Quiet -TimeoutSeconds 1
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}  {2}' -f (Get-Date), $target,
        $(if ($ok) { 'reply' } else { 'NO REPLY' })
    $line | Tee-Object -FilePath $log -Append
    Start-Sleep -Seconds 1
}
```

`Tee-Object` writes to the file and the console at once, so you can watch
it and still have the evidence afterwards. Ctrl-C to stop.

---

## Exporting a sweep

```powershell
$results = 1..20 | ForEach-Object {
    $ip = "192.0.2.$_"
    [PSCustomObject]@{
        IPAddress = $ip
        Up        = Test-Connection -TargetName $ip -Count 1 -Quiet -TimeoutSeconds 1
        Checked   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    }
}

$results | Export-Csv -Path (Join-Path $env:USERPROFILE 'Desktop\sweep.csv') `
    -NoTypeInformation -Encoding UTF8

$results | Group-Object Up | Select-Object Name, Count
```

---

## Gotchas

**`ping.exe` returns text, not objects.** Piping it into
`ForEach-Object` gives you strings to regex. `Test-Connection` gives you
properties. Only reach for `ping.exe` when you need something the cmdlet
won't do, such as `-f` to set the don't-fragment bit for MTU testing.

**A sweep needs a stated timestamp and range.** "Everything on the
subnet was up" is worthless without when, and from where.

**Remote CIM needs WinRM enabled and the firewall open.** `Enable-PSRemoting`
on the target, or it fails with an RPC error that reads like a network
fault and isn't one.
