"""
Lot code generation for batches.

Format: [SHORTNAME]-[CATEGORY]-[YYMMDD]-[STAGE]-[BATCH#]
Example: HSKR-LAB-250516-S1-B2

SHORTNAME: derived from ferment name — first letter of each word, max 4 chars,
           uppercased. "House Sauerkraut" → "HSKR", "Kombucha" → "KOM".
CATEGORY:  category name uppercased, max 3 chars. "LAB" → "LAB", "Kombucha" → "KOM".
YYMMDD:    start date of the batch.
STAGE:     S1, S2, S3...
BATCH#:    B1, B2, B3... (sequential per ferment)
"""

import re
from datetime import datetime


def _short_name(ferment_name: str, max_len: int = 4) -> str:
    """
    Derive a short code from the ferment name.
    Takes the first letter of each word, uppercased, up to max_len chars.
    Falls back to the first max_len letters of the first word if single word.

    Examples:
        "House Sauerkraut"  → "HSKR"
        "Kombucha"          → "KOM"
        "Sourdough Starter Rye" → "SSR"
        "Red Wine Vinegar"  → "RWV"
    """
    words = re.split(r"[\s\-_]+", ferment_name.strip())
    words = [w for w in words if w]

    if len(words) == 1:
        code = words[0][:max_len].upper()
    else:
        code = "".join(w[0] for w in words if w)[:max_len].upper()

    # Strip non-alphanumeric
    code = re.sub(r"[^A-Z0-9]", "", code)
    return code or "FERM"


def _short_category(category_name: str | None, max_len: int = 3) -> str:
    """
    Shorten category name to max_len chars, uppercased.
    "LAB" → "LAB", "Kombucha" → "KOM", "Yeast" → "YST"
    """
    if not category_name:
        return "UNK"
    name = category_name.upper().strip()
    # Special cases for known categories
    mapping = {
        "KOMBUCHA": "KOM",
        "YEAST": "YST",
    }
    if name in mapping:
        return mapping[name]
    return re.sub(r"[^A-Z0-9]", "", name)[:max_len]


def generate_lot_code(
    ferment_name: str,
    category_name: str | None,
    started_at: datetime | None,
    stage: int,
    batch_number: int,
) -> str:
    """
    Generate a lot code suggestion.

    Args:
        ferment_name:  name of the ferment project
        category_name: name of the category (LAB, AAB, Kombucha, Yeast)
        started_at:    batch start date (defaults to today if None)
        stage:         batch stage number (1, 2, 3...)
        batch_number:  sequential batch number within the ferment (1, 2, 3...)

    Returns:
        A lot code string e.g. "HSKR-LAB-250516-S1-B1"
    """
    short_name = _short_name(ferment_name)
    category = _short_category(category_name)
    date = (started_at or datetime.now()).strftime("%y%m%d")
    return f"{short_name}-{category}-{date}-S{stage}-B{batch_number}"


def next_batch_number(existing_batches: list) -> int:
    """
    Return the next brew-run number for a ferment.

    Only root batches (no parent) count — stage 2+ children of the same brew
    run share their parent's batch_number, so they must not inflate this count.
    """
    roots = [b for b in existing_batches if not getattr(b, "parent_batch_id", None)]
    if not roots:
        return 1
    return max((b.batch_number or 0) for b in roots) + 1