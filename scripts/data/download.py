"""Download every Kaggle dataset into data/raw/<key>/ (git-ignored).

Needs a Kaggle API token in ~/.kaggle/kaggle.json (Windows: C:/Users/<you>/.kaggle/kaggle.json).
    uv run python -m scripts.data.download [--only upwork_60k ...]
Records file sizes and SHA-256 hashes in data/raw/download_manifest.json for the data card.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime

from scripts.data.sources import RAW, SOURCES


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", choices=sorted(SOURCES))
    args = parser.parse_args()
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    manifest = {"downloaded_at": datetime.now(UTC).isoformat(), "datasets": {}}
    for key, src in SOURCES.items():
        if args.only and key not in args.only:
            continue
        src.folder.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {src.slug} -> {src.folder}")
        api.dataset_download_files(src.slug, path=str(src.folder), unzip=True, quiet=False)
        manifest["datasets"][key] = {
            "slug": src.slug, "url": src.url, "license": src.license,
            "files": {str(p.relative_to(RAW)): {"bytes": p.stat().st_size, "sha256": sha256(p)}
                      for p in sorted(src.folder.rglob("*")) if p.is_file()},
        }
    (RAW / "download_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {RAW / 'download_manifest.json'}")


if __name__ == "__main__":
    main()
