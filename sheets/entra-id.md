# Entra ID (Microsoft Graph)

**Covers** — connecting with the right scopes, users, groups, registered
devices, bulk lookups from a CSV, and translating old `AzureAD` commands
into Graph ones.

**Reach for it when** — you need a membership or device export out of the
cloud directory, or you're staring at an old script that no longer runs.

**Verified** — every block parses under PowerShell 7.4.6. The parser
cannot tell you a scope was refused or a permission is missing.

---

## The module situation

The `AzureAD` and `MSOnline` modules are retired. Anything built on
`Connect-AzureAD` / `Get-AzureADUser` / `Connect-MsolService` is on
borrowed time or already dead. `Microsoft.Graph` is the replacement.

```powershell
Install-Module Microsoft.Graph -Scope CurrentUser
```

The full module is large and slow to load. If you only touch users and
groups, install the sub-modules instead:

```powershell
Install-Module Microsoft.Graph.Users, Microsoft.Graph.Groups,
    Microsoft.Graph.Identity.DirectoryManagement -Scope CurrentUser
```

### Translation table

| Old (`AzureAD`) | Graph |
|---|---|
| `Connect-AzureAD` | `Connect-MgGraph -Scopes ...` |
| `Disconnect-AzureAD` | `Disconnect-MgGraph` |
| `Get-AzureADUser -ObjectId x` | `Get-MgUser -UserId x` |
| `Get-AzureADUser -All $true` | `Get-MgUser -All` |
| `Get-AzureADUser -Filter "..."` | `Get-MgUser -Filter "..."` (OData, different syntax) |
| `Get-AzureADGroup` | `Get-MgGroup` |
| `Get-AzureADGroupMember` | `Get-MgGroupMember` |
| `Get-AzureADUserRegisteredDevice` | `Get-MgUserRegisteredDevice` |
| `Get-AzureADDevice` | `Get-MgDevice` |
| `Get-AzureADTenantDetail` | `Get-MgOrganization` |
| `$user.ObjectId` | `$user.Id` |
| `$user.PhysicalDeliveryOfficeName` | `$user.OfficeLocation` |

The property renames catch people out more than the cmdlet renames do —
a script that ran clean will now quietly fill a column with nulls.

---

## Connecting

Ask for the least you need. Every scope is consent the tenant has to
grant:

```powershell
Connect-MgGraph -Scopes 'User.Read.All', 'Group.Read.All'
```

Common ones:

| Scope | For |
|---|---|
| `User.Read.All` | reading user objects |
| `Group.Read.All` | groups and membership |
| `Device.Read.All` | device objects |
| `Directory.Read.All` | broad read, when the above won't do |
| `AuditLog.Read.All` | sign-in activity — slow, only add when needed |

Check what you're connected as:

```powershell
$ctx = Get-MgContext
$ctx | Select-Object Account, TenantId, Scopes
```

Always disconnect at the end of a script, in a `finally` so it runs even
when something throws:

```powershell
try {
    Connect-MgGraph -Scopes 'User.Read.All'
    # work
}
finally {
    Disconnect-MgGraph
}
```

---

## Users

```powershell
Get-MgUser -UserId 'jbloggs@example.com' |
    Select-Object DisplayName, UserPrincipalName, Id, AccountEnabled
```

Only the default properties come back. Anything else has to be asked for:

```powershell
Get-MgUser -UserId 'jbloggs@example.com' `
    -Property Id, DisplayName, Department, JobTitle, OfficeLocation, UsageLocation |
    Select-Object DisplayName, Department, JobTitle, OfficeLocation
```

`-Property` controls what Graph sends; `Select-Object` controls what you
see. You need both — `Select-Object Department` on an object that was
never asked for `Department` shows a blank, not an error.

All users, filtered server-side:

```powershell
Get-MgUser -All -Filter "accountEnabled eq true and usageLocation eq 'GB'" `
    -Property Id, DisplayName, UserPrincipalName, UsageLocation |
    Select-Object DisplayName, UserPrincipalName, UsageLocation
```

The `-Filter` here is OData, not the AD module's syntax and not
PowerShell. Property names are camelCase, strings are single-quoted,
operators are `eq`, `ne`, `startswith()`.

Some filters need an extra header, and Graph tells you so with a
"Request_UnsupportedQuery" error rather than anything readable:

```powershell
Get-MgUser -All -Filter "endsWith(mail,'@example.com')" `
    -ConsistencyLevel eventual -CountVariable total
"$total users"
```

---

## Groups and membership

```powershell
$group = Get-MgGroup -Filter "displayName eq 'GroupName'" -Property Id, DisplayName, Mail

