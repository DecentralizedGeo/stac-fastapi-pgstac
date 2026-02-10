# Item-Level Change Detection with Manifest Files

**Status**: Deferred (V1 uses collection-level change detection)  
**Priority**: Medium  
**Complexity**: Medium

---

## Overview

Currently, when a collection is detected as changed, the entire collection (including all items) is re-ingested into the STAC API. For large collections with thousands or tens of thousands of items, this is inefficient when only a few items have actually changed.

This feature proposes implementing item-level granularity for change detection, allowing the watcher to identify and ingest only the specific items that have been added or modified.

---

## Current Limitation

**Problem**: Collection-level hashing
- Collection A has 85,000 items
- 10 items are added/modified
- Currently: All 85,000+ items are re-ingested
- Result: Significant performance waste and unnecessary API calls

**Current Approach**:
```
collection_hashes.json (collection-level only)
{
  "GEDI_L3_LandSurface_Metrics_V2_1952_2": 1234567890.123,
  "GEDI_L4A_AGB_Density_V2_1_2056_2.1": 1234567891.456
}
```

---

## Proposed Solution

Implement **per-collection manifest files** that track individual item mtimes:

### Manifest Structure

```json
// File: /data-warehouse/GEDI_L3_LandSurface_Metrics_V2_1952_2/.manifest.json
{
  "manifest_version": 1,
  "collection_name": "GEDI_L3_LandSurface_Metrics_V2_1952_2",
  "last_sync": 1234567890.123,
  "items": {
    "GEDI_L3_counts_2019108_2020287_002_02.tif.json": 1234567890.123,
    "GEDI_L3_counts_2019108_2021104_002_02.tif.json": 1234567891.456,
    "GEDI_L3_elev_lowestmode_mean_2019108_2020287_002_02.tif.json": 1234567892.789,
    // ... remaining items
  }
}
```

### Implementation Approach

1. **Change Detection**:
   - Compare directory listing against manifest
   - Identify added files (not in manifest)
   - Identify deleted files (in manifest but not on disk)
   - For existing files, compare mtime with manifest value

2. **Processing**:
   - Only ingest changed/new items
   - Update manifest with new mtimes after successful ingest
   - Optionally: Implement item-level rollback mechanism

3. **Fallback Behavior**:
   - If manifest is missing or corrupted, fall back to full collection re-ingest
   - Automatically regenerate manifest on successful full ingest

---

## Technical Considerations

### Advantages
- **Performance**: Process only changed items (potentially 0.1% of collection)
- **Bandwidth**: Reduce unnecessary network operations
- **Scalability**: Efficient for collections with 100K+ items
- **Granularity**: Better tracking and debugging of what changed

### Challenges
- **Manifest Drift**: Manifest can become out of sync with filesystem
- **Atomic Updates**: Need to ensure manifest is updated after successful ingest
- **Concurrent Access**: Handle cases where items are being added while checking
- **Migration**: Need strategy for collections without initial manifest

### Edge Cases to Handle
1. Items added/modified during check cycle
2. Items deleted mid-ingest
3. Filesystem events on network mounts (eventual consistency)
4. Manifest corruption recovery
5. Partial ingest failures

---

## Implementation Plan (When Ready)

### Phase 1: Infrastructure
- [ ] Create manifest file format and versioning strategy
- [ ] Add manifest validation
- [ ] Implement manifest generation for new collections

### Phase 2: Change Detection
- [ ] Implement item-level diff logic
- [ ] Track individual item mtimes
- [ ] Identify added/modified/deleted items

### Phase 3: Selective Ingest
- [ ] Modify `ingest_collection.py` to accept item filter list
- [ ] Implement per-item ingest logic
- [ ] Atomic manifest updates

### Phase 4: Resilience
- [ ] Add manifest recovery mechanisms
- [ ] Implement fallback to full ingest on errors
- [ ] Add validation and integrity checks

---

## Deferral Rationale

- **V1 Scope**: Collection-level detection is sufficient for initial deployment
- **Complexity**: Adds significant state management complexity
- **Priority**: Current bottleneck is collection detection, not item re-ingest
- **Trade-off**: Accept full collection re-ingest for now; optimize item-level later

---

## Related Issues

- Performance degradation with large collections (85K+ items)
- Unnecessary bandwidth usage during updates
- Long ingest cycles for collections with few changes

## References

- [watch_and_ingest.py](../scripts/watch_and_ingest.py) - Current watcher implementation
- [collection_hashes.json](../state/collection_hashes.json) - Collection-level state tracking
