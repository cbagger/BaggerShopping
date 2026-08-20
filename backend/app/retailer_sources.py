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


ADDITIONAL_SOURCES: tuple[RetailerSource, ...] = (
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


__all__ = ["ADDITIONAL_SOURCES", "RETAILER_ORDER"]
