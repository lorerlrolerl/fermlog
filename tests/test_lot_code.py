"""Lot code generation — unit tests for the generator logic."""
from datetime import datetime
from app.lot_code import generate_lot_code, next_batch_number, _short_name, _short_category


def test_short_name_single_word():
    assert _short_name("Kombucha") == "KOMB"

def test_short_name_two_words():
    assert _short_name("House Sauerkraut") == "HS"

def test_short_name_three_words():
    assert _short_name("Red Wine Vinegar") == "RWV"

def test_short_name_four_words():
    assert _short_name("Wild Garlic Soy Sauce") == "WGSS"

def test_short_name_empty_fallback():
    assert _short_name("") == "FERM"

def test_short_category_lab():
    assert _short_category("LAB") == "LAB"

def test_short_category_kombucha():
    assert _short_category("Kombucha") == "KOM"

def test_short_category_yeast():
    assert _short_category("Yeast") == "YST"

def test_short_category_aab():
    assert _short_category("AAB") == "AAB"

def test_short_category_none():
    result = _short_category(None)
    assert result == "UNK"

def test_generate_lot_code_format():
    dt = datetime(2025, 5, 16)
    code = generate_lot_code("House Sauerkraut", "LAB", dt, stage=1, batch_number=1)
    assert code == "HS-LAB-250516-S1-B1"

def test_generate_lot_code_stage2():
    dt = datetime(2025, 6, 1)
    code = generate_lot_code("Kombucha", "Kombucha", dt, stage=2, batch_number=3)
    assert code == "KOMB-KOM-250601-S2-B3"

def test_generate_lot_code_no_category():
    dt = datetime(2025, 1, 1)
    code = generate_lot_code("Water Kefir", None, dt, stage=1, batch_number=1)
    assert "UNK" in code
    assert "S1-B1" in code

def test_generate_lot_code_no_date():
    code = generate_lot_code("Test", "LAB", None, stage=1, batch_number=1)
    assert code is not None
    assert "TEST" in code or "T-" in code

def test_next_batch_number_empty():
    assert next_batch_number([]) == 1

def test_next_batch_number_sequential():
    class B:
        def __init__(self, n): self.batch_number = n; self.parent_batch_id = None
    assert next_batch_number([B(1), B(2), B(3)]) == 4

def test_next_batch_number_with_gaps():
    class B:
        def __init__(self, n): self.batch_number = n; self.parent_batch_id = None
    assert next_batch_number([B(1), B(3), B(5)]) == 6

def test_next_batch_number_ignores_children():
    """Stage 2+ children share their parent's batch_number; they must not inflate the count."""
    class B:
        def __init__(self, n, parent=None): self.batch_number = n; self.parent_batch_id = parent
    root  = B(1, parent=None)
    child = B(1, parent=root)   # same B number, has a parent
    assert next_batch_number([root, child]) == 2