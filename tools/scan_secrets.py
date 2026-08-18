#!/usr/bin/env python3
"""
scan_secrets.py -- publish gate for snippet repos.

WHAT
    Walks a directory tree and reports material that identifies a real
    organisation: email addresses, internal FQDNs, UNC shares, LDAP
    distinguished names, private IPv4 addresses, Windows profile paths
    with a real username in them, and credentials passed on a command
    line. Findings are split into BLOCK (do not publish) and WARN
    (a human must look, may well be fine).

    It also carries one non-secret check, PORTABILITY, because the same
    copy-out-of-a-Word-document habit that leaks a server name also
    leaves en-dashes where a hyphen belongs, and `Get-WmiObject
    -Class ...` written with U+2013 does not run.

WHEN YOU'D REACH FOR IT
    Before flipping any repo of collected scripts from private to
    public, and from the pre-commit hook in the toolbox repo so the
    same material cannot come back in later. Run it on the WORKING
    TREE, not on git history -- it cannot see what is already
    committed, and a clean scan of a dirty history is a false comfort.
    See KNOWN LIMITS.

    Exit codes: 0 clean (or WARN-only without --strict), 1 findings,
    2 bad usage. That makes it usable directly as a CI step.

KNOWN LIMITS (measured, see tests/check_scan.py)
    - It scans files, not git history. If a secret was ever committed,
      redacting the file does not remove it; the repo needs
      git-filter-repo or a fresh history.
    - Hostnames with no domain suffix and no UNC prefix (`lon-dc01`,
      `nix5`) are NOT detected. There is no pattern that separates a
      server name from an ordinary word. They must be found by eye.
    - A private IPv4 address is a WARN, not a BLOCK: 10.34.16.6 is not
      reachable from outside, but it does publish an addressing scheme.
    - Placeholders are allowlisted by shape (example.com, contoso.com,
      <angle brackets>, DC=TEST). A real domain that happens to look
      like a placeholder would be missed.

TESTED ON
    Python 3.12.3 (Linux) and the ihoggan/PowerShell tree, 41 files.
    Proven by tests/check_scan.py: 9 seeded leaks caught, unmodified
    control clean, 2 blind spots measured and listed above.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BLOCK = "BLOCK"
WARN = "WARN"

# Directories never worth walking into.
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}

# Extensions that are not text we can usefully grep.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz",
                 ".pdf", ".docx", ".xlsx", ".pyc", ".exe", ".dll"}

# Domains that are reserved for documentation or are obviously stand-ins.
# RFC 2606 / RFC 6761 reserve example.com|net|org, .example, .invalid,
# .test, .localhost; contoso.com and fabrikam.com are Microsoft's.
PLACEHOLDER_DOMAINS = re.compile(
    r"""(?ix)
    (?: ^ | [@.] )
    (?:
        example\.(?:com|net|org)
      | contoso\.(?:com|onmicrosoft\.com)
      | fabrikam\.com
      | company\.(?:com|onmicrosoft\.com)
      | domain(?:name)?\.com
      | yourdomain\.com
      | localhost
    ) $
    """,
)

# Domains that are public documentation sources, not the user's estate.
VENDOR_DOMAINS = {
    "docs.microsoft.com", "learn.microsoft.com", "microsoft.com",
    "go.microsoft.com", "github.com", "raw.githubusercontent.com",
    "reddit.com", "www.reddit.com", "petri.com", "www.petri.com",
    "techthoughts.info", "nextofwindows.com", "www.nextofwindows.com",
    "powershellgallery.com", "www.powershellgallery.com",
}

# Local-parts that are plainly stand-ins.
PLACEHOLDER_LOCALPARTS = {
    "john.doe", "jane.doe", "johndoe", "user", "username", "someone",
    "test", "testuser", "admin", "me", "you", "firstname.lastname",
    "first.last", "noreply", "no-reply", "email",
}

# Windows profile names that are stand-ins rather than real accounts.
PLACEHOLDER_PROFILES = {
    "me-profile", "myprofile", "my-profile", "profile", "username",
    "user", "youruser", "yourname", "public", "default", "all users",
    "administrator", "<username>", "%username%", "...", "\u2026",
}

# LDAP DC= values that are stand-ins.
PLACEHOLDER_DCS = {"test", "domain", "domainname", "example", "contoso",
                   "com", "local", "yourdomain", "site", "location"}


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    line: int
    text: str
    note: str = ""


def _has_angle_placeholder(match_text: str) -> bool:
    """`<group name>` and friends are stand-ins, not real values."""
    return "<" in match_text and ">" in match_text


# --------------------------------------------------------------------------
# Rules. Each takes (line, lineno, relpath) and yields Findings.
# --------------------------------------------------------------------------

RE_EMAIL = re.compile(
    r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def rule_email(line: str, lineno: int, rel: str):
    for m in RE_EMAIL.finditer(line):
        local, domain = m.group(1), m.group(2)
        dl = domain.lower()
        if PLACEHOLDER_DOMAINS.search("@" + dl) or PLACEHOLDER_DOMAINS.search(dl):
            continue
        if dl in VENDOR_DOMAINS:
            continue
        if local.lower() in PLACEHOLDER_LOCALPARTS:
            continue
        yield Finding(BLOCK, "email", rel, lineno, m.group(0),
                      "real-looking mailbox")


# A bare FQDN not inside a URL to a known vendor. Two or more dotted
# labels ending in a plausible TLD, not preceded by "//" or "@".
RE_FQDN = re.compile(
    r"(?<![/@\w.-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.){2,}"
    r"(?:com|net|org|local|int|co\.uk|io|info|gov|edu|ac\.uk))\b"
)


def rule_fqdn(line: str, lineno: int, rel: str):
    for m in RE_FQDN.finditer(line):
        host = m.group(1)
        hl = host.lower().rstrip(".")
        if hl in VENDOR_DOMAINS or any(hl.endswith("." + v) for v in VENDOR_DOMAINS):
            continue
        if PLACEHOLDER_DOMAINS.search(hl):
            continue
        # onmicrosoft.com tenant names are identifying unless placeholder.
        yield Finding(BLOCK, "fqdn", rel, lineno, host,
                      "internal or tenant hostname")


RE_UNC = re.compile(r"\\\\\\\\([A-Za-z0-9_.-]+)|\\\\([A-Za-z0-9_.-]+)")


def rule_unc(line: str, lineno: int, rel: str):
    for m in RE_UNC.finditer(line):
        host = m.group(1) or m.group(2)
        if not host or host.lower() in {"n", "t", "r", "s", "d", "w", "b"}:
            continue  # regex escapes like \\d in a PowerShell string
        if _has_angle_placeholder(line[max(0, m.start() - 2): m.end() + 2]):
            continue
        if host.lower() in {"hostname", "servername", "computername",
                            "server", "<hostname>"}:
            continue
        yield Finding(BLOCK, "unc", rel, lineno, "\\\\" + host,
                      "UNC path names a real host")


RE_DC = re.compile(r"\bDC\s*=\s*([A-Za-z0-9_-]+)", re.I)
RE_OU = re.compile(r"\bOU\s*=\s*([A-Za-z0-9 _-]+)", re.I)


def rule_ldap(line: str, lineno: int, rel: str):
    for m in RE_DC.finditer(line):
        val = m.group(1)
        if val.lower() in PLACEHOLDER_DCS:
            continue
        yield Finding(BLOCK, "ldap-dc", rel, lineno, m.group(0),
                      "AD domain component")
    for m in RE_OU.finditer(line):
        val = m.group(1).strip()
        if val.lower() in PLACEHOLDER_DCS or val.upper() == val and len(val) <= 4:
            continue
        yield Finding(WARN, "ldap-ou", rel, lineno, m.group(0),
                      "OU name may describe the estate")


RE_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")


def _is_private(ip: str) -> bool:
    try:
        a, b, c, d = (int(p) for p in ip.split("."))
    except ValueError:
        return False
    if not all(0 <= p <= 255 for p in (a, b, c, d)):
        return False
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return False


def _is_documentation_ip(ip: str) -> bool:
    # RFC 5737 TEST-NET ranges, plus the usual loopback/any.
    return (ip.startswith("192.0.2.") or ip.startswith("198.51.100.")
            or ip.startswith("203.0.113.") or ip in {"127.0.0.1", "0.0.0.0",
                                                     "255.255.255.255"})


def rule_ipv4(line: str, lineno: int, rel: str):
    for m in RE_IPV4.finditer(line):
        ip = m.group(1)
        if _is_documentation_ip(ip):
            continue
        if not _is_private(ip):
            # Public literal in a script is worth a look but is often a
            # version number caught by mistake; only flag valid octets.
            parts = [int(p) for p in ip.split(".") if p.isdigit()]
            if len(parts) != 4 or any(p > 255 for p in parts):
                continue
            yield Finding(WARN, "ip-public", rel, lineno, ip,
                          "public address literal")
            continue
        yield Finding(WARN, "ip-private", rel, lineno, ip,
                      "publishes internal addressing")


# A /24 prefix with the last octet built from a variable -- the shape a
# ping sweep takes. Publishes the subnet just as plainly as a full
# address does, and the full-address rule cannot see it.
RE_IP_PARTIAL = re.compile(
    r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3})\.\$[A-Za-z_]"
)


def rule_ipv4_partial(line: str, lineno: int, rel: str):
    for m in RE_IP_PARTIAL.finditer(line):
        prefix = m.group(1)
        octets = [int(p) for p in prefix.split(".")]
        if any(o > 255 for o in octets):
            continue
        if _is_private(prefix + ".1") or not _is_documentation_ip(prefix + ".1"):
            yield Finding(WARN, "ip-partial", rel, lineno, prefix + ".$",
                          "subnet prefix with a variable last octet")


RE_PROFILE = re.compile(r"(?i)\b[A-Z]:\\Users\\([^\\\"'\s]+)")


def rule_profile(line: str, lineno: int, rel: str):
    for m in RE_PROFILE.finditer(line):
        # Trailing markdown or sentence punctuation is not part of a path:
        # a backtick, colon, comma or bracket cannot appear in a Windows
        # account name, so strip them before the allowlist is consulted.
        name = m.group(1).rstrip("`:,;)]\"'")
        if not name:
            continue
        if name.lower() in PLACEHOLDER_PROFILES:
            continue
        if name.startswith("$") or name.startswith("%") or _has_angle_placeholder(name):
            continue
        yield Finding(BLOCK, "profile-path", rel, lineno, m.group(0),
                      "hard-coded account name")


RE_CRED = re.compile(
    r"(?i)(?:^|\s)(?:-p|-pass|-password|/p:|--password)\s+(?!<)[^\s<]+"
)


def rule_credential(line: str, lineno: int, rel: str):
    for m in RE_CRED.finditer(line):
        yield Finding(BLOCK, "credential", rel, lineno, m.group(0).strip(),
                      "secret on a command line")


# U+2013 EN DASH, U+2014 EM DASH, curly quotes -- Word-paste artefacts.
RE_SMART = re.compile("[\u2013\u2014\u2018\u2019\u201c\u201d]")


def rule_portability(line: str, lineno: int, rel: str):
    m = RE_SMART.search(line)
    if m:
        ch = m.group(0)
        broken = ch in "\u2013\u2014" and re.search(
            r"[\u2013\u2014][A-Za-z]", line)
        yield Finding(
            WARN, "smart-punctuation", rel, lineno,
            f"U+{ord(ch):04X} {ch!r}",
            "parameter will not parse" if broken else "Word-paste artefact",
        )


RULES = (rule_email, rule_fqdn, rule_unc, rule_ldap, rule_ipv4,
         rule_ipv4_partial, rule_profile, rule_credential, rule_portability)


# --------------------------------------------------------------------------


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield p


def scan_file(path: Path, root: Path) -> list[Finding]:
    rel = str(path.relative_to(root))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # unreadable is a finding in itself
        return [Finding(WARN, "unreadable", rel, 0, str(exc))]
    out: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            out.extend(rule(line, lineno, rel))
    return out


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in iter_files(root):
        findings.extend(scan_file(p, root))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Find organisation-identifying material before publishing.")
    ap.add_argument("root", nargs="?", default=".", help="directory to scan")
    ap.add_argument("--strict", action="store_true",
                    help="fail on WARN as well as BLOCK")
    ap.add_argument("--quiet", action="store_true",
                    help="print the summary only")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    findings = scan_tree(root)
    blocks = [f for f in findings if f.severity == BLOCK]
    warns = [f for f in findings if f.severity == WARN]

    if not args.quiet:
        for f in sorted(findings, key=lambda f: (f.severity != BLOCK, f.path, f.line)):
            note = f" -- {f.note}" if f.note else ""
            print(f"{f.severity:5} {f.rule:18} {f.path}:{f.line}: {f.text}{note}")
        if findings:
            print()

    print(f"scanned {root}: {len(blocks)} BLOCK, {len(warns)} WARN")
    if blocks:
        return 1
    if warns and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
