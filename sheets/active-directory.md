# Active Directory

**Covers** — finding users and groups, walking OUs, working out who is in
what, and getting it into a CSV.

**Reach for it when** — someone asks "who has access to X", you need a
membership list for an audit, or you're checking an OU before a change.

**Verified** — every block parses under PowerShell 7.4.6. The `AD`
cmdlets ship in RSAT and need a domain-joined host or `-Server`; the
parser cannot check that for you.

```powershell
Import-Module ActiveDirectory
```

---

## Find one user

```powershell
Get-ADUser -Identity 'jbloggs' -Properties *
```

`-Properties *` is for exploring at the prompt. In a script, name what
you want — `*` pulls every attribute and is slow on a large directory.

```powershell
Get-ADUser -Identity 'jbloggs' -Properties Department, Manager, LastLogonDate |
    Select-Object Name, SamAccountName, Department, Enabled, LastLogonDate
```

Search rather than fetch, when you only have part of a name:

```powershell
Get-ADUser -Filter "Surname -like 'Blog*'" -Properties Department |
    Select-Object Name, SamAccountName, Department
```

`-Filter` runs on the DC. `Where-Object` runs on your machine after
everything has been pulled across the wire. Filter first, always.

```powershell
# Good -- the DC does the work
Get-ADUser -Filter "Enabled -eq 'False'"

# Slow -- pulls every user, then throws most away
Get-ADUser -Filter * | Where-Object { -not $_.Enabled }
```

---

## Everything in an OU

```powershell
$OU = 'OU=Staff,OU=SITE,DC=example,DC=com'

Get-ADUser -Filter * -SearchBase $OU -SearchScope Subtree -Properties Description |
    Select-Object Name, SamAccountName, Description, Enabled |
    Sort-Object Name
```

`-SearchScope` is worth being explicit about:

| Scope | Means |
|---|---|
| `Base` | the OU object itself, nothing in it |
| `OneLevel` | direct children only |
| `Subtree` | the OU and everything below it (the default) |

Computers in an OU, which is the usual precursor to a remote query:

```powershell
Get-ADComputer -Filter * -SearchBase $OU -Properties OperatingSystem, LastLogonDate |
    Select-Object Name, OperatingSystem, LastLogonDate |
    Sort-Object LastLogonDate
```

---

## Group membership

The straightforward direction — who is in this group:

```powershell
Get-ADGroupMember -Identity 'GroupName' |
    Select-Object Name, SamAccountName, ObjectClass, DistinguishedName
```

Nested groups are the catch. `Get-ADGroupMember` returns the group object
itself, not the people inside it, unless you ask:

```powershell
Get-ADGroupMember -Identity 'GroupName' -Recursive |
    Select-Object Name, SamAccountName
```

`-Recursive` only returns users, never the intermediate groups — so if
you need to show the *path* by which someone has access, run it without
`-Recursive` and walk it yourself.

The other direction — what is this user in:

```powershell
Get-ADPrincipalGroupMembership -Identity 'jbloggs' |
    Select-Object Name, GroupCategory, GroupScope |
    Sort-Object Name
```

Including groups they inherit through nesting, which
`Get-ADPrincipalGroupMembership` does not show:

```powershell
$user = Get-ADUser -Identity 'jbloggs' -Properties MemberOf
$user.MemberOf | ForEach-Object {
    Get-ADGroup -Identity $_ -Properties MemberOf
} | Select-Object Name, DistinguishedName
```

---

## Cross-referencing an OU against a group

The pattern for "which of these people also has that access". Resolve the
group to a DN once, then match in memory rather than querying per user:

```powershell
$OU      = 'OU=Staff,DC=example,DC=com'
$GroupDN = (Get-ADGroup -Identity 'GroupName').DistinguishedName

$users = Get-ADUser -Filter * -SearchBase $OU -SearchScope Subtree -Properties MemberOf

$matched = $users | Where-Object { $_.MemberOf -contains $GroupDN }

$matched | Select-Object SamAccountName, DistinguishedName
"Matched {0} of {1} users" -f @($matched).Count, @($users).Count
```

Two things worth keeping:

- `-Properties MemberOf` — it is not returned by default, and without it
  the comparison silently matches nothing rather than erroring.
- `@($matched).Count` — a single result is not an array, and `.Count` on
  a lone object returns 1 in PowerShell 7 but nothing useful in 5.1.
  Wrapping in `@()` makes it behave the same on both.

`MemberOf` holds direct membership only. If the access could be granted
through a nested group, compare against `Get-ADGroupMember -Recursive`
instead.

---

## Exporting

```powershell
$exportPath = Join-Path $env:USERPROFILE 'Desktop\members.csv'

Get-ADGroupMember -Identity 'GroupName' -Recursive |
    Select-Object Name, SamAccountName, ObjectClass |
    Export-Csv -Path $exportPath -NoTypeInformation -Encoding UTF8

Write-Host "Exported to $exportPath"
```

- `-NoTypeInformation` drops the `#TYPE` junk line. Default in
  PowerShell 7, still needed in 5.1 — harmless to include either way.
- `-Encoding UTF8` matters the moment a name has an accent in it.
- `Join-Path` with `$env:USERPROFILE` beats a hard-coded `C:\Users\...`:
  it works on any machine and cannot leak an account name into a repo.

---

## Gotchas

**`-Filter` is not PowerShell.** It is its own syntax parsed by the AD
module. Double quotes with an expanded variable is the reliable form:

```powershell
$name = 'Blog*'
Get-ADUser -Filter "Surname -like '$name'"
```

The scriptblock form `-Filter { Surname -like $name }` looks like
PowerShell and mostly works, but fails in ways that are hard to read once
variables and properties are involved. Prefer the string.

**A disabled account is still a member.** Membership queries do not
filter on `Enabled`. For an access audit you almost always want:

```powershell
Get-ADGroupMember -Identity 'GroupName' -Recursive |
    Get-ADUser -Properties Enabled |
    Where-Object { $_.Enabled } |
    Select-Object Name, SamAccountName
```

**`LastLogonDate` is approximate.** It is replicated between DCs on a
delay of up to 14 days by default. For anything precise you need
`lastLogon` from every DC, which is a different job.
