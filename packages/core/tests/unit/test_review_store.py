"""Unit tests for the SME knowledge review store."""
from __future__ import annotations

from pathlib import Path

import pytest

from openexecutive.knowledge.review_store import (
    Annotation,
    ContentType,
    Priority,
    ReviewItem,
    ReviewStatus,
    ReviewStore,
)


@pytest.fixture()
def store(tmp_path: Path) -> ReviewStore:
    db = tmp_path / "review.db"
    ReviewStore.initialize_db(db)
    return ReviewStore(db_path=db)


def _register_builtin(store: ReviewStore, domain: str = "finance", filename: str = "ratios.md") -> ReviewItem:
    item_id = f"builtin:{domain}:{filename}"
    return store.register(
        item_id=item_id,
        content_type=ContentType.BUILTIN,
        domain=domain,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Register / idempotency
# ---------------------------------------------------------------------------


def test_register_creates_pending_item(store: ReviewStore) -> None:
    item = _register_builtin(store)
    assert item.status == ReviewStatus.PENDING
    assert item.priority == Priority.NORMAL
    assert item.content_type == ContentType.BUILTIN


def test_register_is_idempotent(store: ReviewStore) -> None:
    first = _register_builtin(store)
    # Approve, then re-register — should not reset the status
    store.set_status(first.item_id, ReviewStatus.APPROVED)
    second = store.register(
        item_id=first.item_id,
        content_type=ContentType.BUILTIN,
        domain="finance",
        filename="ratios.md",
    )
    assert second.status == ReviewStatus.APPROVED


# ---------------------------------------------------------------------------
# touch_modified transitions
# ---------------------------------------------------------------------------


def test_touch_modified_approved_becomes_needs_revision(store: ReviewStore) -> None:
    item = _register_builtin(store)
    store.set_status(item.item_id, ReviewStatus.APPROVED)
    store.touch_modified(item.item_id)
    updated = store.get_item(item.item_id)
    assert updated is not None
    assert updated.status == ReviewStatus.NEEDS_REVISION


def test_touch_modified_rejected_becomes_needs_revision(store: ReviewStore) -> None:
    item = _register_builtin(store)
    store.set_status(item.item_id, ReviewStatus.REJECTED)
    store.touch_modified(item.item_id)
    updated = store.get_item(item.item_id)
    assert updated is not None
    assert updated.status == ReviewStatus.NEEDS_REVISION


def test_touch_modified_pending_stays_pending(store: ReviewStore) -> None:
    item = _register_builtin(store)
    store.touch_modified(item.item_id)
    updated = store.get_item(item.item_id)
    assert updated is not None
    assert updated.status == ReviewStatus.PENDING


def test_touch_modified_needs_revision_stays(store: ReviewStore) -> None:
    item = _register_builtin(store)
    store.set_status(item.item_id, ReviewStatus.NEEDS_REVISION)
    store.touch_modified(item.item_id)
    updated = store.get_item(item.item_id)
    assert updated is not None
    assert updated.status == ReviewStatus.NEEDS_REVISION


# ---------------------------------------------------------------------------
# get_rejected_filenames
# ---------------------------------------------------------------------------


def test_get_rejected_filenames_returns_only_rejected(store: ReviewStore) -> None:
    item_a = _register_builtin(store, filename="ratios.md")
    item_b = _register_builtin(store, filename="fundraising.md")
    item_c = _register_builtin(store, filename="modeling.md")

    store.set_status(item_a.item_id, ReviewStatus.REJECTED)
    store.set_status(item_b.item_id, ReviewStatus.APPROVED)

    rejected = store.get_rejected_filenames(ContentType.BUILTIN)
    assert rejected == {"ratios.md"}
    assert "fundraising.md" not in rejected
    assert "modeling.md" not in rejected


def test_get_rejected_filenames_empty_when_none_rejected(store: ReviewStore) -> None:
    _register_builtin(store)
    assert store.get_rejected_filenames(ContentType.BUILTIN) == set()


def test_get_rejected_source_ids(store: ReviewStore) -> None:
    store.register(
        item_id="external:openstax-finance",
        content_type=ContentType.EXTERNAL,
        domain="finance",
        filename="openstax-finance",
    )
    store.set_status("external:openstax-finance", ReviewStatus.REJECTED)
    assert "openstax-finance" in store.get_rejected_source_ids()


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


def test_set_priority(store: ReviewStore) -> None:
    item = _register_builtin(store)
    updated = store.set_priority(item.item_id, Priority.HIGH)
    assert updated.priority == Priority.HIGH


def test_get_priority_map_only_approved(store: ReviewStore) -> None:
    item_a = _register_builtin(store, filename="a.md")
    item_b = _register_builtin(store, filename="b.md")
    store.set_status(item_a.item_id, ReviewStatus.APPROVED)
    store.set_priority(item_a.item_id, Priority.HIGH)
    # item_b stays pending — should not appear in priority map

    pmap = store.get_priority_map(ContentType.BUILTIN)
    assert pmap.get("a.md") == "high"
    assert "b.md" not in pmap


# ---------------------------------------------------------------------------
# Bulk approve
# ---------------------------------------------------------------------------


def test_bulk_approve_all_pending(store: ReviewStore) -> None:
    for fn in ["a.md", "b.md", "c.md"]:
        _register_builtin(store, filename=fn)
    count = store.bulk_approve()
    assert count == 3
    for fn in ["a.md", "b.md", "c.md"]:
        item = store.get_item(f"builtin:finance:{fn}")
        assert item is not None
        assert item.status == ReviewStatus.APPROVED


def test_bulk_approve_domain_filter(store: ReviewStore) -> None:
    _register_builtin(store, domain="finance", filename="a.md")
    store.register(
        item_id="builtin:hr:b.md",
        content_type=ContentType.BUILTIN,
        domain="hr",
        filename="b.md",
    )
    count = store.bulk_approve(domain="finance")
    assert count == 1
    assert store.get_item("builtin:finance:a.md") is not None
    assert store.get_item("builtin:finance:a.md").status == ReviewStatus.APPROVED  # type: ignore[union-attr]
    assert store.get_item("builtin:hr:b.md") is not None
    assert store.get_item("builtin:hr:b.md").status == ReviewStatus.PENDING  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------


def test_add_and_list_annotations(store: ReviewStore) -> None:
    item = _register_builtin(store)
    ann = store.add_annotation(item.item_id, "finance", "Q3 burn rate is outdated")
    assert isinstance(ann, Annotation)
    assert ann.is_active is True

    listed = store.list_annotations(item_id=item.item_id)
    assert len(listed) == 1
    assert listed[0].correction == "Q3 burn rate is outdated"


def test_toggle_annotation(store: ReviewStore) -> None:
    item = _register_builtin(store)
    ann = store.add_annotation(item.item_id, "finance", "correction")
    store.toggle_annotation(ann.annotation_id, False)
    active = store.list_annotations(item_id=item.item_id, active_only=True)
    assert len(active) == 0
    all_anns = store.list_annotations(item_id=item.item_id, active_only=False)
    assert len(all_anns) == 1
    assert all_anns[0].is_active is False


def test_list_annotations_by_domain(store: ReviewStore) -> None:
    finance_item = _register_builtin(store, domain="finance", filename="a.md")
    hr_item = store.register(
        item_id="builtin:hr:b.md",
        content_type=ContentType.BUILTIN,
        domain="hr",
        filename="b.md",
    )
    store.add_annotation(finance_item.item_id, "finance", "finance note")
    store.add_annotation(hr_item.item_id, "hr", "hr note")

    finance_anns = store.list_annotations(domains=["finance"])
    assert len(finance_anns) == 1
    assert finance_anns[0].correction == "finance note"


def test_delete_annotation(store: ReviewStore) -> None:
    item = _register_builtin(store)
    ann = store.add_annotation(item.item_id, "finance", "to be deleted")
    store.delete_annotation(ann.annotation_id)
    assert store.list_annotations(item_id=item.item_id) == []


def test_delete_item_cascades_annotations(store: ReviewStore) -> None:
    item = _register_builtin(store)
    store.add_annotation(item.item_id, "finance", "note")
    store.delete_item(item.item_id)
    assert store.get_item(item.item_id) is None
    # Annotations should be cascade-deleted
    assert store.list_annotations(item_id=item.item_id) == []


# ---------------------------------------------------------------------------
# count_by_status
# ---------------------------------------------------------------------------


def test_count_by_status(store: ReviewStore) -> None:
    _register_builtin(store, filename="a.md")
    item_b = _register_builtin(store, filename="b.md")
    store.set_status(item_b.item_id, ReviewStatus.APPROVED)

    counts = store.count_by_status()
    assert counts["pending"] == 1
    assert counts["approved"] == 1
    assert counts["total"] == 2
