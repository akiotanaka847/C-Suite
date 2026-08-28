"""Smoke tests for the external-condition monitoring pipeline (PR-A).

Covers: the store migration, watchlist CRUD, signal insert with dedup,
the suppression cascade (dry_run / below_floor), and the heartbeat
bootstrap/chain. Adapter-level network behaviour is stubbed — the
``vendor_status`` adapter has no logic we can meaningfully unit-test
without touching live Atom feeds, so a small fake Source covers the
pipeline orchestration code instead.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openexecutive.alerts.models import AlertSeverity
from openexecutive.alerts.store import initialize_db as initialize_alerts_db
from openexecutive.memory.episodic import initialize_db as initialize_episodic_db
from openexecutive.monitoring import pipeline as mp
from openexecutive.monitoring import store as ms
from openexecutive.monitoring.models import (
    MODE_ACTIVE,
    MODE_DRY_RUN,
    OUTCOME_ALERTED,
    OUTCOME_SUPPRESSED_BELOW_FLOOR,
    OUTCOME_SUPPRESSED_DRY_RUN,
    Signal,
    WatchlistItem,
)
from openexecutive.monitoring.sources import _REGISTRY as SOURCE_REGISTRY

# --------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------- #


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test SQLite file with all three module schemas initialised."""
    db_path = tmp_path / "test_monitoring.db"
    # Several store modules read DB_PATH dynamically via _resolve_db_path.
    # Pin every one of them to the same tmp file so a write here is a
    # read there.
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db_path)
    monkeypatch.setattr("openexecutive.alerts.store.DB_PATH", db_path)
    initialize_episodic_db(db_path)
    initialize_alerts_db(db_path)
    ms.initialize_db(db_path)
    return db_path


class _FakeSource:
    """Stub Source that returns a fixed list of Signals — no network."""

    kind: str = "vendor_status"  # reuse a registered kind for routing
    default_poll_interval_minutes: int = 5

    def __init__(self, signals: list[Signal]) -> None:
        self._signals = signals
        self.calls = 0

    async def poll(
        self, item: WatchlistItem, *, db_path: Path | None = None
    ) -> list[Signal]:
        self.calls += 1
        # Bind watchlist_id at poll time so the test doesn't have to
        # know the row id at construction time.
        return [s.model_copy(update={"watchlist_id": item.id or 0}) for s in self._signals]

    def matches_trigger(self, signal: Signal, item: WatchlistItem) -> bool:
        return True


@pytest.fixture
def install_fake_source(monkeypatch: pytest.MonkeyPatch):
    """Swap the live vendor_status adapter for a stub.

    Yields a setter; the test calls ``set(signals)`` to install a stub
    that returns those signals on poll. Restored on teardown.
    """
    original = SOURCE_REGISTRY.get("vendor_status")
    installed: list[_FakeSource] = []

    def _set(signals: list[Signal]) -> _FakeSource:
        fake = _FakeSource(signals)
        SOURCE_REGISTRY["vendor_status"] = fake  # type: ignore[assignment]
        installed.append(fake)
        return fake

    yield _set

    if original is not None:
        SOURCE_REGISTRY["vendor_status"] = original
    elif "vendor_status" in SOURCE_REGISTRY:
        del SOURCE_REGISTRY["vendor_status"]


def _make_signal(
    *,
    dedup_key: str = "vendor_status:abc",
    severity: AlertSeverity = AlertSeverity.HIGH,
    summary: str = "Test incident",
) -> Signal:
    return Signal(
        watchlist_id=0,  # set by _FakeSource at poll time
        source_kind="vendor_status",
        source_external_id="upstream-1",
        captured_at=datetime.now(UTC).isoformat(),
        normalized_summary=summary,
        raw_payload={"source": "fake"},
        provenance_url="https://example.com/incident/1",
        severity_hint=severity,
        dedup_key=dedup_key,
    )


# --------------------------------------------------------------------- #
# Watchlist CRUD
# --------------------------------------------------------------------- #


