# /// script
# dependencies = [
#   "requests",
#   "pypgstac[psycopg]",
# ]
# ///

"""Ingest any testdata collection and its items during docker-compose.

Uses pypgstac bulk loading via PostgreSQL COPY for 100-500x faster ingestion
compared to HTTP requests. Falls back to HTTP if database connection unavailable.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    """Ingest collection and items by name from testdata.

    Attempts to use pypgstac bulk loading (100-500x faster) if database
    connection is available, otherwise falls back to HTTP ingestion.
    """
    collection_dir = data_warehouse_dir / collection_name
    collection_path = collection_dir / "collection.json"
    items_dir = collection_dir / "items"

    if not collection_path.exists():
        raise FileNotFoundError(f"collection.json not found for '{collection_name}'.")
    if not items_dir.exists():
        raise FileNotFoundError(f"items directory not found for '{collection_name}'.")

    with collection_path.open() as f:
        collection = json.load(f)

    # Try bulk loading via database first (much faster)
    if _try_bulk_load(collection, items_dir, collection_name):
        logger.info(f"✓ Successfully ingested {collection_name} via bulk loading")
        return

    # Fall back to HTTP ingestion
    logger.info(f"Falling back to HTTP ingestion for {collection_name}")
    _ingest_via_http(collection, items_dir, app_host)


def _try_bulk_load(collection: dict, items_dir: Path, collection_name: str) -> bool:
    """Attempt to ingest using pypgstac bulk loading.

    Returns True if successful, False if database not available.
    """
    try:
        from pypgstac.db import PgstacDB
        from pypgstac.load import Loader, Methods
    except ImportError:
        logger.warning("pypgstac not installed, using HTTP fallback")
        return False

    try:
        # Get database connection from environment variables or DSN
        dsn = _get_db_connection_string()
        if not dsn:
            logger.warning("Database connection not configured, using HTTP fallback")
            return False

        db = PgstacDB(dsn=dsn)
        loader = Loader(db=db)

        # Ingest collection first
        start_time = time.time()
        logger.info(f"Loading collection: {collection_name}")
        loader.load_collections([collection], insert_mode=Methods.upsert)

        # Ingest items via generator (streaming, memory efficient)
        logger.info(f"Loading items from {items_dir}...")
        items_generator = _item_generator(items_dir)
        loader.load_items(items_generator, insert_mode=Methods.upsert, chunksize=10000)

        elapsed = time.time() - start_time
        item_count = len(list(items_dir.glob("*.json")))
        rate = item_count / elapsed if elapsed > 0 else 0
        logger.info(f"Completed in {elapsed:.1f}s ({rate:.0f} items/sec)")

        return True

    except Exception as e:
        logger.warning(f"Bulk loading failed: {e}. Using HTTP fallback.")
        return False


def _get_db_connection_string() -> Optional[str]:
    """Build PostgreSQL connection string from environment variables."""
    host = os.getenv("PGHOST") or os.getenv("postgres_host_writer") or "database"
    port = os.getenv("PGPORT") or os.getenv("postgres_port") or "5432"
    user = os.getenv("PGUSER") or os.getenv("postgres_user") or "postgres"
    password = os.getenv("PGPASSWORD") or os.getenv("postgres_pass")
    dbname = os.getenv("PGDATABASE") or os.getenv("postgres_dbname") or "postgres"

    if not password:
        return None  # Can't connect without password

    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def _item_generator(items_dir: Path):
    """Generator that yields sanitized items from individual JSON files."""
    for item_file in sorted(items_dir.glob("*.json")):
        try:
            with item_file.open() as f:
                item = json.load(f)
            yield sanitize_item(item)
        except Exception as e:
            logger.error(f"Error loading {item_file}: {e}")
            # Continue with next item instead of failing


def _ingest_via_http(collection: dict, items_dir: Path, app_host: str) -> None:
    """Fallback HTTP-based ingestion (slow but reliable)."""
    post_or_put(urljoin(app_host, "/collections"), collection)

    item_files = list(items_dir.glob("*.json"))
    for idx, item_file in enumerate(item_files, 1):
        with item_file.open() as f:
            item = json.load(f)
        item = sanitize_item(item)
        post_or_put(urljoin(app_host, f"collections/{collection['id']}/items"), item)

        if idx % 100 == 0:
            logger.info(f"  Ingested {idx}/{len(item_files)} items...")


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
