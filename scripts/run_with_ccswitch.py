"""Windows-to-WSL launcher using the explicitly selected CC Switch DeepSeek key.

The SQLite database is read-only. The key travels through a local stdin pipe,
never a command-line argument, saved configuration, or printed diagnostic.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path.home() / ".cc-switch" / "cc-switch.db")
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--python", required=True, help="Absolute WSL interpreter path")
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--study-config")
    parser.add_argument("--calibration-task")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    model = json.loads((root / args.model_config).read_text(encoding="utf-8"))
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT settings_config FROM providers WHERE id=? AND app_type=?",
                                  ("deepseek", "opencode")).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one CC Switch DeepSeek/OpenCode provider")
    provider = json.loads(rows[0][0])
    endpoint = provider["options"]["baseURL"].rstrip("/")
    if endpoint != "https://api.deepseek.com/v1" or model["base_url"].rstrip("/") != endpoint:
        raise ValueError("Configured experiment endpoint must match the selected official DeepSeek endpoint")
    key = provider["options"]["apiKey"]
    if not isinstance(key, str) or not key:
        raise ValueError("Selected provider has no API key")
    prefix = ["wsl", "--distribution", args.distribution]
    wsl_root = subprocess.check_output(prefix + ["--exec", "wslpath", "-a", str(root)], text=True).strip()
    command = prefix + ["--cd", wsl_root, "--exec", args.python, "scripts/run_appworld_live.py",
        "--credentials-stdin", "--model-config", args.model_config, "--appworld-root", args.appworld_root,
        "--out", args.out]
    for field in ("manifest", "manifest_sha256", "study_config", "calibration_task"):
        value = getattr(args, field)
        if value:
            command.extend(["--" + field.replace("_", "-"), value])
    # Child stdout/stderr contain study diagnostics only; the key is never echoed.
    return subprocess.run(command, input=json.dumps({"api_key": key}), text=True).returncode


if __name__ == "__main__":
    raise SystemExit(main())
