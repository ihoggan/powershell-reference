#!/usr/bin/env python3
"""
check_scan.py -- proves tools/scan_secrets.py actually detects things.

WHAT
    Builds a throwaway tree in a temp directory and runs the scanner
    against it three ways:

      1. CONTROL       a tree of ordinary, already-sanitised PowerShell.
                       Must come back with zero BLOCK findings. If the
                       control trips, the scanner is crying wolf and its
                       clean runs mean nothing.
      2. MUTATIONS     the control, with exactly one real leak seeded
                       into it, once per leak class. Each must be caught,
                       by the expected rule. A scanner that has only ever
                       returned "clean" has proven nothing.
      3. BLIND SPOTS   things the scanner is known NOT to catch, asserted
                       as still-missed so the limitation stays honest. If
                       one of these starts being caught, this file fails
                       and the KNOWN LIMITS list in the scanner is stale.

WHEN YOU'D REACH FOR IT
    In CI on every push, and by hand after touching any rule or
    allowlist in scan_secrets.py. Adding a rule without adding a
    mutation here means the rule is untested.

TESTED ON
    Python 3.12.3 (Linux). Exit 0 = every mutation caught, control
    clean, blind spots still blind.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import scan_secrets  # noqa: E402

# --------------------------------------------------------------------------
# The control tree: realistic snippets, deliberately already clean.
# --------------------------------------------------------------------------

CONTROL = {
    "AD/Get-GroupMembers.ps1": (
        '$OU = "OU=Staff,DC=example,DC=com"\n'
        '$Group = "GroupName"\n'
        '$CSVPath = "C:\\Users\\me-profile\\Desktop\\Members.csv"\n'
        'Get-ADUser -Filter * -SearchBase $OU | Export-Csv $CSVPath\n'
    ),
    "Azure/Export-Users.ps1": (
        "# See https://learn.microsoft.com/en-us/powershell/ for the module\n"
        "Connect-MgGraph -Scopes 'User.Read.All'\n"
        '$user = Get-MgUser -UserId "john.doe@example.com"\n'
        '$out  = "C:\\Users\\<username>\\Desktop\\users.csv"\n'
    ),
    "Net/Test-Subnet.ps1": (
        "# TEST-NET-1, safe to publish\n"
        '1..254 | ForEach-Object { Test-Connection "192.0.2.$_" -Count 1 -Quiet }\n'
    ),
    "notes/pipeline.md": (
        "Get-Process | Sort-Object CPU -Descending\n"
        "Get-Help Stop-Process -Examples\n"
        "psexec64 \\\\<hostname> cmd -u <username> -p <password>\n"
    ),
}

# --------------------------------------------------------------------------
# Mutations: (label, filename, line to append, rule that must fire)
# Each is a real leak of the kind found in the wild.
# --------------------------------------------------------------------------

MUTATIONS = [
    ("real mailbox",
     "AD/Get-GroupMembers.ps1",
     'sendEmail reports@kaplan.com david.yeo@kaplan.com "Report"\n',
     "email"),
    ("internal FQDN",
     "AD/Get-GroupMembers.ps1",
     '$SMTPServer = "smtpaspect.int.kaplan.com"\n',
     "fqdn"),
    ("UNC share",
     "Net/Test-Subnet.ps1",
     '$out = "\\\\appserver01\\PSScripts\\FreeSpace.htm"\n',
     "unc"),
    ("AD domain component",
     "AD/Get-GroupMembers.ps1",
     '$ou = "OU=Citrix Servers,DC=aspectworld,DC=com"\n',
     "ldap-dc"),
    ("real profile path",
     "Azure/Export-Users.ps1",
     '$csv = Import-Csv "C:\\Users\\Iain.Hoggan\\Downloads\\Sleekflow.csv"\n',
     "profile-path"),
    ("private address",
     "Net/Test-Subnet.ps1",
     "Resolve-DnsName 10.34.16.6\n",
     "ip-private"),
    ("subnet prefix with variable octet",
     "Net/Test-Subnet.ps1",
     '2..254 | ForEach-Object { Test-Connection "10.34.16.$_" }\n',
     "ip-partial"),
    ("password on the command line",
     "notes/pipeline.md",
     "psexec64 \\\\server cmd -u svc_backup -p Winter2019!\n",
     "credential"),
    ("en-dash instead of hyphen",
     "notes/pipeline.md",
     "Get-WmiObject \u2013ComputerName x \u2013Class Win32_ComputerSystem\n",
     "smart-punctuation"),
]

# --------------------------------------------------------------------------
# Blind spots: asserted as NOT caught, so the limitation is measured
# rather than assumed. Each must stay missed by the named rule set.
# --------------------------------------------------------------------------

BLIND_SPOTS = [
    ("bare hostname with no domain and no UNC prefix",
     "notes/pipeline.md",
     "sendEmail from to subject lon-dc01 $freeSpaceFileName\n",
     "no pattern separates a server name from an ordinary word"),
    ("internal folder name",
     "Net/Test-Subnet.ps1",
     '$OutputFolder = "C:\\AccessQ"\n',
     "an arbitrary top-level folder is indistinguishable from any other"),
]


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def run(files: dict[str, str]):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_tree(root, files)
        return scan_secrets.scan_tree(root)


def main() -> int:
    failures: list[str] = []

    # 1. Control must be clean of BLOCK findings.
    control_findings = run(CONTROL)
    control_blocks = [f for f in control_findings if f.severity == scan_secrets.BLOCK]
    if control_blocks:
        for f in control_blocks:
            failures.append(
                f"CONTROL tripped: {f.rule} {f.path}:{f.line}: {f.text}")
        print(f"control            FAIL  ({len(control_blocks)} false positives)")
    else:
        print("control            clean")

    # 2. Every mutation must be caught by the expected rule.
    caught = 0
    for label, target, extra, expect_rule in MUTATIONS:
        files = dict(CONTROL)
        files[target] = files[target] + extra
        rules_fired = {f.rule for f in run(files)}
        baseline = {f.rule for f in control_findings}
        new_rules = rules_fired - baseline
        if expect_rule in new_rules:
            caught += 1
            print(f"mutation caught    {label:<40} rule: {expect_rule}")
        else:
            failures.append(
                f"MUTATION MISSED: {label!r} expected rule {expect_rule!r}, "
                f"new rules fired: {sorted(new_rules) or 'none'}")
            print(f"mutation MISSED    {label}")

    # 3. Blind spots must stay blind.
    for label, target, extra, why in BLIND_SPOTS:
        files = dict(CONTROL)
        files[target] = files[target] + extra
        baseline = {(f.rule, f.line) for f in control_findings}
        fired = {f.rule for f in run(files)} - {f.rule for f in control_findings}
        if fired:
            failures.append(
                f"BLIND SPOT CLOSED: {label!r} is now caught by {sorted(fired)}. "
                f"Update KNOWN LIMITS in scan_secrets.py and remove it from here.")
            print(f"blind spot CLOSED  {label}")
        else:
            print(f"blind spot         {label}  ({why})")

    print()
    print(f"{caught}/{len(MUTATIONS)} mutations caught, "
          f"{len(control_blocks)} control false positives, "
          f"{len(BLIND_SPOTS)} blind spots documented")

    if failures:
        print()
        for f in failures:
            print("  " + f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
