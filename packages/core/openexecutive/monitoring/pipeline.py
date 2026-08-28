"""External-monitor scan + signal promotion pipeline.

Wires the source adapters into the existing scheduled-action / alert
infrastructure:

  1. ``run_external_monitor_scan`` — one tick. Walks enabled watchlist
     rows whose ``last_polled_at`` is stale enough, calls the matching
     adapter, writes signals, and promotes signals that survive triage
     into the existing alerts pipeline.
  2. ``bootstrap_external_monitor_scan`` / ``enqueue_next_external_monitor_scan``
     — heartbeat lifecycle, identical shape to nudge_engine's
     bootstrap / enqueue_next pair. Idempotent on restart.
  3. ``promote_signal_to_alert`` — turns a Signal into an
     ``AlertEvent`` and fires it through ``alerts.pipeline.schedule_evaluation``
     so the existing triage agent handles severity, channels,
     ``suggested_action``, and dispatch.

Design notes:

- Scan failures on one source must not poison the others — every
  adapter is wrapped in its own try/except, identical to nudge_engine.
- The scan is idempotent at the row level via the UNIQUE(dedup_key)
  constraint on ``external_signals``, so a duplicate tick is harmless.
- Adapter cadence is honoured by checking ``last_polled_at`` against
  the per-source minutes setting; the heartbeat interval is the *floor*
  on poll latency, not the actual poll rate.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openexecutive.alerts.models import SEVERITY_RANK, AlertEvent, AlertSeverity
from openexecutive.alerts.pipeline import schedule_evaluation
from openexecutive.audit import log_event as audit_log
from openexecutive.config import get_settings
from openexecutive.memory.episodic import insert_scheduled_action
from openexecutive.monitoring import store
from openexecutive.monitoring.enrichment import build_company_context, enrich_signal
from openexecutive.monitoring.models import (
    MODE_DRY_RUN,
    OUTCOME_ALERTED,
    OUTCOME_FAILED,
    OUTCOME_SUPPRESSED_BELOW_FLOOR,
    OUTCOME_SUPPRESSED_DRY_RUN,
    OUTCOME_SUPPRESSED_LOW_RELEVANCE,
    SOURCE_KIND_QUERY,
    Signal,
    WatchlistItem,
)
from openexecutive.monitoring.sources import get_source_for_kind
from openexecutive.monitoring.sources._http import strip_url_query

logger = logging.getLogger(__name__)

HEARTBEAT_KIND = "external_monitor_scan"
HEARTBEAT_CHANNEL = "__internal__"
HEARTBEAT_CHANNEL_REF = "external_monitor"
HEARTBEAT_INTENT = "External-condition monitoring — periodic source poll."


# Re-exported under a friendlier name for in-module audit calls; the
# behaviour is identical to monitoring.sources._http.strip_url_query.
_safe_url = strip_url_query


# --------------------------------------------------------------------- #
# Per-source cadence resolution
# --------------------------------------------------------------------- #


def _poll_floor_minutes_for(signal_type: str) -> int:
    """Per-source poll-cadence floor in minutes.

    Read directly off the registered adapter's
    ``default_poll_interval_minutes`` — no central switch statement to
    keep in sync as new adapters land. Falls back to the heartbeat
    interval for unknown kinds (defensive: adapter-less rows are
    filtered out upstream, but a stale watchlist row shouldn't crash).
    """
    src = get_source_for_kind(signal_type)
    if src is None:
        return get_settings().external_monitor_scan_interval_minutes
    return src.default_poll_interval_minutes


def _due_for_poll(item: WatchlistItem, now: datetime) -> bool:
    if item.last_polled_at is None:
        return True
    try:
        last = datetime.fromisoformat(item.last_polled_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    floor = timedelta(minutes=_poll_floor_minutes_for(item.signal_type))
    return now - last >= floor


# --------------------------------------------------------------------- #
# Signal → alert promotion
# --------------------------------------------------------------------- #


def _cap_to_ceiling(
    hint: AlertSeverity, item: WatchlistItem,
) -> AlertSeverity:
    # Ceiling-only — raising to meet the floor would defeat the below-
    # floor suppression gate downstream. NOTE: this caps the value
    # recorded on the external_signals row (used by the reflection
    # context in PR-C). The user-visible alert severity is independently
    # produced by the triage agent; PR-C adds an external_signal
    # severity hint to the triage prompt so the ceiling reaches the user.
    rank = SEVERITY_RANK[hint.value]
    ceiling_rank = SEVERITY_RANK[item.severity_ceiling.value]
    if rank > ceiling_rank:
        return item.severity_ceiling
    return hint


# Relevance → severity bands for standing-query signals. The `query` adapter
# emits every web hit flat-LOW (sources/query.py) because it can't judge how
# material a hit is on its own — so a query watch item with a severity_floor
# above "low" used to suppress *every* hit below the floor, including genuinely
# material ones, leaving the Monitoring lane permanently empty. We instead
# grade severity from the capture-time relevance read: a directly-material hit
# (>= _HIGH) escalates to the action lane, a moderately-relevant one (>= _MEDIUM)
# clears a medium floor into Monitoring, and the rest stay LOW so a medium/high
# floor still filters them as noise. Capped to the row's ceiling so an operator's
# explicit cap is honoured.
_QUERY_RELEVANCE_HIGH = 0.8
_QUERY_RELEVANCE_MEDIUM = 0.5


def _grade_query_severity(
    enrichment: dict | None, item: WatchlistItem,
) -> AlertSeverity:
    """Map a query signal's capture-time relevance onto a severity band.

    ``enrichment`` is the ``SignalEnrichment.model_dump()`` payload (or ``None``
    when enrichment is disabled / missed, in which case the signal stays LOW —
    grading requires a relevance read). Result is capped to the watchlist row's
    ceiling.
    """
    score = 0.0
    if enrichment:
        try:
            score = float(enrichment.get("relevance_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
    if score >= _QUERY_RELEVANCE_HIGH:
        graded = AlertSeverity.HIGH
    elif score >= _QUERY_RELEVANCE_MEDIUM:
        graded = AlertSeverity.MEDIUM
    else:
        graded = AlertSeverity.LOW
    return _cap_to_ceiling(graded, item)


def _signal_to_alert_event(
    signal: Signal,
    item: WatchlistItem,
    enrichment: dict | None = None,
) -> AlertEvent:
    """Construct an AlertEvent from a promoted signal.

    The body deliberately interleaves the normalized summary, a
    structured slice of the raw payload, the watchlist slug, the
    adapter's severity hint, and the provenance URL so the triage
    prompt has every cue it needs in one place. ``AlertEvent`` itself
    has no slug or severity field, so anything triage needs to see
    must land in the body — the triage prompt's external-signals
    rules read directly from these lines.

    When capture-time ``enrichment`` is present, its one-line
    ``why_it_matters`` is surfaced high in the body so triage and the
    principal see the company-specific relevance, not just the raw event.
    """
    parts: list[str] = [signal.normalized_summary]
    if enrichment:
        why = str(enrichment.get("why_it_matters") or "").strip()
        if why:
            parts.append(f"Why this matters: {why}")
    # Slug + severity hint are required by the triage prompt's
    # external-signals rules. Putting them on their own labeled lines
    # makes them robust to the model's tendency to skim long bodies.
    parts.append(f"Watchlist: {item.slug}")
    parts.append(f"Severity hint: {signal.severity_hint.value}")
    target = signal.raw_payload.get("target_url")
    if target:
        parts.append(f"Source: {target}")
    if signal.provenance_url:
        parts.append(f"Provenance: {signal.provenance_url}")
    return AlertEvent(
        source=signal.source_kind,
        external_id=signal.dedup_key,
        subject=signal.normalized_summary[:200],
        body="\n".join(parts)[:4000],
    )


async def promote_signal_to_alert(
    signal: Signal,
    item: WatchlistItem,
    *,
    signal_id: int,
    db_path: Path | None = None,
    enrichment: dict | None = None,
) -> None:
    """Promote a stored signal into the alerts pipeline.

    Schedules the triage evaluation as fire-and-forget so a slow LLM
    call cannot stall the scan loop. The signal row is marked
    ``alerted`` immediately on schedule — the eventual
    ``promoted_alert_id`` backfill (after triage runs) is left for a
    later phase to avoid coupling the scan to evaluation latency.

    Caller is responsible for the dry-run / below-floor / dup gates;
    this entry point assumes the signal already cleared them.
    ``enrichment`` (when present) adds a "why this matters" line to the
    alert body.
    """
    event = _signal_to_alert_event(signal, item, enrichment)
    try:
        schedule_evaluation(event)
    except Exception:
        # Triage scheduling itself crashed — the signal was NOT alerted.
        # Record OUTCOME_FAILED so the audit trail doesn't lie about
        # what actually happened (and so a dashboard counting
        # `processed_outcome='alerted'` rows isn't inflated by failures).
        logger.exception(
            "monitoring.pipeline: schedule_evaluation failed for signal %d",
            signal_id,
        )
        store.mark_signal_processed(
            signal_id, OUTCOME_FAILED, promoted_alert_id=None, db_path=db_path
        )
        return

    store.mark_signal_processed(
        signal_id, OUTCOME_ALERTED, promoted_alert_id=None, db_path=db_path
    )
    if item.id is not None:
        store.mark_fired(item.id, datetime.now(UTC), db_path=db_path)
    audit_log(
        "external_signal_promoted",
        f"Signal {signal_id} promoted to alert pipeline ({signal.source_kind})",
        actor="external_monitor",
        details={
            "signal_id": signal_id,
            "watchlist_id": item.id,
            "watchlist_slug": item.slug,
            "source_kind": signal.source_kind,
            "severity_hint": signal.severity_hint.value,
            "dedup_key": signal.dedup_key,
            # Strip query string before logging — a malicious feed could
            # embed a token in the link; we don't want it in audit.
            "provenance_url": _safe_url(signal.provenance_url),
        },
    )


# --------------------------------------------------------------------- #
# Scan orchestration
# --------------------------------------------------------------------- #


def _audit_suppressed(
    signal_id: int,
    item: WatchlistItem,
    signal: Signal,
    outcome: str,
) -> None:
    """Symmetric audit emission for a suppressed signal — pairs with
    ``external_signal_received`` so a reader sees both 'noticed' and
    'suppressed' rows for the same dedup_key."""
    audit_log(
        "external_signal_suppressed",
        f"Signal {signal_id} suppressed ({outcome}): "
        f"{signal.normalized_summary[:120]}",
        actor="external_monitor",
        details={
            "signal_id": signal_id,
            "watchlist_id": item.id,
            "watchlist_slug": item.slug,
            "source_kind": signal.source_kind,
            "severity_hint": signal.severity_hint.value,
            "dedup_key": signal.dedup_key,
            "outcome": outcome,
        },
    )


async def _poll_one_watchlist_item(
    item: WatchlistItem,
    now: datetime,
    *,
    cap_remaining: int,
    company_ctx: str = "",
    db_path: Path | None = None,
) -> int:
    """Poll one watchlist row, persist signals, promote where appropriate.

    Returns the number of signals written to the DB on this tick (NOT
    the number alerted — some get suppressed by floor / dry_run / dup).
    """
    src = get_source_for_kind(item.signal_type)
    if src is None:
        logger.warning(
            "monitoring.pipeline: no registered adapter for signal_type=%r "
            "(watchlist slug=%r) — skipping",
            item.signal_type, item.slug,
        )
        return 0

    try:
        emitted = await src.poll(item, db_path=db_path)
    except Exception:
        logger.exception(
            "monitoring.pipeline: adapter %s crashed for watchlist %r",
            item.signal_type, item.slug,
        )
        return 0
    finally:
        # Always mark polled even on adapter crash so a broken source
        # doesn't get hammered on every tick.
        if item.id is not None:
            store.mark_polled(item.id, now, db_path=db_path)

    if not emitted:
        return 0

    written = 0
    for raw_signal in emitted:
        if cap_remaining <= 0:
            logger.info(
                "monitoring.pipeline: max_signals_per_scan cap hit at "
                "watchlist %r (signal_type=%s) — dropping remaining %d",
                item.slug, item.signal_type, len(emitted) - written,
            )
            break

        # Apply per-item trigger filter (default: True).
        try:
            if not src.matches_trigger(raw_signal, item):
                continue
        except Exception:
            logger.exception(
                "monitoring.pipeline: matches_trigger crashed on %r — "
                "treating as no-match", item.slug
            )
            continue

        capped = _cap_to_ceiling(raw_signal.severity_hint, item)
        signal = raw_signal.model_copy(update={"severity_hint": capped})

        signal_id = store.insert_signal(signal, db_path=db_path)
        if signal_id is None:
            # UNIQUE(dedup_key) clash — same upstream event, already
            # recorded. No-op. We don't even log debug because the same
            # vendor status page returns the same 25 entries every poll.
            continue

        cap_remaining -= 1
        written += 1
        audit_log(
            "external_signal_received",
            f"Signal received: {signal.normalized_summary[:120]}",
            actor="external_monitor",
            details={
                "signal_id": signal_id,
                "watchlist_id": item.id,
                "watchlist_slug": item.slug,
                "source_kind": signal.source_kind,
                "severity_hint": signal.severity_hint.value,
                "dedup_key": signal.dedup_key,
            },
        )

        # Suppression cascade — order matters. Each branch emits a
        # paired external_signal_suppressed audit event so a reader can
        # tell from the audit log alone (without joining external_signals)
        # what was noticed but not surfaced.
        if item.mode == MODE_DRY_RUN:
            store.mark_signal_processed(
                signal_id, OUTCOME_SUPPRESSED_DRY_RUN, db_path=db_path
            )
            _audit_suppressed(
                signal_id, item, signal, OUTCOME_SUPPRESSED_DRY_RUN,
            )
            continue

        # Standing-query signals arrive flat-LOW: the `query` adapter can't
        # judge how material a web hit is, so it grades every result LOW. Left
        # as-is, any query watch item with a severity_floor above "low"
        # suppresses every hit below the floor — even a material one — before it
        # can reach the Monitoring lane. So for query signals we run the
        # capture-time relevance read BEFORE the floor gate and grade severity
        # from it, letting a material hit clear a medium/high floor. Other
        # sources arrive pre-graded by their adapter and keep the cheaper
        # "enrich after the floor gate" order below (no LLM call spent on a
        # signal that's already being floor-suppressed).
        enrichment_payload: dict | None = None
        enriched = False
        if signal.source_kind == SOURCE_KIND_QUERY:
            should_promote, enrichment_payload = await _maybe_enrich(
                signal, item, signal_id, company_ctx=company_ctx, db_path=db_path,
            )
            if not should_promote:
                # Relevance gate already recorded + audited the suppression.
                continue
            # Set the flag only on the surviving path, so it means "enriched AND
            # kept" — keeps the non-query branch below the sole enrichment site
            # for non-query signals even if this block is later edited.
            enriched = True
            signal = signal.model_copy(update={
                "severity_hint": _grade_query_severity(enrichment_payload, item),
            })

        sev_rank = SEVERITY_RANK[signal.severity_hint.value]
        floor_rank = SEVERITY_RANK[item.severity_floor.value]
        if sev_rank < floor_rank:
            store.mark_signal_processed(
                signal_id, OUTCOME_SUPPRESSED_BELOW_FLOOR, db_path=db_path
            )
            _audit_suppressed(
                signal_id, item, signal, OUTCOME_SUPPRESSED_BELOW_FLOOR,
            )
            continue

        # Capture-time enrichment for pre-graded sources runs here — AFTER the
        # dry_run / floor gates — so we never spend an LLM call enriching a
        # signal that's already being suppressed. Fail-open: any miss leaves
        # enrichment None and the signal promotes unchanged. The optional
        # relevance gate is the only branch that can suppress here (default
        # threshold 0.0 = off). Query signals were already enriched above (to
        # grade severity), so skip the repeat call.
        if not enriched:
            should_promote, enrichment_payload = await _maybe_enrich(
                signal, item, signal_id, company_ctx=company_ctx, db_path=db_path,
            )
            if not should_promote:
                continue

        await promote_signal_to_alert(
            signal, item, signal_id=signal_id, db_path=db_path,
            enrichment=enrichment_payload,
        )

    return written


async def _maybe_enrich(
    signal: Signal,
    item: WatchlistItem,
    signal_id: int,
    *,
    company_ctx: str,
    db_path: Path | None = None,
) -> tuple[bool, dict | None]:
    """Enrich a promotable signal, persist it, and apply the relevance gate.

    Returns ``(should_promote, enrichment_payload)``:
      - ``(True, dict)``  — enriched; promote with the payload.
      - ``(True, None)``  — enrichment disabled or a miss; promote unchanged.
      - ``(False, dict)`` — relevance gate fired; already recorded + audited,
        do NOT promote.
    """
    settings = get_settings()
    # Per-row opt-out via config_json["enrich"] = false; default on when the
    # global switch is enabled, so enrichment applies to every source.
    if not settings.external_monitor_enrichment_enabled:
        return True, None
    # Per-row opt-out: any falsy config value (false / 0 / "") disables it.
    # ``is False`` would miss JSON ``0``; a truthiness test is what we want.
    if not item.config_json.get("enrich", True):
        return True, None

    result = await enrich_signal(signal, item, company_ctx=company_ctx)
    if result is None:
        return True, None

    payload = result.model_dump()
    try:
        store.update_signal_enrichment(signal_id, payload, db_path=db_path)
    except Exception:
        logger.exception(
            "monitoring.pipeline: persisting enrichment failed for signal %d",
            signal_id,
        )

    if result.relevance_score < settings.external_monitor_enrichment_min_relevance:
        store.mark_signal_processed(
            signal_id, OUTCOME_SUPPRESSED_LOW_RELEVANCE, db_path=db_path
        )
        _audit_suppressed(
            signal_id, item, signal, OUTCOME_SUPPRESSED_LOW_RELEVANCE,
        )
        return False, payload

    return True, payload


async def run_external_monitor_scan(
    now: datetime | None = None,
    *,
    db_path: Path | None = None,
) -> int:
    """Run one scan pass over the enabled watchlist. Returns total signals written.

    "Signals written" counts rows landed in ``external_signals``, which
    includes suppressed-dry-run / suppressed-below-floor entries — they
    still get the audit trail. Excludes UNIQUE(dedup_key) clashes
    (no row written) and adapter crashes (no row written).
    """
    now = now or datetime.now(UTC)
    settings = get_settings()
    cap_total = settings.external_monitor_max_signals_per_scan

    items = store.list_watchlist(enabled_only=True, db_path=db_path)
    if not items:
        return 0

    # Company context for enrichment is the same for every signal this tick, so
    # load it once (one profile + initiatives read) rather than per signal.
    # Empty string when enrichment is globally off — _maybe_enrich short-circuits
    # before it's used, so we skip the load entirely in that case.
    company_ctx = (
        build_company_context(db_path=db_path)
        if settings.external_monitor_enrichment_enabled
        else ""
    )

    total_written = 0
    cap_remaining = cap_total
    poll_start = datetime.now(UTC)
    for item in items:
        if cap_remaining <= 0:
            break
        # Billed standing-query adapter has its own kill switch — skip those
        # rows entirely (no poll, no mark_polled) when disabled.
        if (
            item.signal_type == SOURCE_KIND_QUERY
            and not settings.external_monitor_query_enabled
        ):
            continue
        if not _due_for_poll(item, now):
            continue
        per_source_start = datetime.now(UTC)
        written = await _poll_one_watchlist_item(
            item, now, cap_remaining=cap_remaining,
            company_ctx=company_ctx, db_path=db_path,
        )
        cap_remaining -= written
        total_written += written
        duration_ms = int(
            (datetime.now(UTC) - per_source_start).total_seconds() * 1000
        )
        audit_log(
            "external_monitor_poll",
            f"Polled {item.slug} ({item.signal_type}) — {written} signal(s)",
            actor="external_monitor",
            details={
                "watchlist_id": item.id,
                "watchlist_slug": item.slug,
                "signal_type": item.signal_type,
                "signals_emitted": written,
                "duration_ms": duration_ms,
            },
        )

    scan_duration_ms = int(
        (datetime.now(UTC) - poll_start).total_seconds() * 1000
    )
    logger.info(
        "monitoring.pipeline: scan complete — %d signal(s) across %d watchlist "
        "row(s) in %dms (cap_total=%d, cap_remaining=%d)",
        total_written, len(items), scan_duration_ms, cap_total, cap_remaining,
    )
    return total_written


# --------------------------------------------------------------------- #
# Heartbeat bootstrap / chain — mirrors scheduler/nudge_engine.py
# --------------------------------------------------------------------- #


def _heartbeat_pending(db_path: Path | None = None) -> bool:
    from openexecutive.memory.episodic import _get_conn, _resolve_db_path

    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return False
    with _get_conn(resolved) as conn:
        row = conn.execute(
            "SELECT 1 FROM scheduled_actions "
            "WHERE kind = ? AND status IN ('pending', 'running') LIMIT 1",
            (HEARTBEAT_KIND,),
        ).fetchone()
    return row is not None


def bootstrap_external_monitor_scan(
    db_path: Path | None = None,
) -> int | None:
    """Ensure exactly one pending external_monitor_scan row exists.

    Idempotent — safe to call on every boot. Returns the new action id
    if one was inserted, else None.
    """
    if _heartbeat_pending(db_path):
        return None
    settings = get_settings()
    run_at = datetime.now(UTC) + timedelta(
        minutes=settings.external_monitor_scan_interval_minutes
    )
    try:
        action_id = insert_scheduled_action(
            run_at=run_at.isoformat(),
            channel=HEARTBEAT_CHANNEL,
            channel_ref=HEARTBEAT_CHANNEL_REF,
            intent_text=HEARTBEAT_INTENT,
            kind=HEARTBEAT_KIND,
            db_path=db_path,
        )
        logger.info(
            "monitoring.bootstrap: heartbeat scheduled at %s (id=%d)",
            run_at.isoformat(), action_id,
        )
        return action_id
    except Exception:
        logger.exception(
            "monitoring.bootstrap: failed to enqueue heartbeat"
        )
        return None


def enqueue_next_external_monitor_scan(
    *,
    after: datetime | None = None,
    db_path: Path | None = None,
) -> int | None:
    """Schedule the next external_monitor_scan tick. Called by the runner
    after each fire."""
    settings = get_settings()
    base = (after or datetime.now(UTC)).astimezone(UTC)
    run_at = base + timedelta(
        minutes=settings.external_monitor_scan_interval_minutes
    )
    try:
        action_id = insert_scheduled_action(
            run_at=run_at.isoformat(),
            channel=HEARTBEAT_CHANNEL,
            channel_ref=HEARTBEAT_CHANNEL_REF,
            intent_text=HEARTBEAT_INTENT,
            kind=HEARTBEAT_KIND,
            db_path=db_path,
        )
        logger.info(
            "monitoring.enqueue_next: next scan at %s (id=%d)",
            run_at.isoformat(), action_id,
        )
        return action_id
    except Exception:
        logger.exception(
            "monitoring.enqueue_next: insert failed"
        )
        return None


__all__ = [
    "HEARTBEAT_CHANNEL",
    "HEARTBEAT_CHANNEL_REF",
    "HEARTBEAT_INTENT",
    "HEARTBEAT_KIND",
    "bootstrap_external_monitor_scan",
    "enqueue_next_external_monitor_scan",
    "promote_signal_to_alert",
    "run_external_monitor_scan",
]
