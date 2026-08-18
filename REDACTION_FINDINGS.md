# ihoggan/PowerShell — redaction findings

Scanned 41 files, 204 KB, on 2026-08-18 with `tools/scan_secrets.py`.
Result: **18 BLOCK, 19 WARN**. Not publishable as it stands.

---

## 1. BLOCK — must go before anything is public

### `Diskspace.ps1` (7.6 KB) — the worst offender, 14 of the 18

This is a real production SOX disk-space reporter lifted verbatim from a
previous employer's estate. It carries:

| What | Value |
|---|---|
| UNC share | `\\appserver01\PSScripts\DiskSpace\FreeSpace.htm` |
| AD domain | `DC=aspectworld,DC=com` (×6) |
| OUs | `OU=Servers`, `OU=Citrix Servers`, `OU=Domain Controllers` |
| SMTP hosts | `smtp.prd.kaplan.com`, `smtpaspect.int.kaplan.com`, `webmail.kaplan.com` |
| Mailboxes | `sox-disk-space@kaplan.com` |
| **Named individuals** | `david.yeo@kaplan.com`, `Yusuf.Tran@kaplan.com` |
| Host | `lon-dc01` (scanner blind spot — found by eye) |
| Org name | "KAPLAN International" in the HTML report title and header |

The two named colleagues are the part I'd treat as non-negotiable: that's
third-party personal data in a public repo, not just an untidy hostname.

### The rest

| File | Line | Finding |
|---|---|---|
| `Printers.ps1` | 1 | `\\prps01aplon` — print server name |
| `2025/Template_Azure_Bulk_Upload.ps1` | 6, 46 | `C:\Users\Iain.Hoggan\Downloads\Sleekflow.csv` — your own AD account name, twice |
| `Install App` | 1 | `C:\Users\kirnen\Desktop\...` — someone else's account name |

---

## 2. WARN — judgement calls

- **Internal addressing, 8 hits.** `10.34.16.x` in `IPScanner`,
  `Resolve-DnsName`, `SCRIPTS/BITLOCKER`, `SCRIPTS/Test-Connection IP Range`,
  `User Profiles`, `7. PS Input & Output`; `10.44.11.121` in
  `SCRIPTS/ping_and_time`. RFC1918, so not reachable — but it publishes a
  subnet layout for no benefit. Suggest swapping to `192.0.2.x`
  (TEST-NET-1, reserved for documentation), which also signals "example"
  to anyone reading.
- **OU names, 8 hits.** `OU=Citrix Servers` etc. describe the estate.
  `AD/ADUsers.ps1` is already generic (`OU=SITE,OU=LOCATION,DC=TEST`) and
  is the model to copy.
- **`C:\AccessQ`** in `Fast_Azure_Group_Members_Export_Script.ps1` — an
  internal folder name. Scanner blind spot; low risk.
- **`Tutors_FinalUsernames.csv`** in
  `SCRIPTS/FindDistributionGroupMember_Exchange.ps1` — hints at the sector.

---

## 3. Broken code found while reading

Separate from redaction, but it goes out under your name, so:

