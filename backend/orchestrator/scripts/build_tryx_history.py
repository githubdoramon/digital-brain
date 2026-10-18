#!/usr/bin/env python3
"""Build the immutable Tryx history extension; no app/session data is accessed."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/tryx"


def run(*args: str, cwd: Path | None = None):
    subprocess.run(args, cwd=cwd, check=True)


def output(*args: str, cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=("dev", "release"), default="release")
    args = parser.parse_args()
    source = args.source_dir.resolve()
    wheels = args.wheel_dir.resolve()
    lock = json.loads((VENDOR / "manifest.json").read_text())
    patch = VENDOR / "history.patch"
    if hashlib.sha256(patch.read_bytes()).hexdigest() != lock["patch_sha256"]:
        raise SystemExit("Tryx patch checksum mismatch")
    if not source.exists():
        source.mkdir(parents=True)
        run("git", "init", str(source))
        run("git", "remote", "add", "origin", lock["repository"], cwd=source)
        run("git", "fetch", "--depth=1", "origin", lock["commit"], cwd=source)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=source)
    if output("git", "rev-parse", "HEAD", cwd=source) != lock["commit"]:
        raise SystemExit("Unexpected Tryx source revision")
    run("git", "config", "submodule.libs/whatsapp-rust.url", lock["rust_repository"], cwd=source)
    run("git", "submodule", "update", "--init", "--recursive", "--depth=1", cwd=source)
    if output("git", "rev-parse", "HEAD", cwd=source / "libs/whatsapp-rust") != lock["rust_commit"]:
        raise SystemExit("Unexpected whatsapp-rust source revision")
    # Reuse a local source directory/cache only when the exact patch is present.
    applied = (
        subprocess.run(
            ["git", "apply", "--reverse", "--check", str(patch)],
            cwd=source,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    if not applied:
        run("git", "apply", "--check", str(patch), cwd=source)
        run("git", "apply", str(patch), cwd=source)
    wheels.mkdir(parents=True, exist_ok=True)
    compatibility = ["--compatibility", "linux"] if sys.platform.startswith("linux") else []
    command = (
        [shutil.which("maturin")] if shutil.which("maturin") else [sys.executable, "-m", "maturin"]
    )
    run(
        *command,
        "build",
        *compatibility,
        "--locked",
        "--profile",
        args.profile,
        "--interpreter",
        sys.executable,
        "--out",
        str(wheels),
        cwd=source,
    )


if __name__ == "__main__":
    main()
