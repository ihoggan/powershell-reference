# PowerShell reference sheets

Working notes for Windows and Microsoft 365 administration, kept as
reference sheets rather than as runnable scripts. Each sheet is organised
around a job — "who is in this group", "what's live on this subnet" —
because that's how the question arrives.

Everything is written for **PowerShell 7**, with the Windows PowerShell
5.1 differences called out where they bite. Roughly half of the material
these sheets replace was written against `Get-WmiObject` and the retired
`AzureAD` module, neither of which exists in PowerShell 7.

## Sheets

| Sheet | Covers |
|---|---|
| [Active Directory](sheets/active-directory.md) | users, OUs, group membership, nesting, CSV export |
| [Entra ID](sheets/entra-id.md) | Graph scopes, users, groups, devices, bulk lookups, the AzureAD translation table |
| [Network discovery](sheets/network-discovery.md) | reachability, parallel sweeps, DNS, ports, MAC, remote CIM |
| [Endpoint admin](sheets/endpoint-admin.md) | logged-on user, profiles, disks, printers, services, installs, the WMI-to-CIM table |

## What is checked

Two claims are made here, and both are enforced by something runnable
rather than asserted:

**Every snippet parses.** `tools/check_sheets.py` extracts every fenced
`powershell` block and runs it through the real PowerShell parser
(`[System.Management.Automation.Language.Parser]::ParseInput`), which
checks syntax without executing anything.

**No internal identifiers.** `tools/scan_secrets.py` scans for email
addresses, internal hostnames, UNC shares, AD domain components, private
addressing and hard-coded account names. Examples use `192.0.2.0/24`
(TEST-NET-1) and `example.com`, both reserved for documentation.

```bash
python3 tools/check_sheets.py sheets    # both stages
python3 tests/prove_sheets.py           # proves the gate rejects things
python3 tests/check_scan.py             # proves the scanner detects things
```

The provers are the point. A check that has only ever passed proves
nothing, so each one seeds deliberate defects — taken from real breakage,
not invented — and fails if any goes undetected. It also asserts the
known blind spots are *still* blind, so the documented limits stay
honest instead of drifting.

Measured limits, rather than assumed ones:

- The parser checks syntax, not existence. `Get-Nonsense -Foo bar` parses
  perfectly.
- The parser does **not** see an en-dash used as a parameter prefix —
  `Get-CimInstance –ClassName x` parses clean and fails at runtime. That
  is why the gate has a second stage; the scanner catches it instead.
- The scanner reads files, not git history.
- A bare hostname with no domain and no UNC prefix is undetectable. No
  pattern separates a server name from an ordinary word.

## Requirements

Python 3.9+ (standard library only — there is no `requirements.txt`
because nothing is imported that isn't built in) and `pwsh` for the parse
stage.

```bash
curl -sL -o /tmp/pwsh.tar.gz \
  https://github.com/PowerShell/PowerShell/releases/download/v7.4.6/powershell-7.4.6-linux-x64.tar.gz
sudo mkdir -p /opt/pwsh && sudo tar -xzf /tmp/pwsh.tar.gz -C /opt/pwsh
sudo chmod +x /opt/pwsh/pwsh && sudo ln -sf /opt/pwsh/pwsh /usr/local/bin/pwsh
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
```

Without `pwsh` the parse stage reports SKIPPED and the leak scan still
runs. A skipped parse is not a pass — CI uses `--require-pwsh` so it
cannot go green by accident.
