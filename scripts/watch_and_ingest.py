#!/usr/bin/env python
"""
Watch and ingest script with smart change detection using file modification timestamps.

Monitors /data-warehouse for collection.json changes and automatically
re-ingests collections when their content changes (based on mtime).
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, Optional

# Disable buffering for immediate output
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 1)


class CollectionWatcher:
    def __init__(self, api_url: str, data_warehouse: str = "/data-warehouse",
                 check_interval: int = 300, state_file: str = "/tmp/collection_hashes.json"):
        """
        Initialize the collection watcher.

        Args:
            api_url: Base URL of the STAC API (e.g., http://app:8083)
            data_warehouse: Path to data warehouse directory
            check_interval: Seconds between checks (default 5 minutes)
            state_file: File to persist collection hashes
        """
        self.api_url = api_url
        self.data_warehouse = Path(data_warehouse)
        self.check_interval = check_interval
        self.state_file = Path(state_file)
        self.hashes: Dict[str, str] = self._load_hashes()

    def _load_hashes(self) -> Dict[str, str]:
        """Load previously saved collection hashes."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load state file: {e}")
        return {}

    def _save_hashes(self) -> None:
        """Save collection hashes to state file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.hashes, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save state file: {e}")

    def _get_file_mtime(self, file_path: Path) -> float:
        """Get modification time of a file."""
        return file_path.stat().st_mtime

    def _get_directory_mtime(self, dir_path: Path) -> float:
        """Get the modification time of the directory itself."""
        if dir_path.exists() and dir_path.is_dir():
            try:
                return dir_path.stat().st_mtime
            except Exception as e:
                print(f"    Warning: Could not check directory {dir_path}: {e}")
        return 0

    def _find_collections(self) -> Dict[str, Path]:
        """Find all collection.json files in data warehouse."""
        collections = {}
        if not self.data_warehouse.exists():
            print(f"Error: Data warehouse not found at {self.data_warehouse}")
            return collections

        for collection_dir in self.data_warehouse.iterdir():
            if collection_dir.is_dir():
                collection_json = collection_dir / "collection.json"
                if collection_json.exists():
                    collection_name = collection_dir.name
                    collections[collection_name] = collection_json

        return collections

    def _format_duration(self, seconds: float) -> str:
        """Format duration as seconds or minutes."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        else:
            minutes = seconds / 60
            return f"{minutes:.1f}m"

    def _format_mtime(self, mtime: float) -> str:
        """Format modification time as human-readable datetime string."""
        from datetime import datetime
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    def _ingest_collection(self, collection_name: str) -> bool:
        """Ingest a collection via the API."""
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ingesting collection: {collection_name}")
        start_time = time.time()
        try:
            # Call the existing ingest script
            result = subprocess.run(
                [
                    sys.executable,
                    "/app/scripts/ingest_collection.py",
                    self.api_url,
                    collection_name
                ],
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout
            )

            elapsed_time = time.time() - start_time
            duration_str = self._format_duration(elapsed_time)

            if result.returncode == 0:
                print(f"✓ Successfully ingested {collection_name} ({duration_str})")
                return True
            else:
                print(f"✗ Failed to ingest {collection_name} ({duration_str})")
                print(f"  STDOUT: {result.stdout[:200]}")
                print(f"  STDERR: {result.stderr[:200]}")
                return False
        except subprocess.TimeoutExpired:
            elapsed_time = time.time() - start_time
            duration_str = self._format_duration(elapsed_time)
            print(f"✗ Ingest timed out for {collection_name} ({duration_str})")
            return False
        except Exception as e:
            elapsed_time = time.time() - start_time
            duration_str = self._format_duration(elapsed_time)
            print(f"✗ Error ingesting {collection_name} ({duration_str}): {e}")
            return False

    def check_and_ingest(self) -> None:
        """Check for collection and items changes and ingest if needed."""
        collections = self._find_collections()

        if not collections:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No collections found", flush=True)
            return

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checking {len(collections)} collection(s)", flush=True)

        changes_detected = False

        for collection_name, collection_json_path in collections.items():
            try:
                collection_dir = collection_json_path.parent
                items_dir = collection_dir / "items"

                # Get modification times for both collection.json and items directory
                collection_mtime = self._get_file_mtime(collection_json_path)
                items_mtime = self._get_directory_mtime(items_dir)

                # Combine mtimes - use the later one
                current_mtime = max(collection_mtime, items_mtime)
                previous_mtime = self.hashes.get(collection_name)

                if previous_mtime is None:
                    print(f"  [NEW] {collection_name}", flush=True)
                    changes_detected = True
                    self._ingest_collection(collection_name)
                    self.hashes[collection_name] = current_mtime

                elif current_mtime != previous_mtime:
                    print(f"  [CHANGED] {collection_name}", flush=True)
                    print(f"    Previous mtime: {self._format_mtime(previous_mtime)}", flush=True)
                    print(f"    Current mtime:  {self._format_mtime(current_mtime)}", flush=True)
                    print(f"    (collection.json: {self._format_mtime(collection_mtime)}, items/: {self._format_mtime(items_mtime)})", flush=True)
                    changes_detected = True
                    self._ingest_collection(collection_name)
                    self.hashes[collection_name] = current_mtime

                else:
                    print(f"  [UNCHANGED] {collection_name}", flush=True)

            except Exception as e:
                print(f"  [ERROR] {collection_name}: {e}", flush=True)

        # Check for deleted collections
        deleted_collections = set(self.hashes.keys()) - set(collections.keys())
        if deleted_collections:
            print(f"  [DELETED] {', '.join(deleted_collections)}", flush=True)
            for deleted in deleted_collections:
                del self.hashes[deleted]
            changes_detected = True

        if changes_detected:
            self._save_hashes()

    def run(self) -> None:
        """Run the watcher continuously."""
        print(f"Starting collection watcher", flush=True)
        print(f"  API URL: {self.api_url}", flush=True)
        print(f"  Data warehouse: {self.data_warehouse}", flush=True)
        print(f"  Check interval: {self.check_interval}s", flush=True)
        print(f"  State file: {self.state_file}", flush=True)
        print(flush=True)

        try:
            while True:
                self.check_and_ingest()
                print(f"  Waiting {self.check_interval}s until next check...", flush=True)
                print(flush=True)
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print("\nWatcher stopped")
            self._save_hashes()
            sys.exit(0)

        except Exception as e:
            print(f"Fatal error: {e}")
            self._save_hashes()
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: watch_and_ingest.py <api_url> [check_interval_seconds] [state_file]")
        print("Example: watch_and_ingest.py http://app:8083 300")
        print("Example: watch_and_ingest.py http://app:8083 300 /app/state/collection_hashes.json")
        sys.exit(1)

    api_url = sys.argv[1]
    check_interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    state_file = sys.argv[3] if len(sys.argv) > 3 else "/tmp/collection_hashes.json"

    watcher = CollectionWatcher(api_url, check_interval=check_interval, state_file=state_file)
    watcher.run()
