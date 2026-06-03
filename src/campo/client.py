"""Client for fetching the daily menu from Mensa Campo (Poppelsdorf).

Thin wrapper around the Hofgarten client's AJAX parser — same Studierendenwerk
Bonn endpoint, different canteen ID (2 = Campo).
"""

import pandas as pd

pd.options.future.infer_string = True

CANTEEN_ID = 2

CATEGORIES_WANTED = frozenset(["Unser Spezial", "Hauptgericht", "Suppe & Eintopf"])


def get_menu() -> pd.DataFrame:
    """Fetch today's week menu for Mensa Campo.

    Returns:
        DataFrame with the same columns as hofgarten.client.get_menu():
        day, category, name, is_vegan, price_student, price_staff, price_guest,
        allergens, additives, week_label.
    """
    from hofgarten.client import get_menu as _hg_get_menu

    return _hg_get_menu(canteen_id=CANTEEN_ID)


if __name__ == "__main__":
    df = get_menu()
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_colwidth", 80)
    print(df.to_string(index=False))
