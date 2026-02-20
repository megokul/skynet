#!/usr/bin/env python3
"""
Sync local .env key/value pairs into GitHub repository secrets or variables.

Default behavior writes all keys to GitHub Actions secrets because the deploy
workflow consumes secrets.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        m = ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def run_cmd(args: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def resolve_gh() -> str:
    gh = shutil.which("gh")
    if gh:
        return gh

    userprofile = Path.home()
    candidates = [
        userprofile / "tools" / "gh" / "bin" / "gh.exe",
        userprofile / "AppData" / "Local" / "Programs" / "GitHubCLI" / "bin" / "gh.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "gh"


def detect_repo(gh_bin: str) -> str:
    view = run_cmd([gh_bin, "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if view.returncode == 0 and view.stdout.strip():
        return view.stdout.strip()

    remote = run_cmd(["git", "config", "--get", "remote.origin.url"])
    if remote.returncode != 0:
        raise RuntimeError("Unable to detect GitHub repo. Pass --repo owner/name.")
    url = remote.stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/.]+)(?:\.git)?$", url)
    if not m:
        raise RuntimeError("Unable to parse remote.origin.url. Pass --repo owner/name.")
    return m.group(1)


def sync_values(
    gh_bin: str,
    repo: str,
    values: dict[str, str],
    mode: str,
    dry_run: bool = False,
    quiet: bool = False,
) -> tuple[int, int]:
    updated = 0
    failed = 0
    for key in sorted(values.keys()):
        value = values[key]
        target = "secret" if mode == "secrets" else "variable"
        cmd = [gh_bin, target, "set", key, "--repo", repo, "--body", value]
        if dry_run:
            if not quiet:
                print(f"[dry-run] gh {target} set {key} --repo {repo} --body ***")
            updated += 1
            continue
        res = run_cmd(cmd)
        if res.returncode == 0:
            updated += 1
            if not quiet:
                print(f"updated {target}: {key}")
        else:
            failed += 1
            print(f"failed {target}: {key}\n{res.stderr.strip()}", file=sys.stderr)
    return updated, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync .env values to GitHub repo secrets/variables.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--repo", default="", help="GitHub repo in owner/name form")
    parser.add_argument(
        "--mode",
        choices=["secrets", "variables"],
        default="secrets",
        help="Sync target type (default: secrets)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing to GitHub")
    parser.add_argument("--quiet", action="store_true", help="Reduce output")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f".env file not found: {env_path}", file=sys.stderr)
        return 2

    gh_bin = resolve_gh()
    gh_check = run_cmd([gh_bin, "--version"])
    if gh_check.returncode != 0:
        print("gh CLI is not installed or not in PATH.", file=sys.stderr)
        return 2

    auth_check = run_cmd([gh_bin, "auth", "status"])
    if auth_check.returncode != 0:
        print("gh is not authenticated. Run: gh auth login", file=sys.stderr)
        return 2

    repo = args.repo.strip() or detect_repo(gh_bin)
    values = parse_env(env_path)
    if not values:
        print("No env keys found to sync.", file=sys.stderr)
        return 2

    updated, failed = sync_values(
        gh_bin,
        repo,
        values,
        mode=args.mode,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )
    print(f"sync complete: updated={updated} failed={failed} repo={repo} mode={args.mode}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