def test_initialize_db_is_idempotent(db: Path) -> None:
    ms.initialize_db(db)
    ms.initialize_db(db)
    assert ms.list_watchlist(db_path=db) == []


def test_insert_and_get_watchlist_item(db: Path) -> None:
    item_id = ms.insert_watchlist_item(
        slug="vendor-aws",
        signal_type="vendor_status",
        target="https://status.aws.amazon.com/rss/all.rss",
        config={"vendor_label": "AWS"},
        cadence="5min",
        severity_floor=AlertSeverity.MEDIUM,
        route_to_specialist="coo",
        db_path=db,
    )
    assert item_id > 0
    item = ms.get_watchlist_item(item_id, db_path=db)
    assert item is not None
    assert item.slug == "vendor-aws"
    assert item.signal_type == "vendor_status"
    assert item.target.startswith("https://")
    assert item.config_json == {"vendor_label": "AWS"}
    assert item.severity_floor == AlertSeverity.MEDIUM
    assert item.mode == MODE_ACTIVE
    assert item.enabled is True
    assert item.fired_count == 0


def test_insert_rejects_unknown_mode(db: Path) -> None:
    with pytest.raises(ValueError, match="Unknown watchlist mode"):
        ms.insert_watchlist_item(
            slug="bad-mode",
            signal_type="vendor_status",
            target="https://example.com",
            mode="forever",
            db_path=db,
        )


def test_signal_insert_dedupes_on_key_clash(db: Path) -> None:
    wl_id = ms.insert_watchlist_item(
        slug="x", signal_type="vendor_status",
        target="https://example.com", db_path=db,
    )
    sig = _make_signal(dedup_key="vendor_status:same")
    sig = sig.model_copy(update={"watchlist_id": wl_id})
    first = ms.insert_signal(sig, db_path=db)
    second = ms.insert_signal(sig, db_path=db)
    assert first is not None
    assert second is None  # UNIQUE clash returns None, not exception


# --------------------------------------------------------------------- #
# Scan orchestration — the heart of the pipeline
# --------------------------------------------------------------------- #


