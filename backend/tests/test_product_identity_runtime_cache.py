from __future__ import annotations

from app import product_identity


def _reset_cache() -> None:
    with product_identity._STORE_CACHE_LOCK:
        product_identity._STORE_CACHE = None
        product_identity._STORE_CACHE_PATH = None
        product_identity._STORE_CACHE_SIGNATURE = None
        product_identity._STORE_CACHE_CHECKED_AT = 0.0
        product_identity._ANALYSIS_GENERATION = 0
        product_identity._cached_analysis_value.cache_clear()


def test_product_identity_store_is_not_reloaded_for_each_compare(monkeypatch, tmp_path):
    calls = 0
    path = tmp_path / "identity.json"
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(path))

    def load_store(_path):
        nonlocal calls
        calls += 1
        return {"matches": {}}

    monkeypatch.setattr(product_identity, "_read_store_from_disk", load_store)
    monkeypatch.setattr(product_identity, "_store_file_signature", lambda _path: (1, 10))
    _reset_cache()

    first = product_identity._load_store()
    second = product_identity._load_store()

    assert first == {"matches": {}}
    assert second == first
    assert calls == 1


def test_identical_product_analysis_is_computed_once(monkeypatch, tmp_path):
    calls = 0
    path = tmp_path / "identity.json"
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(path))
    monkeypatch.setattr(product_identity, "_read_store_from_disk", lambda _path: {})
    monkeypatch.setattr(product_identity, "_store_file_signature", lambda _path: None)

    original = product_identity._analyze_uncached

    def analyze(value, *, quantity=None, unit=None, price=None):
        nonlocal calls
        calls += 1
        return original(value, quantity=quantity, unit=unit, price=price)

    monkeypatch.setattr(product_identity, "_analyze_uncached", analyze)
    _reset_cache()

    first = product_identity.analyze("Lurpak 200 g", price=20)
    second = product_identity.analyze("Lurpak 200 g", price=20)

    assert first is second
    assert calls == 1


def test_product_identity_save_refreshes_runtime_cache(monkeypatch, tmp_path):
    path = tmp_path / "identity.json"
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(path))
    _reset_cache()

    product_identity._save_store({"matches": {"a|b": "same_item"}})
    loaded = product_identity._load_store()

    assert loaded == {"matches": {"a|b": "same_item"}}
    assert product_identity._ANALYSIS_GENERATION == 1
