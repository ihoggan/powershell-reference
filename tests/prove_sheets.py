#!/usr/bin/env python3
"""
prove_sheets.py -- proves tools/check_sheets.py actually rejects things.

WHAT
    Builds throwaway sheets in a temp directory and runs the gate
    against them:

      1. CONTROL     a small, correct sheet. Must pass. A gate that
                     fails everything is as useless as one that passes
                     everything.
      2. MUTATIONS   the control with one real defect seeded, once per
                     class. Every one is a line lifted from the old
                     ihoggan/PowerShell repo, not an invented example.
                     Each must be rejected.
      3. BLIND SPOT  the en-dash case, asserted as NOT caught by the
                     parser stage, then confirmed caught by the scanner
                     stage. This is why the gate has two stages at all;
                     if the parser ever starts catching it, this fails
                     and the note in check_sheets.py is stale.

WHEN YOU'D REACH FOR IT
    In CI, and by hand after touching check_sheets.py or the fence
    regex. A new stage without a mutation here is an untested stage.

TESTED ON
    pwsh 7.4.6 on Linux x64, Python 3.12.3.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))

import check_sheets  # noqa: E402

CONTROL = """# Control sheet

Enumerate a group.

```powershell
Get-ADGroupMember -Identity 'GroupName' |
    Select-Object Name, SamAccountName |
    Export-Csv -Path 'C:\\Users\\<username>\\Desktop\\members.csv' -NoTypeInformation
```

Sweep a subnet.

```powershell
1..254 | ForEach-Object {
    Test-Connection -TargetName "192.0.2.$_" -Count 1 -Quiet -TimeoutSeconds 1
}
```
"""

# Each mutation is a real line from the old repo.
MUTATIONS = [
    ("Printers.ps1 as written -- unbalanced braces",
     "```powershell\nGet-WMIObject Win32_Printer | where{$_.Network -eq 'true'} | "
     "{foreach{$_.delete()}\n```\n",
     "PARSE"),
    ("6. PS Logic -- colon where a semicolon belongs",
     "```powershell\nfor ($i = $aString.length; $i -ge 0: $i--) { $i }\n```\n",
     "PARSE"),
    ("8. PS Errors -- unterminated string",
     "```powershell\nWrite-Host 'Hello, will I run after an error?\n```\n",
     "PARSE"),
    ("Diskspace.ps1 -- internal SMTP host leaked into a sheet",
     "```powershell\n$SMTPServer = 'smtpaspect.int.kaplan.com'\n```\n",
     "LEAK"),
    ("Diskspace.ps1 -- UNC share leaked into a sheet",
     "```powershell\n$out = '\\\\appserver01\\PSScripts\\FreeSpace.htm'\n```\n",
     "LEAK"),
    ("Template_Azure_Bulk_Upload.ps1 -- real profile path",
     "```powershell\nImport-Csv 'C:\\Users\\Iain.Hoggan\\Downloads\\data.csv'\n```\n",
     "LEAK"),
    ("Logged in User -- en-dash for hyphen",
     "```powershell\nGet-CimInstance \u2013ClassName Win32_ComputerSystem\n```\n",
     "PUNCT"),
]

# A sheet whose PROSE uses correct typography -- em-dashes, quotes,
# ellipsis -- and whose code is clean. Must pass. This exists because
# scanning prose for smart punctuation produced 18 false positives on
# the first two real sheets; the fix was to narrow the check to code
# blocks, and this control is what stops that narrowing from quietly
# turning into "we no longer check punctuation at all".
PROSE_CONTROL = """# Prose control

Filter on the DC \u2014 not in `Where-Object` \u2014 because it\u2019s faster.
Paths like `C:\\Users\\...\\Desktop` are placeholders, and \u201cprimary
device\u201d isn\u2019t a real attribute\u2026 Prefer `$env:USERPROFILE`\nover a hard-coded `C:\\Users\\...`:

```powershell
Get-ADUser -Filter "Enabled -eq 'True'" | Select-Object Name
```
"""

BLIND_SPOT_ENDASH = (
    "```powershell\nGet-CimInstance \u2013ClassName Win32_ComputerSystem\n```\n"
)


def run(sheet_body: str):
    """Returns (exit code, printed lines)."""
    import io
    import contextlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "sheet.md").write_text(sheet_body, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = check_sheets.main([str(root)])
        return code, buf.getvalue()


def parse_stage_only(sheet_body: str):
    """Runs just the parser stage, to measure what it does and does not see."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "sheet.md"
        p.write_text(sheet_body, encoding="utf-8")
        blocks = check_sheets.extract_blocks(p, root)
        return check_sheets.parse_blocks(blocks)


def main() -> int:
    failures: list[str] = []

    code, out = run(CONTROL)
    if code == 0:
        print("control            passes")
    else:
        failures.append("CONTROL REJECTED:\n" + out)
        print("control            FAIL")

    code, out = run(PROSE_CONTROL)
    if code == 0:
        print("prose control      passes (typography in prose is not a defect)")
    else:
        failures.append("PROSE CONTROL REJECTED:\n" + out)
        print("prose control      FAIL")

    if "SKIPPED" in out:
        failures.append(
            "pwsh not found -- the parse stage did not run, so nothing below "
            "proves the parser catches anything.")
        print("pwsh               MISSING")

    caught = 0
    for label, extra, expect_tag in MUTATIONS:
        code, out = run(CONTROL + "\n" + extra)
        if code == 1 and expect_tag in out:
            caught += 1
            print(f"mutation rejected  {label}  ->  {expect_tag}")
        else:
            failures.append(
                f"MUTATION MISSED: {label!r} expected a {expect_tag} failure; "
                f"exit={code}\n{out}")
            print(f"mutation MISSED    {label}")

    # The blind spot, measured both ways.
    parse_failures, ran = parse_stage_only(CONTROL + "\n" + BLIND_SPOT_ENDASH)
    if not ran:
        print("blind spot         not measured (no pwsh)")
    elif parse_failures:
        failures.append(
            "BLIND SPOT CLOSED: the PowerShell parser now rejects an en-dash "
            "parameter prefix. Update MEASURED LIMITS in check_sheets.py.")
        print("blind spot CLOSED  en-dash now caught by the parser")
    else:
        print("blind spot         en-dash invisible to the parser "
              "(caught by the scanner stage instead)")

    print()
    print(f"{caught}/{len(MUTATIONS)} mutations rejected")

    if failures:
        print()
        for f in failures:
            print("  " + f.replace("\n", "\n  "))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