def test_scan_promotes_signal_to_alert(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: enabled watchlist → fake adapter → signal → promotion."""
    promoted: list[Any] = []
    # Stub alerts.schedule_evaluation so we don't fire a real triage LLM.
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: promoted.append(event),
    )

    wl_id = ms.insert_watchlist_item(
        slug="vendor-aws",
        signal_type="vendor_status",
        target="https://status.aws.amazon.com/rss/all.rss",
        severity_floor=AlertSeverity.MEDIUM,
        db_path=db,
    )
    install_fake_source([_make_signal()])

    written = asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert written == 1
    assert len(promoted) == 1
    assert promoted[0].source == "vendor_status"
    assert promoted[0].external_id == "vendor_status:abc"

    # Signal row was marked alerted; watchlist fired_count was bumped.
    signals = ms.list_recent_signals(db_path=db)
    assert len(signals) == 1
    assert signals[0]["processed_outcome"] == OUTCOME_ALERTED
    item = ms.get_watchlist_item(wl_id, db_path=db)
    assert item is not None
    assert item.fired_count == 1
    assert item.last_polled_at is not None
    assert item.last_fired_at is not None


def test_dry_run_mode_suppresses_promotion(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted: list[Any] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: promoted.append(event),
    )

    ms.insert_watchlist_item(
        slug="dry-aws", signal_type="vendor_status",
        target="https://example.com", mode=MODE_DRY_RUN, db_path=db,
    )
    install_fake_source([_make_signal()])

    written = asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert written == 1  # row IS written
    assert promoted == []  # but NOT promoted to alerts

    signals = ms.list_recent_signals(db_path=db)
    assert signals[0]["processed_outcome"] == OUTCOME_SUPPRESSED_DRY_RUN


def test_severity_floor_suppresses_low_signals(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted: list[Any] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: promoted.append(event),
    )

    ms.insert_watchlist_item(
        slug="strict", signal_type="vendor_status",
        target="https://example.com",
        severity_floor=AlertSeverity.URGENT,  # only URGENT surfaces
        db_path=db,
    )
    # Adapter says HIGH; floor is URGENT → clamp_severity keeps HIGH
    # (floor is the LOWER bound, ceiling is the UPPER bound — a HIGH
    # signal is BELOW an URGENT floor, so it should be suppressed)
    install_fake_source([_make_signal(severity=AlertSeverity.HIGH)])

    written = asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert written == 1
    assert promoted == []  # below floor → no alert
    signals = ms.list_recent_signals(db_path=db)
    assert signals[0]["processed_outcome"] == OUTCOME_SUPPRESSED_BELOW_FLOOR


def test_disabled_watchlist_skipped(
    db: Path,
    install_fake_source,
) -> None:
    ms.insert_watchlist_item(
        slug="off", signal_type="vendor_status",
        target="https://example.com", enabled=False, db_path=db,
    )
    fake = install_fake_source([_make_signal()])
    written = asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert written == 0
    assert fake.calls == 0


def test_dedup_key_collision_is_silent(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second scan tick with the same upstream event is a no-op."""
    promoted: list[Any] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: promoted.append(event),
    )

    ms.insert_watchlist_item(
        slug="vendor", signal_type="vendor_status",
        target="https://example.com", db_path=db,
    )
    install_fake_source([_make_signal(dedup_key="vendor_status:stable")])

    # First tick: row written, alert promoted
    asyncio.run(mp.run_external_monitor_scan(db_path=db))
    # Reset poll cadence so the next scan re-polls — without this the
    # _due_for_poll guard would skip it on the second tick because
    # last_polled_at is fresher than the cadence floor.
    wl_items = ms.list_watchlist(db_path=db)
    ms.mark_polled(wl_items[0].id or 0, datetime(2000, 1, 1, tzinfo=UTC), db_path=db)

    # Second tick: adapter returns same dedup_key — UNIQUE clash → no new row
    asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert len(promoted) == 1  # not 2
    assert len(ms.list_recent_signals(db_path=db)) == 1  # still 1


# --------------------------------------------------------------------- #
# Heartbeat lifecycle
# --------------------------------------------------------------------- #


