#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"

def err(msg): print(f"{RED}✗ {msg}{NC}")
def ok(msg):  print(f"{GREEN}✓ {msg}{NC}")
def info(msg): print(f"{BLUE}{msg}{NC}")


def load_rollout(path):
    with open(path) as f:
        data = json.load(f)
    rollout_id = data.get("id")
    screens = [s.get("id", "unknown") for s in data.get("screens", [])]
    return rollout_id, screens, data


TRUNCATE_LEN = 120

def _truncate(s):
    s = json.dumps(s) if not isinstance(s, str) else s
    return s if len(s) <= TRUNCATE_LEN else s[:TRUNCATE_LEN] + "…"


def deep_diff(old, new, path=""):
    """Recursively diff two JSON values. Returns a list of human-readable change strings."""
    changes = []

    if type(old) != type(new):
        changes.append(f"`{path}`: `{_truncate(json.dumps(old))}` → `{_truncate(json.dumps(new))}`")
        return changes

    if isinstance(old, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            if key not in old:
                changes.append(f"`{child}`: added `{_truncate(json.dumps(new[key]))}`")
            elif key not in new:
                changes.append(f"`{child}`: removed")
            else:
                changes.extend(deep_diff(old[key], new[key], child))

    elif isinstance(old, list):
        # If list elements are dicts with an "id" field, match by id rather than position.
        old_by_id = {item["id"]: item for item in old if isinstance(item, dict) and "id" in item}
        new_by_id = {item["id"]: item for item in new if isinstance(item, dict) and "id" in item}
        if old_by_id or new_by_id:
            seen = []
            for item in old + new:
                if isinstance(item, dict) and "id" in item and item["id"] not in seen:
                    seen.append(item["id"])
            for id_ in seen:
                child = f"{path}[{id_}]" if path else f"[{id_}]"
                if id_ not in old_by_id:
                    changes.append(f"`{child}`: added")
                elif id_ not in new_by_id:
                    changes.append(f"`{child}`: removed")
                else:
                    changes.extend(deep_diff(old_by_id[id_], new_by_id[id_], child))
        elif old != new:
            changes.append(f"`{path}`: `{_truncate(json.dumps(old))}` → `{_truncate(json.dumps(new))}`")

    else:
        if old != new:
            changes.append(f"`{path}`: `{_truncate(json.dumps(old))}` → `{_truncate(json.dumps(new))}`")

    return changes


def build_diff_summary(old_data, new_data):
    """Return a list of markdown lines summarising all changes between two rollout configs."""
    lines = []

    # Top-level fields (excluding screens)
    for key in sorted(set(old_data) | set(new_data)):
        if key == "screens":
            continue
        old_val = old_data.get(key)
        new_val = new_data.get(key)
        if old_val == new_val:
            continue
        if key not in old_data:
            lines.append(f"- `{key}`: added `{_truncate(json.dumps(new_val))}`")
        elif key not in new_data:
            lines.append(f"- `{key}`: removed")
        else:
            for c in deep_diff(old_val, new_val, key):
                lines.append(f"- {c}")

    # Screens — match by screen id
    old_screens = {s["id"]: s for s in old_data.get("screens", []) if "id" in s}
    new_screens = {s["id"]: s for s in new_data.get("screens", []) if "id" in s}
    old_order = [s["id"] for s in old_data.get("screens", []) if "id" in s]
    new_order = [s["id"] for s in new_data.get("screens", []) if "id" in s]
    # Preserve old order, append any newly added screens at the end
    all_ids = list(dict.fromkeys(old_order + new_order))

    for screen_id in all_ids:
        if screen_id not in old_screens:
            lines.append(f"\n**Screen added:** `{screen_id}`")
        elif screen_id not in new_screens:
            lines.append(f"\n**Screen removed:** `{screen_id}`")
        else:
            changes = deep_diff(old_screens[screen_id], new_screens[screen_id])
            if changes:
                lines.append(f"\n**Screen changed:** `{screen_id}`")
                for c in changes:
                    lines.append(f"  - {c}")

    return lines


def append_ledger(archive_path, rollout_id, diff_lines):
    ledger_path = Path("ledger.md")
    author = subprocess.run(
        ["git", "config", "user.name"], capture_output=True, text=True
    ).stdout.strip() or "Unknown"
    today = date.today().strftime("%Y-%m-%d")

    body = "\n".join(diff_lines) if diff_lines else "_No content changes detected._"
    entry = (
        f"## {today} — {author} — {rollout_id}\n\n"
        f"**Archive:** `{archive_path.name}`\n\n"
        f"{body}\n\n"
        f"---\n\n"
    )

    if ledger_path.exists():
        existing = ledger_path.read_text()
        ledger_path.write_text(entry + existing)
    else:
        ledger_path.write_text(f"# Rollout Change Ledger\n\n{entry}")


if __name__ == "__main__":
    if not Path("current-rollout.json").exists():
        err("current-rollout.json not found")
        sys.exit(1)

    try:
        rollout_id, screens, old_data = load_rollout("current-rollout.json")
    except (json.JSONDecodeError, KeyError) as e:
        err(f"Failed to parse current-rollout.json: {e}")
        sys.exit(1)

    if not rollout_id:
        err("Rollout ID not found")
        sys.exit(1)

    ok(f"Rollout ID: {rollout_id}")
    print(f"   Screens:  {len(screens)}")

    # Back up current-rollout.json before the user makes changes
    archive_dir = Path("archive")
    archive_dir.mkdir(exist_ok=True)

    date_str = date.today().strftime("%y%m%d")
    rollout_slug = rollout_id.replace(":", "-")

    version = 0
    # Determine the version number for this date
    while True:
        archive_name = f"{date_str}-{version}-{rollout_slug}.json"
        archive_path = archive_dir / archive_name
        if not archive_path.exists():
            break
        version += 1

    if version > 0:
        # If we already have a version 0 for this date, prompt the user whether to overwrite or create a new version.
        answer = input(f"\n{YELLOW}{archive_path.parent / f'{date_str}-{version - 1}-{rollout_slug}.json'} already exists. Overwrite? (y/N): {NC}").strip().lower()
        if answer == "y":
            version -= 1
            archive_name = f"{date_str}-{version}-{rollout_slug}.json"
            archive_path = archive_dir / archive_name

    shutil.copy("current-rollout.json", archive_path)
    ok(f"Backed up to {archive_path}")

    # Prompt user to make their changes
    input(f"\n{BLUE}Make your changes to current-rollout.json, then press Enter to continue...{NC}")

    # Load the updated rollout
    try:
        new_rollout_id, new_screens, new_data = load_rollout("current-rollout.json")
    except (json.JSONDecodeError, KeyError) as e:
        err(f"Failed to parse current-rollout.json: {e}")
        sys.exit(1)

    if not new_rollout_id:
        err("Rollout ID not found")
        sys.exit(1)

    ok(f"Updated Rollout ID: {new_rollout_id}")
    print(f"   Screens:  {len(new_screens)}")

    # Diff new vs backup
    prev_set, curr_set = set(screens), set(new_screens)
    added = curr_set - prev_set
    removed = prev_set - curr_set
    if added or removed:
        print(f"\n   vs {archive_path.name}:")
        for s in sorted(added):   print(f"   {GREEN}+ {s}{NC}")
        for s in sorted(removed): print(f"   {RED}- {s}{NC}")
    else:
        print(f"   No screen ID changes vs {archive_path.name}")

    diff_lines = build_diff_summary(old_data, new_data)
    append_ledger(archive_path, new_rollout_id, diff_lines)
    ok("Ledger updated")

    # Commit + push
    answer = input("\nCommit? (y/N): ").strip().lower()
    if answer != "y":
        sys.exit(0)

    result = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True)
    if result.returncode != 0:
        err("Not in a git repository")
        sys.exit(1)

    subprocess.run(["git", "add", "current-rollout.json", str(archive_path), "ledger.md"], check=True)
    commit_msg = f"Update rollout: {new_rollout_id}\n\nArchive: {archive_name}"
    subprocess.run(["git", "commit", "-m", commit_msg], check=True)
    ok("Committed")

    answer = input("Push? (y/N): ").strip().lower()
    if answer == "y":
        subprocess.run(["git", "push"], check=True)
        ok("Pushed")
