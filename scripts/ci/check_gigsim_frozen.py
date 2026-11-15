"""Fail CI if anything under gigsim/ changed since it was frozen.

The brief treats GigSim as a provided, unmodifiable simulator. This repo builds its own, so
after the gigsim-v1.0 tag it is frozen: gigsim/FROZEN holds a hash of every tracked file.
A deliberate new GigSim version updates the hash with --update in its own commit.

    uv run python -m scripts.ci.check_gigsim_frozen            # check
    uv run python -m scripts.ci.check_gigsim_frozen --update   # re-freeze (new version only)
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "gigsim" / "FROZEN"


def tree_hash() -> str:
    files = subprocess.run(["git", "ls-files", "gigsim"], cwd=ROOT, check=True,
                           capture_output=True, text=True).stdout.split()
    h = hashlib.sha256()
    for rel in sorted(f for f in files if f != "gigsim/FROZEN"):
        h.update(rel.encode())
        h.update(hashlib.sha256((ROOT / rel).read_bytes()).digest())
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    current = tree_hash()
    if args.update:
        FROZEN.write_text(current + "\n")
        print(f"gigsim frozen at {current}")
        return 0
    if not FROZEN.exists():
        print("gigsim/FROZEN missing: GigSim not frozen yet (allowed before gigsim-v1.0)")
        return 0
    expected = FROZEN.read_text().strip()
    if expected != current:
        print(f"gigsim/ changed since it was frozen (expected {expected[:12]}, got {current[:12]})")
        return 1
    print("gigsim/ unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
