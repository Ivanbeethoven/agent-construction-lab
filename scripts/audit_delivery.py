"""Check the original delivery manifest using only the standard library.

Exit 0 means all listed files match; exit 1 means files are missing or changed.
The original README is preserved in docs/ORIGINAL_README.md. Newly added
repository-maintenance files are outside the original delivery's scope.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def audit(root: Path, archive: Path | None = None) -> dict:
    root = root.resolve()
    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    missing, mismatches = [], []
    readme = root / "docs" / "ORIGINAL_README.md"
    mapping = {"README.md": "docs/ORIGINAL_README.md"} if readme.is_file() else {}
    matched = 0
    for name, expected in manifest["files"].items():
        path = (root / mapping.get(name, name)).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Manifest path escapes delivery root: {name!r}")
        if not path.is_file():
            missing.append(name)
            continue
        content = path.read_bytes()
        actual = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        if actual != {key: expected[key] for key in actual}:
            mismatches.append({"path": name, "expected": expected, "actual": actual})
        else:
            matched += 1
    result = {
        "scope": "Original delivery only; new repository files are excluded.",
        "release": manifest["release"],
        "manifest_sha256": hashlib.sha256((root / "PACKAGE_MANIFEST.json").read_bytes()).hexdigest(),
        "status": "complete" if not missing and not mismatches else "incomplete_or_modified",
        "expected_files": len(manifest["files"]),
        "present_files": len(manifest["files"]) - len(missing),
        "matching_files": matched,
        "missing_count": len(missing),
        "missing_by_directory": dict(sorted(Counter(name.split("/")[0] for name in missing).items())),
        "original_path_mapping": mapping,
        "missing_files": missing,
        "mismatched_files": mismatches,
    }
    if archive is not None:
        with zipfile.ZipFile(archive) as zipped:
            prefix = "agent-construction-lab/"
            entries = {item.filename[len(prefix):]: item for item in zipped.infolist()
                       if item.filename.startswith(prefix) and not item.is_dir()}
            result["archive_check"] = {
                "archive_name": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "project_file_entries": len(entries),
                "missing_files_present_in_archive": [name for name in missing if name in entries],
            }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path, help="Optional original ZIP")
    parser.add_argument("--output", type=Path, help="Write the full JSON report")
    args = parser.parse_args()
    result = audit(args.root, args.archive)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "status", "expected_files", "present_files", "matching_files", "missing_count", "missing_by_directory"
    )}, ensure_ascii=False, indent=2))
    print(f"Hash/size mismatches: {len(result['mismatched_files'])}")
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
