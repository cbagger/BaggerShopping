import json

from app import product_identity


def _reset_cache():
    with product_identity._STORE_CACHE_LOCK:
        product_identity._STORE_CACHE = None
        product_identity._STORE_CACHE_PATH = None
        product_identity._STORE_CACHE_SIGNATURE = None
        product_identity._STORE_CACHE_CHECKED_AT = 0.0
        product_identity._ANALYSIS_GENERATION = 0
        product_identity._cached_analysis_value.cache_clear()


def test_repeated_analysis_reuses_first_class_store_and_analysis_cache(monkeypatch, tmp_path):
    _reset_cache()
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({"aliases": {}, "families": {}, "matches": {}}), encoding="utf-8")
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(path))

    original = product_identity._read_store_from_disk
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(product_identity, "_read_store_from_disk", counted)

    first = product_identity.analyze("Lurpak smørbar 200 g")
    second = product_identity.analyze("Lurpak smørbar 200 g")

    assert first is second
    assert len(calls) == 1


def test_save_invalidates_analysis_cache_and_exposes_learning_immediately(monkeypatch, tmp_path):
    _reset_cache()
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({"aliases": {}, "families": {}, "matches": {}}), encoding="utf-8")
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(path))

    before = product_identity.analyze("Testmælk")
    assert before.canonical_id is None

    store = product_identity._load_store()
    store.setdefault("aliases", {})[product_identity.normalize("Testmælk")] = "product:test-milk"
    product_identity._save_store(store)

    after = product_identity.analyze("Testmælk")

    assert after is not before
    assert after.canonical_id == "product:test-milk"


def test_store_path_change_never_reuses_cached_analysis_from_previous_store(monkeypatch, tmp_path):
    _reset_cache()
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    key = product_identity.normalize("Testmælk")

    first_path.write_text(json.dumps({
        "aliases": {key: "product:first"},
        "families": {},
        "matches": {},
    }), encoding="utf-8")
    second_path.write_text(json.dumps({
        "aliases": {key: "product:second"},
        "families": {},
        "matches": {},
    }), encoding="utf-8")

    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(first_path))
    assert product_identity.analyze("Testmælk").canonical_id == "product:first"

    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(second_path))
    assert product_identity.analyze("Testmælk").canonical_id == "product:second"
