# /// script
# dependencies = [
#   "requests",
# ]
# ///

"""Ingest any testdata collection and its items during docker-compose."""

import json
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

workingdir = Path(__file__).parent.absolute()
# Try data-warehouse first, fallback to testdata
data_warehouse_dir = Path("/data-warehouse")
# testdata_dir = workingdir.parent / "testdata"
# testdata_dir = data_warehouse_dir if data_warehouse_dir.exists() else testdata_dir


def post_or_put(url: str, data: dict) -> None:
    """Post or put data to url."""
    r = requests.post(url, json=data)
    if r.status_code == 409:
        new_url = url + f"/{data['id']}"
        r = requests.put(new_url, json=data)
        if not r.status_code == 404:
            if r.status_code != 200:
                print(f"Error putting {new_url}: {r.text}")
            r.raise_for_status()
    elif r.status_code != 201 and r.status_code != 200:
        print(f"Error posting to {url}: {r.text}")
        r.raise_for_status()
    else:
        r.raise_for_status()


def ingest_collection_data(collection_name: str, app_host: str) -> None:
    """Ingest collection and items by name from testdata."""
    collection_dir = data_warehouse_dir / collection_name
    collection_path = collection_dir / "collection.json"
    items_dir = collection_dir / "items"

    if not collection_path.exists():
        raise FileNotFoundError(f"collection.json not found for '{collection_name}'.")
    if not items_dir.exists():
        raise FileNotFoundError(f"items directory not found for '{collection_name}'.")

    with collection_path.open() as f:
        collection = json.load(f)

    post_or_put(urljoin(app_host, "/collections"), collection)

    for item_file in items_dir.glob("*.json"):
        with item_file.open() as f:
            item = json.load(f)
        post_or_put(urljoin(app_host, f"collections/{collection['id']}/items"), item)


def _usage() -> None:
    print(
        "Usage: python ingest_collection.py <app_host> <collection_name>\n"
        "Example: python ingest_collection.py http://localhost:8081 landsat"
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        _usage()
        raise SystemExit(1)

    app_host = sys.argv[1]
    collection_name = sys.argv[2]

    if not app_host:
        raise Exception("You must include full path/port to stac instance")

    print(f"Loading {collection_name} Collection")
    ingest_collection_data(collection_name, app_host)
    print("All Done")
