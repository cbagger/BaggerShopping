from app.flyer_intelligence import extract_variants


def names(heading: str, description: str | None = None, payload=None) -> list[str]:
    return [
        value.name
        for value in extract_variants(
            "campaign", heading, description, payload=payload,
        )
    ]


def test_v2_prefers_explicit_structured_products_and_strips_sizes():
    result = extract_variants(
        "bread",
        "Schulstad brød",
        "Udvalgte varianter. 470-1080 g.",
        payload={
            "products": [
                {"name": "Schulstad Det Gode Solsikkerugbrød 950 g"},
                {"name": "Schulstad Levebrød Sandwich 725 g"},
                {"name": "Schulstad Signaturbrød 750 g"},
            ],
        },
    )

    assert [value.name for value in result] == [
        "Schulstad Det Gode Solsikkerugbrød",
        "Schulstad Levebrød Sandwich",
        "Schulstad Signaturbrød",
    ]
    assert all(value.source == "structured-products" for value in result)
    assert min(value.confidence for value in result) >= 0.95


def test_v2_restores_shared_text_context_without_weight_or_image_data():
    assert names("Lurpak smør eller smørbar 200-250 g") == [
        "Lurpak smør",
        "Lurpak smørbar",
    ]
    assert names("Tulip bacon i skiver eller i tern 150-200 g") == [
        "Tulip bacon i skiver",
        "Tulip bacon i tern",
    ]
    assert names("Kalkunoverlår eller -schnitzel af brystfilet") == [
        "Kalkunoverlår",
        "Kalkunschnitzel af brystfilet",
    ]
    assert names("Coop kyllingeover- eller underlår") == [
        "Coop kyllingeover",
        "Coop kyllingeunderlår",
    ]


def test_v2_keeps_independent_brands_independent():
    assert names("Tuborg Classic eller Carlsberg Pilsner") == [
        "Tuborg Classic",
        "Carlsberg Pilsner",
    ]
    assert names("AMA fedtstof eller Bakkedal smørbar") == [
        "AMA fedtstof",
        "Bakkedal smørbar",
    ]
    assert names("Coca-Cola, Fanta eller Squash sodavand") == [
        "Coca-Cola",
        "Fanta",
        "Squash sodavand",
    ]


def test_v2_does_not_treat_generic_items_or_image_labels_as_variants():
    result = names(
        "Bakkedal smørbar 200-500 g",
        payload={
            "items": [
                {"title": "Ingredienser"},
                {"title": "Næringsindhold"},
            ],
            "image_labels": ["AMA", "Bakkedal", "smør"],
            "vision": {"choices": ["Forkert billedvariant"]},
        },
    )

    # A generic provider 'items' collection and image-derived labels are not
    # trusted sources. With no explicit textual choices, the campaign remains a
    # single unresolved product rather than inventing variants.
    assert result == ["Bakkedal smørbar"]


def test_v2_uses_provider_description_and_handles_brand_punctuation():
    assert names(
        "Sodavand",
        "Frit valg mellem Pepsi Max, Faxe Kondi eller Squash. Maks. 6 stk.",
    ) == ["Pepsi Max", "Faxe Kondi", "Squash"]

    assert names(
        "Xtra! tun",
        payload={"description": "Xtra! tun i vand eller Xtra! tun i olie. Frit valg. 56 g."},
    ) == ["Xtra! tun i vand", "Xtra! tun i olie"]

    assert names(
        "Pesto",
        "Ingredienser: basilikum, olie eller ost. Opbevares på køl.",
    ) == ["Pesto"]


def test_v2_structured_choices_ignore_misleading_variant_weights():
    result = names(
        "AMA fedtstof eller Bakkedal smørbar",
        payload={
            "variants": [
                {"name": "AMA fedtstof 500 g", "quantity": {"from": 500}},
                {"name": "Bakkedal smørbar 200 g", "quantity": {"from": 200}},
            ],
            "quantity": {"size": {"from": 500}, "unit": {"symbol": "g"}},
        },
    )

    assert result == ["AMA fedtstof", "Bakkedal smørbar"]
