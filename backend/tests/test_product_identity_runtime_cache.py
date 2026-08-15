from __future__ import annotations

import app


def _reset_cache() -> None:
    app._product_store_cache = None
    app._product_store_signature = None
    app._product_store_checked_at = 0.0


def test_product_identity_store_is_not_reloaded_for_each_compare(monkeypatch):
    calls = 0

    def load_store():
        nonlocal calls
        calls += 1
        return {"matches": {}}

    monkeypatch.setattr(app, "_original_product_load_store", load_store)
    monkeypatch.setattr(app, "_product_store_file_signature", lambda: (1, 10))
    _reset_cache()

    first = app._cached_product_load_store()
    second = app._cached_product_load_store()

    assert first == {"matches": {}}
    assert second == first
    assert calls == 1


def test_product_identity_save_refreshes_runtime_cache(monkeypatch):
    saved = []

    monkeypatch.setattr(app, "_original_product_save_store", lambda value: saved.append(value.copy()))
    monkeypatch.setattr(app, "_product_store_file_signature", lambda: (2, 20))
    _reset_cache()

    app._cached_product_save_store({"matches": {"a|b": "same_item"}})
    loaded = app._cached_product_load_store()

    assert saved == [{"matches": {"a|b": "same_item"}}]
    assert loaded == {"matches": {"a|b": "same_item"}}
