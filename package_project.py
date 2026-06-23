#!/usr/bin/env python3
"""
package_project.py — Make a clean ZIP of the whole project for sharing/review.

Excludes secrets (.env), Python caches, virtualenvs, node_modules, large
regenerable data caches (data/raw/*), zips, logs and OS junk — so the archive
stays small and SAFE to send (no API keys leak).

USAGE — put this file in the PROJECT ROOT (the 'fair-line' folder, next to the
'src' and 'web' folders), then from anywhere:

    python package_project.py

Output: fair-line-clean.zip  (next to this script)
"""
import os
import sys
import fnmatch
import zipfile

# The project root = the folder this script lives in.
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "fair-line-clean.zip")

# Whole directories to skip wherever they appear in the tree.
SKIP_DIRS = {
    "__pycache__", ".git", ".github", ".venv", "venv", "env", "ENV",
    "node_modules", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    ".ipynb_checkpoints", "dist", "build", ".cache",
}

# Directories to skip by RELATIVE path (large, regenerable data caches).
# data/raw holds the downloaded historical CSVs — they re-download on run.
SKIP_REL_PREFIXES = [
    os.path.normpath("data/raw"),
    os.path.normpath("data/cache"),
]

# Individual files to skip (glob patterns).
SKIP_FILE_GLOBS = [
    "*.pyc", "*.pyo", "*.pyd", "*.log", "*.zip", "*.tmp", "*.bak",
    "*.sqlite", "*.db", ".DS_Store", "Thumbs.db", "desktop.ini",
]

# Secret files — NEVER include. Tracked separately so we can warn about them.
SECRET_GLOBS = [".env", ".env.*", "*.pem", "*.key", "service_key*", "secrets*"]
# ...but keep harmless templates that only contain placeholder variable names.
SECRET_KEEP_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_secret(name):
    if name.endswith(SECRET_KEEP_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS)


def matches(name, globs):
    return any(fnmatch.fnmatch(name, g) for g in globs)


def main():
    if os.path.exists(OUT):
        os.remove(OUT)

    included, secrets_skipped, big_files = [], [], []

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # prune unwanted directories in-place so os.walk doesn't descend
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            rel_dir = os.path.normpath(os.path.relpath(dirpath, ROOT))
            if any(rel_dir == p or rel_dir.startswith(p + os.sep)
                   for p in SKIP_REL_PREFIXES):
                continue

            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if os.path.abspath(full) == OUT:
                    continue                      # never zip the zip itself
                if is_secret(fn):
                    secrets_skipped.append(os.path.relpath(full, ROOT))
                    continue                      # SAFETY: never share secrets
                if matches(fn, SKIP_FILE_GLOBS):
                    continue

                arcname = os.path.relpath(full, ROOT)
                z.write(full, arcname)
                included.append(arcname)
                try:
                    if os.path.getsize(full) > 2 * 1024 * 1024:   # >2 MB
                        big_files.append((arcname, os.path.getsize(full)))
                except OSError:
                    pass

    size_mb = os.path.getsize(OUT) / 1024 / 1024
    print(f"\n  Created: {OUT}")
    print(f"  Size:    {size_mb:.2f} MB   ({len(included)} files)")

    if secrets_skipped:
        print("\n  SECRETS excluded (NOT in the zip — good):")
        for s in secrets_skipped:
            print(f"     - {s}")
    else:
        print("\n  No .env / secret files found to exclude.")

    if big_files:
        print("\n  Heads-up — files larger than 2 MB still inside:")
        for name, sz in big_files:
            print(f"     - {name}  ({sz/1024/1024:.1f} MB)")

    print("\n  Excluded: caches, venvs, node_modules, data/raw/*, "
          "*.zip, *.log, OS junk.")
    print("  Before sending: open the zip and double-check .env is NOT inside.\n")


if __name__ == "__main__":
    main()
