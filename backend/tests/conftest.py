from datetime import date

import pytest


@pytest.fixture(autouse=True)
def _stabilize_dated_flyer_serving_cache_fixtures(request, monkeypatch):
    """Keep historical serving-cache fixtures independent of the wall clock.

    test_flyer_serving_cache uses a fixed August 2026 flyer generation to test
    cache replacement and fallback semantics, not expiration behavior. Freeze
    only that module at a date inside its fixture window.
    """

    if request.module.__name__.split(".")[-1] != "test_flyer_serving_cache":
        return

    from app import luna_overlay

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 22)

    monkeypatch.setattr(luna_overlay, "date", FixedDate)
