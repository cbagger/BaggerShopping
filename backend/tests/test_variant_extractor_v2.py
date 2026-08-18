from app.flyer_intelligence import extract_variants


def names(heading: str, description: str | None = None, payload=None) -> list[str]:
    return [
        value.name
        for value in extract_variants(
            "campaign", heading, description, payload=payload,
        )
    ]


def test_v3_prefers_explicit_structured_products_and_keeps_sizes():
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
        "Schulstad Det Gode Solsikkerugbrød 950 g",
        "Schulstad Levebrød Sandwich 725 g",
        "Schulstad Signaturbrød 750 g",
    ]
    assert all(value.source == "structured-products-v3" for value in result)
    assert min(value.confidence for value in result) >= 0.95


def test_v3_preserves_pack_count_for_explicit_product_choice():
    assert names(
        "Quickbury Fastfood Buns",
        payload={
            "variants": [
                {"name": "Hamburger Buns 6 Stk"},
                {"name": "Hotdog Buns 8 Stk"},
            ]
        },
    ) == ["Hamburger Buns 6 Stk", "Hotdog Buns 8 Stk"]


def test_v3_accepts_generic_items_only_when_they_are_product_records():
    assert names(
        "Boller",
        payload={
            "items": [
                {"id": "sku-1", "name": "Hamburger Buns 6 Stk"},
                {"id": "sku-2", "name": "Hotdog Buns 8 Stk"},
            ]
        },
    ) == ["Hamburger Buns 6 Stk", "Hotdog Buns 8 Stk"]


def test_v3_restores_shared_text_context_without_image_data():
    assert names("Lurpak smør eller smørbar 200-250 g") == [
        "Lurpak smør",
        "Lurpak smørbar 200-250 g",
    ]
    assert names("Tulip bacon i skiver eller i tern 150-200 g") == [
        "Tulip bacon i skiver",
        "Tulip bacon i tern 150-200 g",
    ]
    assert names("Kalkunoverlår eller -schnitzel af brystfilet") == [
        "Kalkunoverlår",
        "Kalkunschnitzel af brystfilet",
    ]
    assert names("Coop kyllingeover- eller underlår") == [
        "Coop kyllingeover",
        "Coop kyllingeunderlår",
    ]


def test_v3_keeps_independent_brands_independent():
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


def test_v3_does_not_treat_unidentified_generic_items_or_image_labels_as_variants():
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

    assert result == ["Bakkedal smørbar"]


def test_v3_uses_provider_description_and_handles_brand_punctuation():
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


def test_v3_structured_choices_keep_variant_specific_weights():
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

    assert result == ["AMA fedtstof 500 g", "Bakkedal smørbar 200 g"]
