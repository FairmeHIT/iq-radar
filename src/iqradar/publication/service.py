from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Any, Sequence

from iqradar.config.loader import parse_price_config
from iqradar.metrics.aggregate import aggregate_runs
from iqradar.reporting.repository import FileDashboardRepository
from iqradar.schemas.run_record import RunRecord

PROJECTION_VERSION = "radar-v3"


@dataclass(frozen=True)
class Publication:
    snapshot_id: str
    source_job_id: str


class DashboardPublisher:
    """Publish deep-swe run records as an immutable dashboard snapshot.

    ``publish_records`` is called by the deep-swe service once a completed run
    has been imported into RunRecord JSONL. The dashboard only ever reflects
    these explicit snapshot activations.

    Since ``radar-v3`` a publication *accumulates*: the new records are merged
    with the current versioned snapshot's records (deduplicated by record
    ``run_id`` — deterministic ``<benchmark>__<task>__<model>__<effort>`` keys
    — newest ``created_at`` wins), so the dashboard grows across batches and
    benchmarks instead of being overwritten by each publish. Pass
    ``merge_with_current=False`` to publish a replacement snapshot built only
    from the given records.
    """

    def __init__(
        self,
        dashboard: FileDashboardRepository,
        prices_path: Path,
    ) -> None:
        self._dashboard = dashboard
        self._prices_path = prices_path

    def publish_records(
        self,
        run_id: str,
        records: list[RunRecord],
        *,
        merge_with_current: bool = True,
    ) -> Publication:
        if not records:
            raise ValueError("no records to publish")
        sources = [run_id]
        if merge_with_current:
            current = self._dashboard.load_snapshot(
                self._dashboard.current_snapshot_id()
            )
            if current is not None and current.runs:
                records = merge_records(current.runs, records)
                sources = _source_chain(self._dashboard, current.snapshot_id, run_id)
        content = "".join(f"{record.model_dump_json()}\n" for record in records)
        records_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        prices_content = self._prices_path.read_bytes()
        prices_digest = hashlib.sha256(prices_content).hexdigest()
        prices = parse_price_config(prices_content)
        identity = hashlib.sha256(
            f"{records_digest}:{prices_digest}:{PROJECTION_VERSION}".encode("ascii")
        ).hexdigest()
        snapshot_id = f"{run_id}-{identity[:16]}"
        summary = aggregate_runs(records, prices)
        self._dashboard.publish(
            snapshot_id,
            summary.model_dump(mode="json"),
            records,
            manifest={
                "source_job_id": run_id,
                "source_job_ids": sources,
                "records_sha256": records_digest,
                "prices_sha256": prices_digest,
                "projection_version": PROJECTION_VERSION,
                "published_at": datetime.now(UTC).isoformat(),
            },
        )
        return Publication(snapshot_id=snapshot_id, source_job_id=run_id)

    def publish_runs(
        self,
        run_ids: list[str],
        records: list[RunRecord],
        *,
        merge_with_current: bool = True,
    ) -> Publication:
        """Publish records from several runs as ONE accumulated snapshot.

        Mirrors :meth:`publish_records` but takes multiple source runs: the
        incoming records are merged with the current snapshot (when
        ``merge_with_current``) exactly as a single-run publish would, so the
        dashboard grows instead of being replaced. The single created snapshot
        carries every selected run in ``source_job_ids`` (for the
        "source-deleted" annotation) and gets a publication marker per run, so
        the test-page records list shows each selected run as published and a
        per-run unpublish resolves to this shared snapshot.
        """
        if not records:
            raise ValueError("no records to publish")
        if not run_ids:
            raise ValueError("no run ids to publish")
        # Deduplicate while preserving order; the primary source is the first.
        ordered_ids: list[str] = list(dict.fromkeys(run_ids))
        primary = ordered_ids[0]
        sources = list(ordered_ids)
        if merge_with_current:
            current = self._dashboard.load_snapshot(
                self._dashboard.current_snapshot_id()
            )
            if current is not None and current.runs:
                records = merge_records(current.runs, records)
                sources = _source_chain_multi(
                    self._dashboard, current.snapshot_id, ordered_ids
                )
        content = "".join(f"{record.model_dump_json()}\n" for record in records)
        records_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        prices_content = self._prices_path.read_bytes()
        prices_digest = hashlib.sha256(prices_content).hexdigest()
        prices = parse_price_config(prices_content)
        identity = hashlib.sha256(
            f"{records_digest}:{prices_digest}:{PROJECTION_VERSION}".encode("ascii")
        ).hexdigest()
        snapshot_id = f"{primary}-{identity[:16]}"
        summary = aggregate_runs(records, prices)
        manifest = {
            "source_job_id": primary,
            "source_job_ids": sources,
            "records_sha256": records_digest,
            "prices_sha256": prices_digest,
            "projection_version": PROJECTION_VERSION,
            "published_at": datetime.now(UTC).isoformat(),
        }
        # repository.publish writes the primary's marker; write one for every
        # other selected run so each shows as published on the test page.
        # Re-read the persisted manifest so per-run markers carry the same
        # digests the snapshot stored (summary/runs sha256 added inside
        # repository.publish).
        self._dashboard.publish(
            snapshot_id,
            summary.model_dump(mode="json"),
            records,
            manifest=manifest,
        )
        persisted_manifest = self._dashboard.snapshot_manifest(snapshot_id) or manifest
        for rid in ordered_ids:
            if rid == primary:
                continue  # already written by repository.publish
            self._dashboard.write_publication(rid, snapshot_id, persisted_manifest)
        return Publication(snapshot_id=snapshot_id, source_job_id=primary)

    def publication_for_job(self, run_id: str) -> Publication | None:
        data = self._dashboard.publication_for_job(run_id)
        return (
            Publication(snapshot_id=str(data["snapshot_id"]), source_job_id=run_id)
            if data
            else None
        )

    def delete_publication(self, run_id: str) -> dict[str, Any] | None:
        """回撤一个 run 的发布：删除包含该 run 来源的所有快照。

        radar-v3 快照会累积历史发布结果：后续快照可能继续携带该 run 的
        records，但该 run 的 publication marker 仍指向最初发布它的旧快照。
        因此仅删除 marker 指向的快照会让被删除/回撤的评测数据残留在当前
        大盘。这里按 manifest 的 source_job_ids 扫描并删除所有包含该 run
        的快照，确保回撤后大盘不再显示该来源数据。
        """
        removed = self._dashboard.delete_snapshots_for_sources([run_id])
        if not removed:
            return None
        return removed[0] if len(removed) == 1 else {"removed": removed, "current_snapshot_id": self._dashboard.current_snapshot_id()}


