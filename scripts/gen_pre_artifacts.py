#!/usr/bin/env python3
"""Generate pre_artifacts.sh for deep-swe tasks that only declare
``[[verifier.collect]]`` in task.toml.

Background: the deep-swe task dataset synced into ``checkouts/deep-swe/tasks``
declares the model-patch capture command under ``[[verifier.collect]]``
(schema 1.3). The locally installed pier 0.3.0 has no ``collect`` field in
``VerifierConfig``, so pydantic silently drops it: the command never runs,
``/logs/artifacts/model.patch`` never exists, and the separate verifier
container grades the pristine base state — reward 0 by construction for every
model and every task (all F2P fail while P2P passes, hence IQ = 0).

pier 0.3.0 DOES run the task's ``pre_artifacts.sh`` (trial.py:
``_run_pre_artifacts_script``) right before artifact collection, which is
exactly the hook the older vendored task generation used for the same purpose.
This script extracts each task's collect command verbatim and wraps it into a
``pre_artifacts.sh`` equivalent to the old template.

Idempotent: tasks that already have pre_artifacts.sh are left untouched.
"""

from __future__ import annotations

import argparse
import os
import stat
import tomllib
from pathlib import Path

TEMPLATE = """\
#!/bin/bash
# Auto-generated from task.toml [[verifier.collect]] by
# scripts/gen_pre_artifacts.py — pier 0.3.0 ignores verifier.collect, so the
# patch capture must live in this pre-artifacts hook instead.
# Capture the agent's committed work as the submission artifact: the diff
# between the starting commit and the agent's final HEAD.
set -uo pipefail
{commands}

echo "[pre_artifacts] captured $(wc -c < /logs/artifacts/model.patch 2>/dev/null || echo 0) bytes"
"""


def generate(tasks_root: Path, dry_run: bool) -> int:
    written = skipped_existing = skipped_no_collect = 0
    for task_dir in sorted(p for p in tasks_root.iterdir() if p.is_dir()):
        task_toml = task_dir / "task.toml"
        if not task_toml.exists():
            continue
        data = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        collect = (data.get("verifier") or {}).get("collect") or []
        commands = [c["command"] for c in collect if c.get("command")]
        if not commands:
            skipped_no_collect += 1
            continue
        target = task_dir / "pre_artifacts.sh"
        if target.exists():
            skipped_existing += 1
            continue
        body = TEMPLATE.format(
            commands="\n".join(f"{cmd} || true" for cmd in commands)
        )
        if dry_run:
            print(f"[dry-run] would write {target}")
        else:
            target.write_text(body, encoding="utf-8")
            os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print(f"wrote {target}")
        written += 1

    print(
        f"\n{tasks_root}: generated={written} "
        f"skipped_existing={skipped_existing} "
        f"skipped_no_collect={skipped_no_collect}"
    )
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_tasks = repo_root / "checkouts" / "deep-swe" / "tasks"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tasks_root",
        nargs="?",
        type=Path,
        default=default_tasks,
        help=f"Path to the deep-swe tasks directory (default: {default_tasks})",
    )
    parser.add_argument("--dry-run", action="store_true", help="List targets without writing")
    args = parser.parse_args()
    return generate(args.tasks_root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
