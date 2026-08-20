from __future__ import annotations

from .flyer_adapters import RetailerSource


RETAILER_ORDER = (
    "MENY",
    "365discount",
    "REMA 1000",
    "Bilka",
    "føtex",
    "Lidl",
    "Netto",
    "SPAR",
    "SuperBrugsen",
    "Kvickly",
    "Brugsen",
    "Min Købmand",
    "LET-KØB",
)


# Customer-facing retailer registry. MENY has its dedicated iPaper fetcher;
# every other chain is described here and flows through the same provider,
# enrichment, readiness, search and notification pipeline.
SOURCES: tuple[RetailerSource, ...] = (
    RetailerSource(
        "365discount",
        "https://365discount.coop.dk/365avis/",
        ("365discount.coop.dk", "tjek.com", "ipaper.io"),
        tjek_dealer_id="DWZE1w",
    ),
    RetailerSource(
        "REMA 1000",
        "https://rema1000.dk/avis",
        ("avis.rema1000.dk", "ipaper.io", "view.publitas.com"),
        tjek_dealer_id="11deC",
    ),
    RetailerSource(
        "Bilka",
        "https://www.bilka.dk/bilkaavisen/",
        ("avis.bilka.dk",),
        tjek_dealer_id="93f13",
    ),
    RetailerSource(
        "føtex",
        "https://www.foetex.dk/foetex-avis/",
        ("avis.foetex.dk",),
        tjek_dealer_id="bdf5A",
    ),
    RetailerSource(
        "Lidl",
        "https://www.lidl.dk/c/tilbudsavis/s10013730",
        ("leaflets.schwarz", "lidl.dk"),
        tjek_dealer_id="71c90",
    ),
    RetailerSource(
        "Netto",
        "https://netto.dk/netto-avisen/",
        ("viewer.ipaper.io", "netto.dk", "tjek.com"),
        tjek_dealer_id="9ba51",
    ),
    RetailerSource(
        "SPAR",
        "https://spar.dk/ugensavis",
        ("ipaper.io", "view.publitas.com", "spar.dk"),
        tjek_dealer_id="88ddE",
    ),
    RetailerSource(
        "SuperBrugsen",
        "https://superbrugsen.coop.dk/avis/",
        ("superbrugsen.coop.dk", "tjek.com", "image-transformer-api.tjek.com"),
        tjek_dealer_id="0b1e8",
    ),
    RetailerSource(
        "Kvickly",
        "https://kvickly.coop.dk/avis/",
        ("kvickly.coop.dk", "tjek.com", "image-transformer-api.tjek.com"),
        tjek_dealer_id="c1edq",
    ),
    RetailerSource(
        "Brugsen",
        "https://brugsen.coop.dk/avis/",
        ("brugsen.coop.dk", "tjek.com", "image-transformer-api.tjek.com"),
        tjek_dealer_id="d311fg",
    ),
    RetailerSource(
        "Min Købmand",
        "https://etilbudsavis.dk/Min-Kobmand",
        ("etilbudsavis.dk", "tjek.com", "image-transformer-api.tjek.com"),
        tjek_dealer_id="603dfL",
    ),
    RetailerSource(
        "LET-KØB",
        "https://etilbudsavis.dk/LET-KOB",
        ("etilbudsavis.dk", "tjek.com", "image-transformer-api.tjek.com"),
        tjek_dealer_id="f6f54",
    ),
)


__all__ = ["RETAILER_ORDER", "SOURCES"]
