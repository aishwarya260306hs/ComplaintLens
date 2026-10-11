"""Load and validate the replaceable organization directory dataset."""
import json
from pathlib import Path


DEFAULT_DATASET = Path(__file__).resolve().parent / "data" / "organizations.json"
SUPPORTED_SECTORS = {"education", "healthcare", "government"}


def load_dataset(path=None):
    dataset_path = Path(path) if path else DEFAULT_DATASET
    with dataset_path.open(encoding="utf-8") as source:
        dataset = json.load(source)
    if not isinstance(dataset, dict) or not isinstance(dataset.get("organizations"), list):
        raise ValueError("Organization dataset must contain an organizations array.")

    seen = set()
    identities = set()
    for item in dataset["organizations"]:
        required = ("dataset_id", "name", "sector", "city", "state", "organization_type", "source_url")
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip()
                                               for key in required):
            raise ValueError("Every organization needs an ID, name, sector, city, state, type, and source URL.")
        if item["sector"].lower() not in SUPPORTED_SECTORS:
            raise ValueError(f"Unsupported dataset sector: {item['sector']}")
        key = item["dataset_id"].strip().lower()
        if key in seen:
            raise ValueError(f"Duplicate organization dataset ID: {item['dataset_id']}")
        seen.add(key)
        identity = tuple(item[field].strip().casefold() for field in ("name", "sector", "city"))
        if identity in identities:
            raise ValueError(f"Duplicate organization entry: {item['name']}")
        identities.add(identity)
    return dataset


def dataset_index(dataset):
    return {
        (item["name"].strip().casefold(), item["sector"].strip().casefold(),
         item["city"].strip().casefold()): item
        for item in dataset["organizations"]
    }
