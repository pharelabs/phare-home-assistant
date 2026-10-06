#!/usr/bin/env python3
"""Things a public repository must not carry: fail the build when one is found.

Checked in every tracked text file, and in the commit messages of the pull request:

* credentials: Phare personal access tokens, AWS keys, private keys, env files
* internal infrastructure: sandbox/dev API hosts, AWS account ids and ARNs
* personal data: company e-mail addresses, phone numbers, Auth0 user ids
  (test fixtures may carry anonymised ids; everything else may not)
* AI tool attribution in files or commit trailers

Run locally with `python .github/scripts/check_public_hygiene.py`; pass a commit range
(`origin/develop..HEAD`) to check commit messages too.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (name, regex, paths where the pattern is allowed)
# Role addresses a public README may legitimately carry.
PUBLIC_ADDRESSES = ("security@pharelabs.com", "support@pharelabs.com", "hello@pharelabs.com")
AI_TOOLS = r"(claude|anthropic|copilot|chatgpt|openai|codex|gemini|cursor|devin|aider|windsurf)"

RULES: list[tuple[str, re.Pattern[str], tuple[str, ...]]] = [
    ("Phare access token", re.compile(r"phare_pat_(?!testtoken)[A-Za-z0-9_-]{30,}"), ()),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), ()),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), ()),
    ("AWS account id or ARN", re.compile(r"\b\d{12}\b(?=.*amazonaws)|arn:aws:"), ()),
    ("API Gateway endpoint", re.compile(r"(?i)\b[a-z0-9]{10}\.execute-api\.[a-z0-9-]+\.amazonaws\.com"), ()),
    ("non-production Phare host", re.compile(r"(?i)\b(dev|sandbox|staging|test)\.[a-z]+\.pharelabs\.com"), ()),
    ("company e-mail address", re.compile(r"(?i)(?<![\w.])[A-Za-z0-9._%+-]+@([a-z0-9-]+\.)*pharelabs\.com"), ()),
    ("phone number", re.compile(r"(?<![\w/:])\(?\+\d{1,3}\)?[\d\s().-]{7,}\d(?![\w:])"), ()),
    ("Auth0 user id", re.compile(r"(?i)auth0\|[0-9a-f]{24}"), ("tests/",)),
    ("AI attribution", re.compile(
        r"(?im)^\s*(co-authored-by|assisted-by|generated-by|signed-off-by|reviewed-by):.*\b" + AI_TOOLS + r"\b"
        + r"|\b(generated|made|written|created|built)\s+(with|by)\s+(\[|the\s+)?(github\s+)?" + AI_TOOLS
        + r"|noreply@anthropic\.com|cursoragent@cursor\.com|\bcodex@openai\.com"
    ), ()),
]

SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".zip")
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|id_rsa.*)$")


SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
# The patterns above match their own source, and the test table that pins them.
EXEMPT_FILES = {SELF, "tests/test_public_hygiene.py"}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line and line not in EXEMPT_FILES]


def _strip_public_addresses(text: str) -> str:
    for address in PUBLIC_ADDRESSES:
        # Whole addresses only: "x.security@pharelabs.com" must still be flagged.
        text = re.sub(r"(?<![\w.%+-])" + re.escape(address), "", text, flags=re.I)
    return text


def line_problems(text: str, path: str = "") -> list[str]:
    """Rule names that match `text`, as a file line or a commit message would be checked."""
    hits = []
    for name, pattern, allowed in RULES:
        if path and any(path.startswith(prefix) for prefix in allowed):
            continue
        probe = _strip_public_addresses(text) if name == "company e-mail address" else text
        if pattern.search(probe):
            hits.append(name)
    return hits


def scan_files() -> list[str]:
    problems: list[str] = []
    for rel in tracked_files():
        if FORBIDDEN_FILES.search(rel):
            problems.append(f"{rel}: credential-style file must not be committed")
            continue
        if rel.endswith(SKIP_SUFFIXES):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            problems += [f"{rel}:{lineno}: {name}" for name in line_problems(line, rel)]
    return problems


def scan_commits(revision_range: str) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%s%x00%b%x01", revision_range],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    problems: list[str] = []
    for chunk in out.split("\x01"):
        if not chunk.strip():
            continue
        sha, subject, body = (chunk.strip("\n").split("\x00") + ["", ""])[:3]
        for name in line_problems(subject + "\n" + body):
            problems.append(f"commit {sha[:8]} ({subject[:50]}): {name} in the message")
    return problems


def main(argv: list[str]) -> int:
    problems = scan_files()
    if len(argv) > 1:
        problems += scan_commits(argv[1])
    if problems:
        print("Not suitable for a public repository:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("public hygiene: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
