"""Vendor status-page adapter.

Polls Statuspage-compatible Atom history feeds for vendors a company
depends on (AWS, Stripe, Twilio, GitHub, Cloudflare, etc.) and emits one
``Signal`` per new incident. The Atom 1.0 history feed each vendor
publishes is the same shape — entry/id, entry/title, entry/updated,
entry/link — so a single adapter handles them all.

Watchlist row shape this adapter expects:

  - ``signal_type``: ``"vendor_status"``
  - ``target``: the full Atom URL (e.g. ``https://status.aws.amazon.com/rss/all.rss``)
  - ``config_json``: optional ``{"vendor_label": "AWS"}`` — used as a
    prefix in the normalized summary; falls back to a host-derived label
    when missing so legacy rows still render readably.
  - ``trigger_json``: optional ``{"keywords": ["payment", "us-east-1"]}``
    — when present, only entries whose title/summary contain at least one
    keyword match. Otherwise every new entry surfaces.

Severity hint: defaults to ``HIGH`` for any new incident. The triage
pipeline downstream still has final say — a HIGH hint with a low-trust
score from the watchlist (configured in PR-B's tuning loop) gets damped
before the principal sees it.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, ParseError

import httpx

# defusedxml hardens the stdlib ElementTree parser against billion-laughs
# / quadratic-blowup / external-entity attacks. Adapters fetch arbitrary
# URLs from user-supplied watchlist rows, so the parser sees attacker-
# controlled XML in the wild — defusedxml is the right default.
from defusedxml.ElementTree import fromstring as defused_fromstring

from openexecutive.alerts.models import AlertSeverity
from openexecutive.config import get_settings
from openexecutive.monitoring.models import (
    SOURCE_KIND_VENDOR_STATUS,
    Signal,
    WatchlistItem,
)
from openexecutive.monitoring.sources._http import (
    FetchOverflowError,
    fetch_bounded,
    strip_url_query,
    validate_target_url,
)

logger = logging.getLogger(__name__)

# Atom namespace. Most Statuspage instances publish a pure Atom 1.0 feed
# at /history.atom; a few publish RSS 2.0 at /rss/all.rss. We support
# both: see _parse_feed.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# The documented Statuspage history feed is bounded (~25 entries) — a
# runaway count here means the feed is malformed and we should stop
# reading rather than process megabytes of garbage.
_MAX_ENTRIES_PER_FEED = 100


class VendorStatusSource:
    kind: str = SOURCE_KIND_VENDOR_STATUS
    default_poll_interval_minutes: int = 5

    async def poll(
        self, item: WatchlistItem, *, db_path: Path | None = None
    ) -> list[Signal]:
        if not item.target:
            logger.warning(
                "vendor_status: watchlist %r has empty target — skipping", item.slug
            )
            return []

        # SSRF guard — see monitoring.sources._http.validate_target_url
        # for the full rationale. Watchlist rows are user-supplied, so
        # this is the only spot that turns a string into an HTTP request.
        ok, reason = validate_target_url(item.target)
        if not ok:
            logger.warning(
                "vendor_status: rejecting watchlist %r target — %s",
                item.slug, reason,
            )
            return []

        max_bytes = get_settings().external_monitor_max_fetch_bytes
        try:
            body = await fetch_bounded(item.target, max_bytes)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            logger.warning(
                "vendor_status: fetch failed for %s (%s): %s",
                item.slug, item.target, exc,
            )
            return []
        except FetchOverflowError:
            logger.warning(
                "vendor_status: feed %s exceeded %d bytes — dropping tick",
                item.target, max_bytes,
            )
            return []

        try:
            entries = _parse_feed(body)
        except ParseError as exc:
            logger.warning(
                "vendor_status: feed %s failed to parse: %s", item.target, exc
            )
            return []

        if not entries:
            return []

        vendor_label = (
            item.config_json.get("vendor_label")
            or _host_label(item.target)
        )
        signals: list[Signal] = []
        for entry in entries[:_MAX_ENTRIES_PER_FEED]:
            entry_id = entry.get("id") or entry.get("link") or ""
            if not entry_id:
                # Without a stable upstream id we cannot dedup — skip
                # rather than emit a noisy hash-of-title signal that
                # would re-fire on every minor edit.
                continue
            title = (entry.get("title") or "").strip() or "(untitled incident)"
            summary = f"[{vendor_label}] {title}"
            dedup_key = _make_dedup_key(item.slug, entry_id)
            signals.append(Signal(
                watchlist_id=item.id or 0,
                source_kind=self.kind,
                source_external_id=entry_id[:500],
                captured_at=datetime.now(UTC).isoformat(),
                normalized_summary=summary[:500],
                raw_payload={
                    "vendor_label": vendor_label,
                    "target_url": item.target,
                    "entry_id": entry_id,
                    "title": title,
                    "link": entry.get("link", ""),
                    "updated": entry.get("updated", ""),
                },
                provenance_url=entry.get("link") or item.target,
                severity_hint=AlertSeverity.HIGH,
                dedup_key=dedup_key,
            ))
        return signals

    def matches_trigger(self, signal: Signal, item: WatchlistItem) -> bool:
        """Optional keyword filter — see module docstring."""
        keywords = item.trigger_json.get("keywords") or []
        if not keywords:
            return True
        haystack = (signal.normalized_summary or "").lower()
        return any(kw.lower() in haystack for kw in keywords)


def _parse_feed(body: bytes) -> list[dict[str, str]]:
    """Parse an Atom or RSS feed into a list of {id, title, link, updated} dicts.

    Uses ``defusedxml.ElementTree`` to block entity-expansion attacks
    (billion-laughs, quadratic-blowup) — see the module-level import
    comment for the full rationale. Returns ``[]`` on unknown shapes
    so a vendor switching feed formats doesn't crash the scan.
    """
    root = defused_fromstring(body)
    tag = root.tag.lower()

    # Atom 1.0 feed root: <feed xmlns="http://www.w3.org/2005/Atom"> with <entry>s
    if tag.endswith("feed"):
        return _parse_atom(root)

    # RSS 2.0: <rss><channel><item>...
    if tag == "rss":
        channel = root.find("channel")
        return _parse_rss(channel) if channel is not None else []

    return []


def _parse_atom(feed: Element) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for entry in feed.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        updated = (entry.findtext(f"{_ATOM_NS}updated") or "").strip()
        link = ""
        link_el = entry.find(f"{_ATOM_NS}link")
        if link_el is not None:
            link = (link_el.get("href") or "").strip()
        link = strip_url_query(link) if link else ""
        out.append({"id": entry_id, "title": title, "link": link, "updated": updated})
    return out


def _parse_rss(channel: Element) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in channel.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        link = strip_url_query(link) if link else ""
        # Prefer guid for id (stable upstream identifier); fall back to
        # the cleaned link so dedup remains stable.
        entry_id = guid or link
        out.append({"id": entry_id, "title": title, "link": link, "updated": pub})
    return out


def _host_label(url: str) -> str:
    """Best-effort vendor name from a URL host when config doesn't set one."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return "vendor"
    # status.aws.amazon.com → aws; status.stripe.com → stripe
    parts = [p for p in host.split(".") if p and p != "status" and p != "www"]
    return parts[0] if parts else host or "vendor"


def _make_dedup_key(slug: str, entry_id: str) -> str:
    """Deterministic dedup_key over (watchlist slug, upstream entry id).

    Hashed because Atom ids can be 200+ char tag URIs; the watchlist
    slug prefix ensures two different watchlist rows pointing at the
    same vendor still produce distinct keys (so each row's own
    trigger / routing fires independently).
    """
    payload = f"{slug}\x00{entry_id}".encode()
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"vendor_status:{digest}"
