import asyncio

from app import mobile_offers


def test_shelf_reports_background_refresh_until_finished(monkeypatch):
    async def scenario():
        release = asyncio.Event()
        refresh = asyncio.create_task(release.wait())
        monkeypatch.setattr(mobile_offers, "_publication_refresh_task", refresh)

        async def cached():
            return []

        monkeypatch.setattr(mobile_offers, "_publications", cached)
        try:
            assert (await mobile_offers.publications())["refresh_pending"] is True
            release.set()
            await refresh
            assert (await mobile_offers.publications())["refresh_pending"] is False
        finally:
            refresh.cancel()

    asyncio.run(scenario())
