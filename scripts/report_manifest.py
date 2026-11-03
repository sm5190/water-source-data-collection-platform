"""Manifests for generated evaluation outputs kept out of Git (reports/README.md).

    uv run python scripts/report_manifest.py write <report_dir> [...]   # new manifest.json each
    uv run python scripts/report_manifest.py check <report_dir> [...]   # sha256 still match?

A report directory's generated files (JSON, JSONL, logs: ignored by .gitignore) stay on disk
where the scripts wrote them. ``manifest.json`` lists each of them under the directory with
its size, sha256 and modification time, plus the git commit at the time of writing, so a
local or archived copy can be checked against the report that used it. Files are only read;
an existing manifest is never overwritten (``write`` refuses). Run ids, commands and settings
are in the report Markdown next to the manifest.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = "manifest.json"

def _ignored(paths: list[Path]) -> set[Path]:
    """Paths git ignores (the generated outputs)."""
    if not paths:
        return set()
    out = subprocess.run(["git", "check-ignore", "--stdin", "-z"],
                         input="\0".join(str(p) for p in paths), capture_output=True,
                         text=True, check=False).stdout
    return {Path(p) for p in out.split("\0") if p}

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def generated_files(directory: Path) -> list[Path]:
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != MANIFEST)
    ignored = _ignored(files)
    return [p for p in files if p in ignored]

def write(directory: Path) -> Path:
    target = directory / MANIFEST
    if target.exists():
        raise SystemExit(f"{target} exists; not overwritten")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip()
    entries = []
    for p in generated_files(directory):
        st = p.stat()
        entries.append({"path": p.relative_to(directory).as_posix(), "bytes": st.st_size,
                        "sha256": _sha256(p),
                        "modified": datetime.fromtimestamp(st.st_mtime, UTC)
                        .isoformat(timespec="seconds")})
    target.write_text(json.dumps({
        "directory": directory.as_posix(),
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": commit,
        "note": ("Generated outputs kept out of Git (see reports/README.md). Run ids, "
                 "commands and settings are in the Markdown reports in this directory."),
        "files": entries}, indent=1) + "\n", encoding="utf-8")
    return target

def check(directory: Path) -> list[str]:
    data = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    problems = []
    for e in data["files"]:
        p = directory / e["path"]
        if not p.exists():
            problems.append(f"missing {p}")
        elif _sha256(p) != e["sha256"]:
            problems.append(f"changed {p}")
    return problems

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["write", "check"])
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args()
    bad = 0
    for d in args.dirs:
        if args.cmd == "write":
            print(f"{write(d)}: {len(json.loads((d / MANIFEST).read_text())['files'])} files")
        else:
            problems = check(d)
            bad += len(problems)
            print(f"{d}: {'OK' if not problems else '; '.join(problems)}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