def test_bootstrap_is_idempotent(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bootstrap_external_monitor_scan reads DB_PATH via the
    # _heartbeat_pending → episodic._get_conn path; the fixture already
    # monkey-patched episodic.DB_PATH so this works.
    id_1 = mp.bootstrap_external_monitor_scan()
    id_2 = mp.bootstrap_external_monitor_scan()
    assert id_1 is not None
    assert id_2 is None  # second call: already pending → no-op


# --------------------------------------------------------------------- #
# Regressions for findings from adversarial review
# --------------------------------------------------------------------- #


def test_ceiling_caps_signal_severity_hint(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URGENT-emitting adapter on a row with ceiling=MEDIUM lands in
    external_signals with severity_hint='medium'."""
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: None,
    )
    ms.insert_watchlist_item(
        slug="cap-test", signal_type="vendor_status",
        target="https://example.com",
        severity_ceiling=AlertSeverity.MEDIUM,
        db_path=db,
    )
    install_fake_source([_make_signal(severity=AlertSeverity.URGENT)])
    asyncio.run(mp.run_external_monitor_scan(db_path=db))
    rows = ms.list_recent_signals(db_path=db)
    assert rows[0]["severity_hint"] == "medium"


def test_schedule_evaluation_failure_records_failed_outcome(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If schedule_evaluation raises, the signal must NOT be recorded as
    'alerted' — that would silently lose signals during an alerts
    outage."""
    def boom(event: Any) -> None:
        raise RuntimeError("triage pipeline down")
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation", boom
    )
    wl_id = ms.insert_watchlist_item(
        slug="failtest", signal_type="vendor_status",
        target="https://example.com", db_path=db,
    )
    install_fake_source([_make_signal()])
    asyncio.run(mp.run_external_monitor_scan(db_path=db))
    rows = ms.list_recent_signals(db_path=db)
    assert rows[0]["processed_outcome"] == "failed"
    # And the watchlist's fired_count should NOT have been bumped.
    item = ms.get_watchlist_item(wl_id, db_path=db)
    assert item is not None
    assert item.fired_count == 0


def test_max_signals_per_scan_cap_enforced(
    db: Path,
    install_fake_source,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cost guard — exceeding max_signals_per_scan stops writes mid-list."""
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.schedule_evaluation",
        lambda event: None,
    )
    # get_settings() constructs a fresh Settings each call — patching
    # an instance attribute wouldn't survive the next call. Swap the
    # whole getter for a thunk returning a low-cap object.
    from openexecutive.config import get_settings
    base = get_settings()
    monkeypatch.setattr(
        "openexecutive.monitoring.pipeline.get_settings",
        lambda: base.model_copy(update={"external_monitor_max_signals_per_scan": 2}),
    )

    ms.insert_watchlist_item(
        slug="bulky", signal_type="vendor_status",
        target="https://example.com", db_path=db,
    )
    # Five distinct signals — cap=2 means only 2 land.
    install_fake_source([
        _make_signal(dedup_key=f"vendor_status:n{i}") for i in range(5)
    ])
    written = asyncio.run(mp.run_external_monitor_scan(db_path=db))
    assert written == 2
    rows = ms.list_recent_signals(db_path=db)
    assert len(rows) == 2


def test_validate_target_url_rejects_ssrf_targets() -> None:
    """SSRF guard catches the classic pivot targets without a network."""
    from openexecutive.monitoring.sources._http import validate_target_url as _validate_target_url
    bad = [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "http://localhost/anything",
        "http://127.0.0.1/whatever",
        # IMDS — link-local 169.254.0.0/16
        "http://169.254.169.254/latest/meta-data/",
        # RFC1918
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        # No host
        "http:///foo",
    ]
    for url in bad:
        ok, reason = _validate_target_url(url)
        assert ok is False, f"SSRF guard let through: {url!r}"
        assert reason  # has a human-readable reason


def test_signal_to_alert_event_carries_slug_and_severity_for_triage() -> None:
    """Triage prompt's external-signals rules read `Watchlist: <slug>`
    and `Severity hint: <hint>` from the body — guarantee they land
    there as labeled lines."""
    from openexecutive.alerts.models import AlertSeverity
    from openexecutive.monitoring.models import Signal, WatchlistItem
    from openexecutive.monitoring.pipeline import _signal_to_alert_event

    item = WatchlistItem(
        id=42,
        slug="stock-aapl",
        signal_type="stock",
        target="AAPL",
    )
    signal = Signal(
        watchlist_id=42,
        source_kind="stock",
        source_external_id="AAPL",
        captured_at="2026-05-28T12:00:00+00:00",
        normalized_summary="Apple (AAPL) down 7.0%",
        raw_payload={"target_url": "https://finance.yahoo.com/quote/AAPL"},
        provenance_url="https://finance.yahoo.com/quote/AAPL",
        severity_hint=AlertSeverity.HIGH,
        dedup_key="stock:abc123",
    )
    event = _signal_to_alert_event(signal, item)
    assert "Watchlist: stock-aapl" in event.body
    assert "Severity hint: high" in event.body
    assert "Provenance: https://finance.yahoo.com/quote/AAPL" in event.body
    assert event.source == "stock"
    assert event.external_id == "stock:abc123"


def test_strip_url_query_drops_tokens() -> None:
    """The audit-log _safe_url helper drops query strings (potential tokens)."""
    from openexecutive.monitoring.pipeline import _safe_url
    assert _safe_url("https://x.com/a/b?token=secret") == "https://x.com/a/b"
    assert _safe_url("https://x.com/a#frag") == "https://x.com/a"
    assert _safe_url("not a url") == "not a url"  # graceful no-op
