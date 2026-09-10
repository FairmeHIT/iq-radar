from __future__ import annotations

import json
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from iqradar.schemas.run_record import RunRecord
from iqradar.shared.atomic_files import atomic_write_text, fsync_directory


@dataclass(frozen=True)
class DashboardSnapshot:
    snapshot_id: str
    summary: dict[str, Any]
    runs: tuple[RunRecord, ...]


class FileDashboardRepository:
    def __init__(
        self,
        root: Path,
        *,
        legacy_summary_path: Path | None = None,
        legacy_runs_path: Path | None = None,
    ) -> None:
        self.root = root
        self._legacy_summary_path = legacy_summary_path
        self._legacy_runs_path = legacy_runs_path

    def publish(
        self,
        snapshot_id: str,
        summary: dict[str, Any],
        records: Iterable[RunRecord],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
            raise ValueError("invalid dashboard snapshot ID")
        records = tuple(records)
        summary_content = json.dumps(summary, ensure_ascii=True, indent=2) + "\n"
        runs_content = "".join(f"{record.model_dump_json()}\n" for record in records)
        manifest_document = {
            **(manifest or {}),
            "summary_sha256": _sha256_text(summary_content),
            "snapshot_runs_sha256": _sha256_text(runs_content),
        }
        snapshot_root = self.root / "snapshots" / snapshot_id
        if not snapshot_root.exists():
            temporary_root = snapshot_root.with_name(f".{snapshot_id}.{uuid4().hex}.tmp")
            temporary_root.mkdir(parents=True)
            temporary_root.chmod(0o700)
            try:
                atomic_write_text(
                    temporary_root / "summary.json",
                    summary_content,
                )
                atomic_write_text(
                    temporary_root / "runs.jsonl",
                    runs_content,
                )
                atomic_write_text(
                    temporary_root / "manifest.json",
                    json.dumps(manifest_document, ensure_ascii=True, indent=2) + "\n",
                )
                try:
                    temporary_root.replace(snapshot_root)
                    fsync_directory(snapshot_root.parent)
                except OSError:
                    if not snapshot_root.exists():
                        raise
            finally:
                if temporary_root.exists():
                    shutil.rmtree(temporary_root)
        self._validate_snapshot(snapshot_root)
        self._validate_snapshot_identity(snapshot_root, manifest_document)
        atomic_write_text(
            self.root / "current.json",
            json.dumps({"snapshot_id": snapshot_id}, ensure_ascii=True) + "\n",
        )
        source_job_id = manifest.get("source_job_id") if manifest else None
        if isinstance(source_job_id, str):
            self.write_publication(source_job_id, snapshot_id, manifest_document)

    def write_publication(
        self,
        job_id: str,
        snapshot_id: str,
        manifest_document: dict[str, Any],
    ) -> None:
        """Write (or overwrite) the ``publications/<job_id>.json`` marker.

        Batch publishing one snapshot from several runs writes a marker per run
        so the test-page records list shows each as published and per-run
        unpublish resolves to the shared snapshot. Newest snapshot wins on
        overwrite, matching the accumulated-publish semantics.
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id):
            raise ValueError("invalid evaluation job ID")
        atomic_write_text(
            self.root / "publications" / f"{job_id}.json",
            json.dumps(
                {"snapshot_id": snapshot_id, **manifest_document},
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        )

    def publication_for_job(self, job_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id):
            raise ValueError("invalid evaluation job ID")
        path = self.root / "publications" / f"{job_id}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def snapshot_manifest(self, snapshot_id: str) -> dict[str, Any] | None:
        """Manifest of one published snapshot, or None when unreadable."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
            return None
        path = self.root / "snapshots" / snapshot_id / "manifest.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def current_snapshot_id(self) -> str | None:
        current_path = self.root / "current.json"
        if not current_path.is_file():
            return None
        data = json.loads(current_path.read_text(encoding="utf-8"))
        snapshot_id = data.get("snapshot_id") if isinstance(data, dict) else None
        if snapshot_id is None:
            return None
        if not isinstance(snapshot_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id
        ):
            raise ValueError("invalid current dashboard snapshot ID")
        return snapshot_id

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List all published snapshots with lightweight metadata.

        Returns a list of ``{snapshot_id, published_at, models, tasks_total}``
        sorted by ``published_at`` descending (newest first). Snapshots whose
        manifest or summary are unreadable are skipped.
        """
        snapshots_root = self.root / "snapshots"
        if not snapshots_root.is_dir():
            return []
        current = self.current_snapshot_id()
        result: list[dict[str, Any]] = []
        for entry in snapshots_root.iterdir():
            if not entry.is_dir():
                continue
            snapshot_id = entry.name
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
                continue
            manifest_path = entry / "manifest.json"
            summary_path = entry / "summary.json"
            if not manifest_path.is_file() or not summary_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict) or not isinstance(summary, dict):
                continue
            summaries = summary.get("summaries")
            if not isinstance(summaries, list):
                summaries = []
            models = sorted(
                dict.fromkeys(
                    str(s.get("model"))
                    for s in summaries
                    if isinstance(s, dict) and isinstance(s.get("model"), str)
                )
            )
            tasks_total = sum(
                int(s["tasks_total"])
                for s in summaries
                if isinstance(s, dict) and isinstance(s.get("tasks_total"), (int, float))
            )
            result.append(
                {
                    "snapshot_id": snapshot_id,
                    "published_at": manifest.get("published_at") or "",
                    "source_job_id": manifest.get("source_job_id") or "",
                    # 累积快照的全部来源（单源快照退化为 [source_job_id]），
                    # 前端/服务用它判断"源记录是否仍存在于测试页"。
                    "source_job_ids": _manifest_source_ids(manifest),
                    "models": models,
                    "tasks_total": tasks_total,
                    "is_current": snapshot_id == current,
                }
            )
        result.sort(key=lambda s: s.get("published_at", ""), reverse=True)
        return result

    def delete_snapshots_for_sources(self, source_job_ids: list[str]) -> list[dict[str, Any]]:
        """Remove every snapshot whose manifest includes any source job id.

        This is used when evaluation records are deleted from the test page: an
        accumulated dashboard snapshot can carry records from older source runs
        even when that run's own publication marker still points at an earlier
        snapshot. Scanning snapshot manifests prevents deleted-source data from
        remaining visible on the dashboard.
        """
        sources = {
            source
            for source in source_job_ids
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", source)
        }
        if not sources:
            return []
        removed: list[dict[str, Any]] = []
        for entry in list(self.list_snapshots()):
            snapshot_sources = {
                str(item)
                for item in (entry.get("source_job_ids") or [entry.get("source_job_id")])
                if item
            }
            if sources.isdisjoint(snapshot_sources):
                continue
            result = self.delete_snapshot(str(entry["snapshot_id"]))
            if result is not None:
                removed.append(result)
        return removed

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """Remove one published snapshot and repair the pointers referencing it.

        Returns ``{"snapshot_id", "source_job_id", "current_snapshot_id"}`` for
        the removed snapshot, or ``None`` when no such snapshot exists. When the
        removed snapshot was the current one, ``current.json`` is repointed at
        the newest remaining snapshot (or removed when none are left). Every
        ``publications/<source_job_id>.json`` marker still pointing at the
        deleted snapshot is dropped (including per-run markers written by batch
        publishing) — a re-published source keeps its newer mapping.
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
            raise ValueError("invalid dashboard snapshot ID")
        snapshot_root = self.root / "snapshots" / snapshot_id
        if not snapshot_root.is_dir():
            return None
        source_job_id = ""
        manifest_path = snapshot_root / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = None
            if isinstance(manifest, dict) and isinstance(
                manifest.get("source_job_id"), str
            ):
                source_job_id = manifest["source_job_id"]
        shutil.rmtree(snapshot_root)
        if self.current_snapshot_id() == snapshot_id:
            self._repoint_current_after_removal()
        if source_job_id and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", source_job_id
        ):
            publication_path = self.root / "publications" / f"{source_job_id}.json"
            if publication_path.is_file():
                try:
                    data = json.loads(publication_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if not isinstance(data, dict) or data.get("snapshot_id") == snapshot_id:
                    publication_path.unlink(missing_ok=True)
        # Batch-published snapshots carry a publication marker per source run
        # (not just the manifest's primary source_job_id), so scan every marker
        # and drop any still pointing at the removed snapshot. A re-published
        # source keeps its newer mapping (pointing elsewhere) intact.
        self._remove_stale_publication_markers(snapshot_id)
        return {
            "snapshot_id": snapshot_id,
            "source_job_id": source_job_id,
            "current_snapshot_id": self.current_snapshot_id(),
        }

    def _remove_stale_publication_markers(self, snapshot_id: str) -> None:
        """Delete every ``publications/*.json`` still pointing at *snapshot_id*."""
        publications_root = self.root / "publications"
        if not publications_root.is_dir():
            return
        for entry in publications_root.iterdir():
            if not entry.is_file() or not entry.name.endswith(".json"):
                continue
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("snapshot_id") == snapshot_id:
                entry.unlink(missing_ok=True)

    def _repoint_current_after_removal(self) -> None:
        current_path = self.root / "current.json"
        newest_id: str | None = None
        newest_at = ""
        snapshots_root = self.root / "snapshots"
        if snapshots_root.is_dir():
            for entry in snapshots_root.iterdir():
                if not entry.is_dir():
                    continue
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", entry.name):
                    continue
                manifest_path = entry / "manifest.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(manifest, dict):
                    continue
                published_at = str(manifest.get("published_at") or "")
                if published_at >= newest_at:
                    newest_at = published_at
                    newest_id = entry.name
        if newest_id is None:
            current_path.unlink(missing_ok=True)
        else:
            atomic_write_text(
                current_path,
                json.dumps({"snapshot_id": newest_id}, ensure_ascii=True) + "\n",
            )

    def load_summary(self) -> dict[str, Any] | None:
        snapshot = self.load_snapshot()
        return snapshot.summary if snapshot else None

    def load_runs(self) -> list[RunRecord]:
        snapshot = self.load_snapshot()
        return list(snapshot.runs) if snapshot else []

    def load_snapshot(self, snapshot_id: str | None = None) -> DashboardSnapshot | None:
        if snapshot_id is None:
            snapshot_id = self.current_snapshot_id()
        if snapshot_id:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", snapshot_id):
                raise ValueError("invalid dashboard snapshot ID")
            snapshot_root = self.root / "snapshots" / snapshot_id
            self._validate_snapshot(snapshot_root)
            summary_path = snapshot_root / "summary.json"
            runs_path = snapshot_root / "runs.jsonl"
        else:
            summary_path = self._legacy_summary_path
            runs_path = self._legacy_runs_path
        if summary_path is None or not summary_path.is_file():
            return None
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        paths = sorted(runs_path.glob("*.jsonl")) if runs_path and runs_path.is_dir() else [runs_path]
        records = tuple(
            RunRecord.model_validate_json(line)
            for raw_path in paths
            if raw_path is not None and raw_path.is_file()
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        return DashboardSnapshot(snapshot_id or "legacy", data, records)

    @staticmethod
    def _validate_snapshot(snapshot_root: Path) -> None:
        required = ("summary.json", "runs.jsonl", "manifest.json")
        if not snapshot_root.is_dir() or any(
            not (snapshot_root / filename).is_file() for filename in required
        ):
            raise ValueError("dashboard snapshot is incomplete")
        manifest = json.loads((snapshot_root / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("dashboard snapshot manifest is invalid")
        for filename, digest_key in (
            ("summary.json", "summary_sha256"),
            ("runs.jsonl", "snapshot_runs_sha256"),
        ):
            expected = manifest.get(digest_key)
            if isinstance(expected, str) and hashlib.sha256(
                (snapshot_root / filename).read_bytes()
            ).hexdigest() != expected:
                raise ValueError(f"dashboard snapshot {filename} digest mismatch")

    @staticmethod
    def _validate_snapshot_identity(
        snapshot_root: Path,
        expected_manifest: dict[str, Any],
    ) -> None:
        existing = json.loads((snapshot_root / "manifest.json").read_text(encoding="utf-8"))
        identity_keys = (
            "source_job_id",
            "records_sha256",
            "prices_sha256",
            "projection_version",
        )
        if not isinstance(existing, dict) or any(
            existing.get(key) != expected_manifest.get(key) for key in identity_keys
        ):
            raise ValueError("dashboard snapshot identity does not match its immutable inputs")


def _manifest_source_ids(manifest: dict[str, Any]) -> list[str]:
    """All source job ids of a snapshot (radar-v3 merged snapshots have many).

    Falls back to the single legacy ``source_job_id`` for radar-v2 snapshots.
    """
    raw = manifest.get("source_job_ids")
    ids: list[str] = []
    if isinstance(raw, list):
        ids = [str(item) for item in raw if isinstance(item, str) and item]
    if not ids:
        single = manifest.get("source_job_id")
        if isinstance(single, str) and single:
            ids = [single]
    return ids


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