| File | Problem |
|---|---|
| `Printers.ps1` | **Does not parse.** `\| {foreach{$_.delete()}` — unbalanced braces, and a bare scriptblock in a pipeline is invalid. The filter `-and {$_.SystemName -eq '...'}` wraps the comparison in a scriptblock, which is always truthy — so if it *did* run, it would delete every network printer, not the matched ones. Dangerous and broken at once. |
| `ClearFontCache.ps1` | `Remove-Item ...\AppData\Local\*.* -Force` deletes the whole directory contents, not just the font cache. Should target `FontCache*.dat`. |
| `Logged in User` | Uses U+2013 en-dash for `–ComputerName` / `–Class`. Will not parse. Word-paste artefact. |
| `5. PS Variables` | Same en-dash on `–Descending`. |
| `6. PS Logic` | `for ($i = 0; $i - 15; $i++)` should be `-lt 15`; `for (...; $i -ge 0: $i--)` has a colon for a semicolon; `Get-ChildItem $path - Recurse` has a stray space; `elseif ($evalPath -lt $false)` is meaningless on a boolean. |
| `8. PS Errors...` | `Invoke-WebRequest -Uri -ErrorAction Stop` — `$uri` never passed. |
| `Test-Connection` | `ForEach { # do something $_.Address }` — the comment eats the body. |
| `Diskspace.ps1` | `sendEmail` declares `$smtphostserver` but the body uses the global `$smtpserver`; passing `lon-dc01` positionally does nothing. Division by zero if a volume reports `Size = 0`. |
| `Fast_Azure_Group_Members_Export_Script.ps1` | `Write-Progress ... / $GroupMembers.Count` divides by zero on an empty group; `$GroupMembersData +=` in a loop is O(n²); calls `Get-MgUser` per member for properties `Get-MgGroupMember` could have returned. |
| `FindDistributionGroupMember_Exchange.ps1` | Writes `Tutors_FinalUsernames.csv` but tells the user it wrote `Email_FinalUsernames.csv`. |

---

## 4. Platform reality

Most of this only runs on **Windows PowerShell 5.1**, not PowerShell 7:

- `Get-WmiObject` was removed in PS 6+ (`Get-CimInstance` is the
  replacement). Affects `IPScanner`, `Printers.ps1`, `Diskspace.ps1`,
  `Logged in User`, `User Profiles`, `Send Message`,
  `SCRIPTS/Function_2_Rule_All`.
- `AzureAD` and `MSOnline` are retired modules. Affects
  `Template_Azure_Bulk_Upload.ps1`, `FindAllUsersinGBAzure.ps1`,
  `FindUserAssignedDevicesAzure.ps1`, `FindUsersandDevicesAzure.ps1`.
  `Fast_Azure_Group_Members_Export_Script.ps1` is already on
  Microsoft.Graph and is the model.

Publishing 5.1-only scripts is fine; publishing them *without saying so*
is what makes a repo look unmaintained.

---

## 5. The history problem

**Redacting the files does not clean the repo.** Every value above is in
the commit history of `ihoggan/PowerShell`. If that repo is flipped
public after a redaction commit, `git log -p` still hands over
`aspectworld`, `appserver01` and both colleagues' addresses.

Two ways out:

- **A — fresh history.** Copy the redacted, renamed snippets into
  `toolbox` as new files. Leave `PowerShell` private (or delete it).
  Cheap, and nothing can leak from a history that was never published.
- **B — rewrite history.** `git filter-repo` over `PowerShell`, then
  flip it public. More work, and every miss is permanent the moment it
  goes public.

A is the recommendation. `toolbox` is where these were heading anyway,
and the 41-file layout here (spaces, `$` in filenames, no extensions)
isn't a layout worth preserving.

---

## 6. What enforces this

`tools/scan_secrets.py` is the gate. `tests/check_scan.py` is what makes
it trustworthy: 9 seeded leaks — one per rule — all caught, an
unmodified control tree clean, and 2 blind spots asserted as *still
missed* so the limits stay honest rather than assumed.

Proven by breaking it on purpose, four ways:

| Sabotage | Result |
|---|---|
| Allowlisted `kaplan.com` | 2 mutations missed, exit 1 |
| Unregistered the partial-IP rule | 1 mutation missed, exit 1 |
| Removed the placeholder allowlist | control tripped, exit 1 |
| Added a rule closing a documented blind spot | blind-spot assertion failed, exit 1 |
| Restored | 9/9, 0 false positives, exit 0 |

The partial-IP rule exists because the first real run missed
`"10.34.16.$_"` in `IPScanner` — a three-octet prefix with a variable
last octet publishes the subnet just as plainly as a full address, and
the full-address regex could not see it. Rule added, mutation added,
both now caught.
