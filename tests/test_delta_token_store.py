"""Tests for DeltaTokenStore — async SQLite persistence of Graph API delta links."""

from __future__ import annotations

import logging

import pytest_asyncio

from itgov.services.delta_token_store import DeltaTokenStore, _safe_link_repr

# All async tests use asyncio mode=auto; sync tests get no mark via pytestmark
# (only mark async test functions individually if needed)


@pytest_asyncio.fixture
async def store(tmp_path):
    """DeltaTokenStore backed by a temp SQLite file."""
    return DeltaTokenStore(db_path=str(tmp_path / "delta_tokens.db"))


# ── core CRUD ────────────────────────────────────────────────────────────────


async def test_get_returns_none_when_not_found(store):
    result = await store.get("servicePrincipals", "tenant-abc")
    assert result is None


async def test_set_and_get_roundtrip(store):
    link = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=abc123"
    await store.set("servicePrincipals", "tenant-abc", link)
    result = await store.get("servicePrincipals", "tenant-abc")
    assert result == link


async def test_set_overwrites_previous_value(store):
    await store.set("servicePrincipals", "tenant-abc", "https://graph.microsoft.com/delta?token=old")
    await store.set("servicePrincipals", "tenant-abc", "https://graph.microsoft.com/delta?token=new")
    result = await store.get("servicePrincipals", "tenant-abc")
    assert result == "https://graph.microsoft.com/delta?token=new"


async def test_clear_removes_entry(store):
    await store.set("servicePrincipals", "tenant-abc", "https://graph.microsoft.com/delta?token=xyz")
    await store.clear("servicePrincipals", "tenant-abc")
    result = await store.get("servicePrincipals", "tenant-abc")
    assert result is None


async def test_clear_on_nonexistent_is_safe(store):
    # Should not raise
    await store.clear("servicePrincipals", "nonexistent-tenant")


# ── multi-tenant isolation ────────────────────────────────────────────────────


async def test_multiple_tenants_are_isolated(store):
    link_a = "https://graph.microsoft.com/delta?token=aaaa"
    link_b = "https://graph.microsoft.com/delta?token=bbbb"
    await store.set("servicePrincipals", "tenant-A", link_a)
    await store.set("servicePrincipals", "tenant-B", link_b)

    assert await store.get("servicePrincipals", "tenant-A") == link_a
    assert await store.get("servicePrincipals", "tenant-B") == link_b


async def test_clear_only_affects_target_tenant(store):
    link_a = "https://graph.microsoft.com/delta?token=aaaa"
    link_b = "https://graph.microsoft.com/delta?token=bbbb"
    await store.set("servicePrincipals", "tenant-A", link_a)
    await store.set("servicePrincipals", "tenant-B", link_b)

    await store.clear("servicePrincipals", "tenant-A")

    assert await store.get("servicePrincipals", "tenant-A") is None
    assert await store.get("servicePrincipals", "tenant-B") == link_b


# ── security: delta_link must not appear in logs ──────────────────────────────


async def test_delta_link_never_logged(store, caplog):
    secret_link = "https://graph.microsoft.com/delta?$deltatoken=SUPERSECRETTOKEN12345"
    # Check at INFO level — aiosqlite emits SQL params only at DEBUG (internal lib noise).
    # Our application code must never log the delta_link at INFO or above.
    with caplog.at_level(logging.INFO, logger="itgov.services.delta_token_store"):
        await store.set("servicePrincipals", "tenant-abc", secret_link)
        await store.get("servicePrincipals", "tenant-abc")

    app_logs = caplog.text
    assert "SUPERSECRETTOKEN12345" not in app_logs
    assert secret_link not in app_logs


def test_safe_link_repr_does_not_expose_token():
    link = "https://graph.microsoft.com/delta?$deltatoken=MY_SECRET_TOKEN"
    result = _safe_link_repr(link)
    assert "MY_SECRET_TOKEN" not in result
    assert result.startswith("sha256:")
    assert len(result) < 30
