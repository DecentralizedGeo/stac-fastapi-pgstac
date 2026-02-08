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


def sanitize_item(item: dict) -> dict:
    """Normalize link and asset media types so the server validation accepts them.

    - Map unknown `links[].type` values to `application/json`.
    - Map unknown asset `type` values to `application/octet-stream`.
    - Specifically maps `application/vnd.nasa.cmr.umm+json` -> `application/json`.
    """
    # Allowed media types (subset matching server validation)
    allowed = {
        "image/tiff; application=geotiff",
        "image/tiff; application=geotiff; profile=cloud-optimized",
        "image/jp2",
        "image/png",
        "image/jpeg",
        "application/geo+json",
        "application/geo+json-seq",
        "application/geopackage+sqlite3",
        "application/vnd.google-earth.kml+xml",
        "application/vnd.google-earth.kmz",
        "application/x-protobuf",
        "application/vnd.mapbox-vector-tile",
        "application/x-hdf",
        "application/x-hdf5",
        "application/xml",
        "application/json",
        "application/ndjson",
        "text/html",
        "text/plain",
        "application/vnd.oai.openapi+json;version=3.0",
        "application/vnd.oai.openapi;version=3.0",
        "application/schema+json",
        "application/pdf",
        "text/csv",
        "application/vnd.apache.parquet",
    }

    # Sanitize links
    links = item.get("links") or []
    for link in links:
        t = link.get("type")
        if not t:
            continue
        if t == "application/vnd.nasa.cmr.umm+json":
            link["type"] = "application/json"
        elif t not in allowed:
            link["type"] = "application/json"

    # Sanitize assets
    assets = item.get("assets") or {}
    for k, asset in assets.items():
        if isinstance(asset, dict):
            t = asset.get("type")
            if t and t not in allowed:
                asset["type"] = "application/octet-stream"

    return item


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
        # Normalize media types to satisfy server enum validation
        # This is needed to ingest some of the testdata collections which have media types that don't match the server's allowed
        # list. For example, the CMR UMM items use `application/vnd.nasa.cmr.umm+json` which is not in the server's allowed list,
        # so we map it to `application/json`.
        # see ..\site-packages\stac_pydantic\shared.py for list of media types allowed by the server validation
        item = sanitize_item(item)
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
