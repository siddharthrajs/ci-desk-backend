import json
from pathlib import Path


def load_instruments() -> dict:
    path = Path(__file__).parent.parent.parent / "instruments.json"
    with open(path, "r") as f:
        return json.load(f)


def get_item_ids(instruments: dict) -> list[str]:
    suffix = instruments["stream"]["item_suffix"]
    item_ids = []
    for product_data in instruments["products"].values():
        if not product_data.get("enabled", True):
            continue
        for instr in product_data["instruments"]:
            if instr.get("enabled", True) and instr.get("instrument_id"):
                item_ids.append(instr["instrument_id"] + suffix)
    return item_ids


def build_lookup(instruments: dict) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for product, product_data in instruments["products"].items():
        if not product_data.get("enabled", True):
            continue
        for instr in product_data["instruments"]:
            if instr.get("enabled", True) and instr.get("instrument_id"):
                lookup[instr["instrument_id"]] = {
                    "product": product,
                    "tenor": instr["tenor"],
                    "label": instr["label"],
                    "instrument_type": instr["instrument_type"],
                }
    return lookup
