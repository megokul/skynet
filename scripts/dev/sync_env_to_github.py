#!/usr/bin/env python3
"""
Sync local .env key/value pairs to GitHub Actions secrets/variables.

Defaults are tuned for this repository policy:
- Secrets-only sync in `--mode secrets`.
- Optional stale-secret pruning with workflow-aware keep set.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
GH_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WORKFLOW_SECRET_RE = re.compile(r"secrets\.([A-Z0-9_]+)")

SECRET_EXACT = {
    "SKYNET_API_KEY",
    "SKYNET_AUTH_TOKEN",
    "OPENCLAW_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "OPENCLAW_SSH_PRIVATE_KEY_B64",
    "OPENCLAW_SSH_PASSWORD",
    "GH_TOKEN",
    "GITHUB_PAT",
}
SECRET_SUFFIXES = (
    "_TOKEN",
    "_PASSWORD",
    "_API_KEY",
    "_API_HASH",
    "_SECRET",
    "_PRIVATE_KEY",
    "_PAT",
)


def is_secret_key(name: str) -> bool:
    key = name.strip().upper()
    if key in SECRET_EXACT:
        return True
    return any(key.endswith(suffix) for suffix in SECRET_SUFFIXES)


def parse_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        match = ENV_LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
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
    match = re.search(r"github\.com[:/]+([^/]+/[^/.]+)(?:\.git)?$", url)
    if not match:
        raise RuntimeError("Unable to parse remote.origin.url. Pass --repo owner/name.")
    return match.group(1)


def list_target_names(gh_bin: str, repo: str, mode: str) -> set[str]:
    target = "secret" if mode == "secrets" else "variable"
    res = run_cmd([gh_bin, target, "list", "--repo", repo])
    if res.returncode != 0:
        return set()

    names: set[str] = set()
    for line in (res.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        names.add(text.split("\t", 1)[0].strip())
    return names


def workflow_secret_names(root: Path) -> set[str]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.exists():
        return set()

    names: set[str] = set()
    for path in workflows_dir.glob("*.yml"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        names.update(WORKFLOW_SECRET_RE.findall(text))
    for path in workflows_dir.glob("*.yaml"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        names.update(WORKFLOW_SECRET_RE.findall(text))
    return names


def sync_values(
    gh_bin: str,
    repo: str,
    values: dict[str, str],
    mode: str,
    *,
    dry_run: bool,
    quiet: bool,
    secret_limit: int,
    secrets_only: bool,
) -> tuple[int, int, int, set[str]]:
    target = "secret" if mode == "secrets" else "variable"
    updated = 0
    failed = 0
    skipped = 0
    synced_keys: set[str] = set()

    existing = list_target_names(gh_bin, repo, mode)
    existing_count = len(existing)

    for key in sorted(values.keys()):
        value = values[key]

        if mode == "secrets" and secrets_only and not is_secret_key(key):
            skipped += 1
            if not quiet:
                print(f"skipped {target}: {key} (non-secret key)")
            continue
        if key.upper().startswith("GITHUB_"):
            skipped += 1
            if not quiet:
                print(f"skipped {target}: {key} (reserved prefix)")
            continue
        if not GH_NAME_RE.match(key):
            skipped += 1
            if not quiet:
                print(f"skipped {target}: {key} (invalid name)")
            continue
        if value == "":
            skipped += 1
            if not quiet:
                print(f"skipped {target}: {key} (empty value)")
            continue
        if mode == "secrets" and key not in existing and existing_count >= secret_limit:
            skipped += 1
            if not quiet:
                print(f"skipped {target}: {key} (secret quota reached: {secret_limit})")
            continue

        cmd = [gh_bin, target, "set", key, "--repo", repo, "--body", value]
        if dry_run:
            if not quiet:
                print(f"[dry-run] gh {target} set {key} --repo {repo} --body ***")
            updated += 1
            synced_keys.add(key)
            if key not in existing:
                existing.add(key)
                existing_count += 1
            continue

        res = run_cmd(cmd)
        if res.returncode == 0:
            updated += 1
            synced_keys.add(key)
            if key not in existing:
                existing.add(key)
                existing_count += 1
            if not quiet:
                print(f"updated {target}: {key}")
            continue

        failed += 1
        print(f"failed {target}: {key}\n{res.stderr.strip()}", file=sys.stderr)

    return updated, failed, skipped, synced_keys


def prune_stale_secrets(
    gh_bin: str,
    repo: str,
    *,
    desired: set[str],
    dry_run: bool,
    quiet: bool,
) -> tuple[int, int]:
    removed = 0
    failed = 0

    existing = list_target_names(gh_bin, repo, "secrets")
    stale = sorted(name for name in existing if name not in desired)
    for name in stale:
        cmd = [gh_bin, "secret", "delete", name, "--repo", repo]
        if dry_run:
            if not quiet:
                print(f"[dry-run] gh secret delete {name} --repo {repo}")
            removed += 1
            continue

        res = run_cmd(cmd)
        if res.returncode == 0:
            removed += 1
            if not quiet:
                print(f"removed secret: {name}")
            continue

        failed += 1
        print(f"failed remove secret: {name}\n{res.stderr.strip()}", file=sys.stderr)

    return removed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync .env values to GitHub repo secrets/variables.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--repo", default="", help="GitHub repo in owner/name form")
    parser.add_argument("--mode", choices=["secrets", "variables"], default="secrets")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing to GitHub")
    parser.add_argument("--quiet", action="store_true", help="Reduce output")
    parser.add_argument("--secret-limit", type=int, default=100)
    parser.add_argument(
        "--all-keys",
        action="store_true",
        help="In secrets mode, sync all keys instead of only secret-like keys.",
    )
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="In secrets mode, remove repo secrets not present in keep set.",
    )
    parser.add_argument(
        "--keep-keys",
        default="",
        help="Comma-separated extra secret names to preserve while pruning.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f".env file not found: {env_path}", file=sys.stderr)
        return 2

    gh_bin = resolve_gh()
    if run_cmd([gh_bin, "--version"]).returncode != 0:
        print("gh CLI is not installed or not in PATH.", file=sys.stderr)
        return 2

    if run_cmd([gh_bin, "auth", "status"]).returncode != 0:
        print("gh is not authenticated. Run: gh auth login", file=sys.stderr)
        return 2

    repo = args.repo.strip() or detect_repo(gh_bin)
    values = parse_env(env_path)
    if not values:
        print("No env keys found to sync.", file=sys.stderr)
        return 2

    updated, failed, skipped, synced_keys = sync_values(
        gh_bin,
        repo,
        values,
        args.mode,
        dry_run=args.dry_run,
        quiet=args.quiet,
        secret_limit=max(args.secret_limit, 0),
        secrets_only=(args.mode == "secrets" and not args.all_keys),
    )

    removed = 0
    remove_failed = 0
    if args.mode == "secrets" and args.prune_stale:
        root = Path.cwd()
        workflow_refs = workflow_secret_names(root)
        keep_keys = {name.strip() for name in args.keep_keys.split(",") if name.strip()}
        desired = set(synced_keys) | workflow_refs | keep_keys
        removed, remove_failed = prune_stale_secrets(
            gh_bin,
            repo,
            desired=desired,
            dry_run=args.dry_run,
            quiet=args.quiet,
        )

    total_failed = failed + remove_failed
    print(
        "sync complete: "
        f"updated={updated} removed={removed} failed={total_failed} skipped={skipped} "
        f"repo={repo} mode={args.mode}"
    )
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
