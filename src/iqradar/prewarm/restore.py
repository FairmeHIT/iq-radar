from __future__ import annotations

import subprocess
from pathlib import Path

BACKUP_DIR = Path("data/prewarm/images")


def _image_exists(tag: str) -> bool:
    """Check if a Docker image tag exists locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def ensure_image(tag: str, backup_dir: Path = BACKUP_DIR) -> bool:
    """Ensure a Docker image is available locally, loading from backup if
    necessary.  Returns True once the image is present (whether it was already
    there or was restored from backup).

    The backup tar is expected at ``<backup_dir>/<safe_name>.tar.gz`` where
    ``safe_name`` = ``tag`` with ``/`` → ``_`` and ``:`` → ``_``, matching
    the naming convention of ``scripts/backup_images.sh``.
    """
    if _image_exists(tag):
        return True
    safe_name = tag.replace("/", "_").replace(":", "_")
    tar_path = backup_dir / f"{safe_name}.tar.gz"
    if not tar_path.is_file():
        return False
    result = subprocess.run(
        ["docker", "load", "-i", str(tar_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        return False
    # The loaded image may have a different tag than requested (e.g. a
    # prewarm image was saved under iqradar-prewarm/<task> but the
    # caller wants the ECR tag).  If the exact tag still isn't present,
    # try to find the newly loaded tag and retag it.
    if _image_exists(tag):
        return True
    for line in result.stdout.splitlines():
        if line.startswith("Loaded image: "):
            loaded_tag = line.removeprefix("Loaded image: ").strip()
            if loaded_tag and loaded_tag != tag:
                subprocess.run(
                    ["docker", "tag", loaded_tag, tag],
                    capture_output=True,
                    timeout=10,
                )
                return _image_exists(tag)
    return False


def ensure_images(tags: list[str], backup_dir: Path = BACKUP_DIR) -> int:
    """Ensure all listed Docker images are available locally.
    Returns the number of images that were restored from backup.
    """
    restored = 0
    for tag in tags:
        if not _image_exists(tag) and ensure_image(tag, backup_dir):
            restored += 1
    return restored