def merge_records(
    previous: Sequence[RunRecord],
    incoming: Sequence[RunRecord],
) -> list[RunRecord]:
    """Union two record sets keyed by ``run_id``, newest ``created_at`` wins.

    The result is ordered newest-first so ``runs[:limit]`` slices of a merged
    snapshot show the most recent records in the dashboard's 最近运行 table.
    """
    merged: dict[str, RunRecord] = {}
    for record in previous:
        merged[record.run_id] = record
    for record in incoming:
        existing = merged.get(record.run_id)
        if existing is None or _created_at(record) >= _created_at(existing):
            merged[record.run_id] = record
    return sorted(
        merged.values(),
        key=lambda record: (_created_at(record), record.run_id),
        reverse=True,
    )


def _created_at(record: RunRecord) -> datetime:
    value = record.created_at
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _source_chain(
    repository: FileDashboardRepository,
    current_snapshot_id: str,
    trigger_job_id: str,
) -> list[str]:
    """Source job ids carried forward from the current snapshot + the trigger.

    Falls back to the snapshot id's trigger segment (``<job>-<digest16>``)
    when its manifest is unreadable, so the chain never silently forgets the
    data already on the dashboard.
    """
    sources: list[str] = []
    manifest = repository.snapshot_manifest(current_snapshot_id)
    if manifest:
        raw = manifest.get("source_job_ids")
        if isinstance(raw, list):
            sources.extend(str(item) for item in raw if isinstance(item, str) and item)
        trigger = manifest.get("source_job_id")
        if isinstance(trigger, str) and trigger and trigger not in sources:
            sources.append(trigger)
    if not sources:
        # Manifest unreadable: recover the previous trigger from the id.
        fallback = current_snapshot_id.rsplit("-", 1)[0]
        if fallback and fallback != current_snapshot_id:
            sources.append(fallback)
    if trigger_job_id not in sources:
        sources.append(trigger_job_id)
    return sources


def _source_chain_multi(
    repository: FileDashboardRepository,
    current_snapshot_id: str,
    trigger_job_ids: list[str],
) -> list[str]:
    """Like :func:`_source_chain` but for a batch publish of several runs.

    Carries forward the current snapshot's sources and appends every selected
    run id (deduplicated, order preserved) so the published snapshot's
    ``source_job_ids`` lists every run whose records it contains.
    """
    sources: list[str] = []
    manifest = repository.snapshot_manifest(current_snapshot_id)
    if manifest:
        raw = manifest.get("source_job_ids")
        if isinstance(raw, list):
            sources.extend(str(item) for item in raw if isinstance(item, str) and item)
        trigger = manifest.get("source_job_id")
        if isinstance(trigger, str) and trigger and trigger not in sources:
            sources.append(trigger)
    if not sources:
        fallback = current_snapshot_id.rsplit("-", 1)[0]
        if fallback and fallback != current_snapshot_id:
            sources.append(fallback)
    for tid in trigger_job_ids:
        if tid not in sources:
            sources.append(tid)
    return sources
