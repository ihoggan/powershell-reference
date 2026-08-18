#!/usr/bin/env python3
"""
check_sheets.py -- gate for the PowerShell reference sheets.

WHAT
    Pulls every fenced ```powershell block out of the sheets and runs it
    through the real PowerShell parser
    ([System.Management.Automation.Language.Parser]::ParseInput), which
    checks syntax without executing anything. Then runs
    tools/scan_secrets.py over the same files.

    A reference sheet is only worth having if the thing you copy out of
    it runs. A sheet nobody has parsed is a sheet full of plausible
    text.

WHEN YOU'D REACH FOR IT
    After editing any sheet, and from CI on every push. Needs `pwsh` on
    PATH (PowerShell 7 runs on Linux -- see the install line in the
    README). Without pwsh it reports the parse stage as SKIPPED and
    still runs the secret scan, but a skipped parse is not a pass.

    Exit 0 clean, 1 findings, 2 bad usage or missing pwsh with
    --require-pwsh.

MEASURED LIMITS
    The parser checks SYNTAX, not existence or behaviour. It will not
    tell you a cmdlet was removed in PowerShell 7, that a parameter name
    is wrong, or that a module is retired -- `Get-Nonsense -Foo bar`
    parses perfectly.

    It also does NOT catch en-dash-for-hyphen: `Get-WmiObject
    <U+2013>ComputerName x` parses clean, because PowerShell reads the
    dashed word as a bare string argument. Measured, not assumed --
    see tests/prove_sheets.py. That class is caught by the
    smart-punctuation rule in scan_secrets.py instead, which is why
    both stages run here and why smart punctuation is promoted from
    WARN to a failure for sheets.

TESTED ON
    pwsh 7.4.6 on Linux x64, Python 3.12.3. Proven by
    tests/prove_sheets.py.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_secrets  # noqa: E402

# ```powershell ... ``` -- the only block type we claim to check.
FENCE = re.compile(
    r"^```powershell[ \t]*\r?\n(.*?)^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Blocks tagged with this on the fence line are console transcripts or
# deliberate counter-examples and are not expected to parse.
NOPARSE = re.compile(r"^```powershell[ \t]+noparse", re.MULTILINE)

# Parse the blocks with the real PowerShell parser. Written as a here-doc
# so there is one pwsh start-up for the whole run, not one per block.
PWSH_DRIVER = r"""
$ErrorActionPreference = 'Stop'
$manifest = Get-Content -Raw -LiteralPath $args[0] | ConvertFrom-Json
$out = @()
foreach ($item in $manifest) {
    $errors = $null
    $src = Get-Content -Raw -LiteralPath $item.file
    [System.Management.Automation.Language.Parser]::ParseInput(
        $src, [ref]$null, [ref]$errors) | Out-Null
    foreach ($e in $errors) {
        $out += [PSCustomObject]@{
            sheet   = $item.sheet
            block   = $item.block
            offset  = $item.offset
            line    = $e.Extent.StartLineNumber
            message = $e.Message
        }
    }
}
$out | ConvertTo-Json -Depth 4 -AsArray
"""


@dataclass(frozen=True)
class Block:
    sheet: str
    index: int
    start_line: int
    body: str


def extract_blocks(path: Path, root: Path) -> list[Block]:
    text = path.read_text(encoding="utf-8")
    noparse_starts = {m.start() for m in NOPARSE.finditer(text)}
    rel = str(path.relative_to(root))
    blocks: list[Block] = []
    for i, m in enumerate(FENCE.finditer(text), start=1):
        if m.start() in noparse_starts:
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        blocks.append(Block(rel, i, line_no, m.group(1)))
    return blocks


def parse_blocks(blocks: list[Block]) -> tuple[list[str], bool]:
    """Returns (failure messages, ran). ran=False means pwsh was absent."""
    if not blocks:
        return [], True
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return [], False

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest = []
        for b in blocks:
            f = tmp / f"block_{len(manifest):04d}.ps1"
            f.write_text(b.body, encoding="utf-8")
            manifest.append({
                "file": str(f),
                "sheet": b.sheet,
                "block": b.index,
                "offset": b.start_line,
            })
        mpath = tmp / "manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        driver = tmp / "driver.ps1"
        driver.write_text(PWSH_DRIVER, encoding="utf-8")

        proc = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File",
             str(driver), str(mpath)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            failures.append(f"pwsh driver failed: {proc.stderr.strip()[:400]}")
            return failures, True
        raw = proc.stdout.strip() or "[]"
        try:
            errors = json.loads(raw)
        except json.JSONDecodeError:
            failures.append(f"could not read pwsh output: {raw[:300]}")
            return failures, True

        for e in errors:
            # Line inside the block, plus where the block starts in the sheet.
            sheet_line = e["offset"] + e["line"]
            failures.append(
                f"PARSE  {e['sheet']}:~{sheet_line} (block {e['block']}, "
                f"line {e['line']}): {e['message']}")
    return failures, True


def scan_for_leaks(root: Path, blocks: list[Block]) -> list[str]:
    """Two different scopes, deliberately.

    BLOCK findings are scanned across the WHOLE sheet: a real server name
    is a leak whether it sits in a code block or in a sentence.

    Smart punctuation is scanned in CODE BLOCKS ONLY, and promoted there
    to a failure because the parser provably cannot see it. An em-dash in
    prose is correct typography, not a defect -- scanning prose for it
    produced 18 false positives on the first two sheets written, which is
    exactly the noise that trains you to ignore a gate.
    """
    failures = []
    for f in scan_secrets.scan_tree(root):
        if f.severity == scan_secrets.BLOCK:
            failures.append(
                f"LEAK   {f.path}:{f.line}: {f.rule} -- {f.text}")

    for b in blocks:
        for lineno, line in enumerate(b.body.splitlines(), start=1):
            for f in scan_secrets.rule_portability(line, lineno, b.sheet):
                failures.append(
                    f"PUNCT  {b.sheet}:~{b.start_line + lineno} "
                    f"(block {b.index}): {f.text} -- {f.note}")
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the reference sheets.")
    ap.add_argument("root", nargs="?", default="sheets")
    ap.add_argument("--require-pwsh", action="store_true",
                    help="treat a missing pwsh as a failure, not a skip")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    sheets = sorted(root.rglob("*.md"))
    blocks: list[Block] = []
    for s in sheets:
        blocks.extend(extract_blocks(s, root))

    parse_failures, ran = parse_blocks(blocks)
    leak_failures = scan_for_leaks(root, blocks)

    if ran:
        status = "clean" if not parse_failures else f"{len(parse_failures)} errors"
        print(f"parse   {len(blocks)} blocks in {len(sheets)} sheets: {status}")
    else:
        print(f"parse   SKIPPED -- pwsh not found ({len(blocks)} blocks unchecked)")
    print(f"leaks   {'clean' if not leak_failures else len(leak_failures)}")

    failures = parse_failures + leak_failures
    if failures:
        print()
        for f in failures:
            print("  " + f)
        return 1
    if not ran and args.require_pwsh:
        print("\n  pwsh missing and --require-pwsh set")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