if (-not $group) {
    throw "Group not found"
}
if (@($group).Count -gt 1) {
    throw "Group name is not unique -- use the Id"
}
```

Display names are not unique in Entra. Checking for more than one match
is the difference between a report and a wrong report.

```powershell
$members = Get-MgGroupMember -GroupId $group.Id -All
"{0} members" -f @($members).Count
```

`Get-MgGroupMember` returns directory objects, not users, and the useful
fields sit in `AdditionalProperties` rather than as real properties:

```powershell
$members | ForEach-Object {
    [PSCustomObject]@{
        DisplayName = $_.AdditionalProperties['displayName']
        UPN         = $_.AdditionalProperties['userPrincipalName']
        Type        = $_.AdditionalProperties['@odata.type']
    }
}
```

That avoids a `Get-MgUser` call per member. On a group of 500 that is one
request instead of 501 — the single biggest speed-up available in this
module. Only fall back to per-member lookups for properties the group
call genuinely does not return:

```powershell
$group   = Get-MgGroup -Filter "displayName eq 'GroupName'" -Property Id, DisplayName
$members = Get-MgGroupMember -GroupId $group.Id -All
$rows    = [System.Collections.Generic.List[object]]::new()
$i       = 0

foreach ($m in $members) {
    $i++
    Write-Progress -Activity 'Members' -Status "$i of $(@($members).Count)" `
        -PercentComplete ($i / [Math]::Max(@($members).Count, 1) * 100)

    $u = Get-MgUser -UserId $m.Id -Property Id, DisplayName, Mail, UserPrincipalName,
        UserType, AccountEnabled, CreatedDateTime -ErrorAction SilentlyContinue
    if (-not $u) { continue }

    $rows.Add([PSCustomObject]@{
        GroupName   = $group.DisplayName
        DisplayName = $u.DisplayName
        UPN         = $u.UserPrincipalName
        UserType    = $u.UserType
        Status      = if ($u.AccountEnabled) { 'Enabled' } else { 'Disabled' }
        Created     = if ($u.CreatedDateTime) { $u.CreatedDateTime.ToString('yyyy-MM-dd') } else { '' }
    })
}
Write-Progress -Activity 'Members' -Completed
```

Two habits in there worth carrying:

- `[Math]::Max(..., 1)` — `Write-Progress` divides by the count, and an
  empty group otherwise throws a divide-by-zero on the first iteration.
- `List[object]` with `.Add()` instead of `$rows += $row`. `+=` rebuilds
  the whole array every time round the loop; on a few thousand members
  that is the difference between seconds and minutes.

---

## Devices

```powershell
$user    = Get-MgUser -UserId 'jbloggs@example.com' -Property Id, DisplayName, UserPrincipalName
$devices = Get-MgUserRegisteredDevice -UserId $user.Id -All

$devices | ForEach-Object {
    [PSCustomObject]@{
        User       = $user.DisplayName
        UPN        = $user.UserPrincipalName
        Device     = $_.AdditionalProperties['displayName']
        OS         = $_.AdditionalProperties['operatingSystem']
        LastSignIn = $_.AdditionalProperties['approximateLastSignInDateTime']
    }
}
```

"Primary device" is not a thing Entra records. The usual stand-in is most
recently signed in:

```powershell
$primary = $devices |
    Sort-Object { $_.AdditionalProperties['approximateLastSignInDateTime'] } -Descending |
    Select-Object -First 1
```

Worth saying so in the report rather than labelling the column "Primary"
and letting someone believe it.

---

## Bulk lookup from a CSV

The pattern for "here is a list of addresses, tell me about them". Record
the misses rather than letting them vanish:

```powershell
$rows = [System.Collections.Generic.List[object]]::new()

Import-Csv -Path (Join-Path $env:USERPROFILE 'Downloads\input.csv') -Encoding UTF8 |
    ForEach-Object {
        $upn = $_.userPrincipalName
        try {
            $u = Get-MgUser -UserId $upn -Property Id, DisplayName, UserPrincipalName -ErrorAction Stop
            $rows.Add([PSCustomObject]@{
                UserPrincipalName = $upn
                Id                = $u.Id
                DisplayName       = $u.DisplayName
                Result            = 'Found'
            })
        }
        catch {
            $rows.Add([PSCustomObject]@{
                UserPrincipalName = $upn
                Id                = $null
                DisplayName       = $null
                Result            = 'Not found'
            })
        }
    }

$rows | Group-Object Result | Select-Object Name, Count
$rows | Export-Csv -Path (Join-Path $env:USERPROFILE 'Downloads\results.csv') `
    -NoTypeInformation -Encoding UTF8
```

`-Encoding UTF8` on the way in as well as out. A name with an accent that
survives the lookup and then comes out mangled is a decoding problem at
`Import-Csv`, not at `Export-Csv`.

---

## Gotchas

**`Set-ExecutionPolicy RemoteSigned` does not belong in a script.** It
needs elevation and changes machine state to fix a problem the script
cannot detect. Set it once on the host, or run the file with
`pwsh -ExecutionPolicy Bypass -File script.ps1`.

**`-All` is not the default.** Without it you get the first page, which
is usually 100 objects. A report that quietly stops at 100 looks exactly
like a correct report.

**Throttling.** Graph returns HTTP 429 with a `Retry-After` header on
large loops. `Get-Mg*` cmdlets retry on their own, but if you are calling
`Invoke-MgGraphRequest` directly you have to honour it yourself.
