"""Source adapter Protocol.

Each external feed (vendor status page, RSS feed, stock ticker, etc.) is
encapsulated as a ``Source`` implementation. The pipeline calls
``poll(item)`` to fetch and ``matches_trigger(signal, item)`` to apply
the watchlist row's per-item trigger DSL.

Invariants every adapter MUST uphold:

1. **Read-only** — no writes to upstream systems. The whole monitoring
   subsystem is observation-only; surfacing happens through the alert
   pipeline, never via a direct upstream side-effect.
2. **Deterministic dedup_key** — two polls of the same source returning
   the same underlying event MUST produce the same ``dedup_key``. The
   UNIQUE constraint on ``external_signals.dedup_key`` is the cheap
   anti-replay guard; adapters do the work upstream of it.
3. **Provenance URL is always set** — every surfaced signal must let
   the principal click through to the source in one tap.
4. **Bounded fetch** — adapters consult
   ``settings.external_monitor_max_fetch_bytes`` and stop reading past
   it. Defends against runaway feeds and XML-bomb shapes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from openexecutive.monitoring.models import Signal, WatchlistItem


class Source(Protocol):
    """Adapter contract — see module docstring for invariants."""

    kind: str  # matches WatchlistItem.signal_type

    default_poll_interval_minutes: int

    async def poll(
        self, item: WatchlistItem, *, db_path: Path | None = None
    ) -> list[Signal]:
        """Fetch the source, normalize into Signals, return them.

        Implementations should:
          - clamp body size via settings.external_monitor_max_fetch_bytes
          - return [] on any transient failure (logged inside the
            adapter) rather than raising — the pipeline tolerates a bad
            tick on one source without poisoning the others

        ``db_path`` is the database the current scan is using. Stateless
        adapters ignore it; STATEFUL adapters (e.g. page_watch, which stores
        the last-seen content hash) MUST thread it into their store calls so
        their state lands in the same DB as the signals — never the default.
        """
        ...

    def matches_trigger(self, signal: Signal, item: WatchlistItem) -> bool:
        """Apply the watchlist row's trigger_json to a candidate signal.

        Default is True. Trigger is an opt-in filter — most v1 use cases
        (vendor status incident posted = always alert) don't need one.
        """
        ...


# Re-export Signal so adapter code can ``from .base import Signal``.
__all__ = ["Signal", "Source"]
