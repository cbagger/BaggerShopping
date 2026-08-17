from app.luna_overlay import _safe_luna_variant_name


def test_luna_variant_guard_rejects_size_only_values():
    assert _safe_luna_variant_name("500-1000 g") is None
    assert _safe_luna_variant_name("6 x 330 ml") is None
    assert _safe_luna_variant_name("2 stk") is None
    assert _safe_luna_variant_name("Flere varianter") is None
    assert _safe_luna_variant_name("Frit valg") is None


def test_luna_variant_guard_keeps_named_variants():
    assert _safe_luna_variant_name("Kylling og karry") == "Kylling og karry"
    assert _safe_luna_variant_name("Laks") == "Laks"
    assert _safe_luna_variant_name("Coca-Cola Zero 1,5 l") == "Coca-Cola Zero 1,5 l"
