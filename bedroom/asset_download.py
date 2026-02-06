

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pipeline A: source-specific text extraction for Objaverse-XL annotations.

Install:
  pip install -U objaverse pandas

Run:
  python bedroom/asset_download.py --out_dir ./objaverse_bedroom --per_class 20 --processes 16
"""

import argparse
import os
import random
import re
from collections import defaultdict

import pandas as pd
import objaverse.xl as oxl


# --- Minimal keyword sets (user approved) ---
BIG_ASSETS = {
    "bed": ["bed"],
    "wardrobe": ["wardrobe"],
    "desk": ["desk"],
    "chair": ["chair"],
}

SMALL_ASSETS = {
    "shoes": ["shoes"],
    "box": ["box"],
    "suitcase": ["suitcase"],
    "book": ["book"],
    "laptop": ["laptop"],
    "bottle": ["bottle"],
    "pillow": ["pillow"],
    "blanket": ["blanket"],
}

ALL_CLASSES = {**BIG_ASSETS, **SMALL_ASSETS}

# Sources in Objaverse-XL (common). We will prioritize sketchfab + thingiverse.
# github is often noisy for household assets; skip by default.
SKIP_SOURCES_DEFAULT = {"github"}

# Simple negative filters to reduce obvious false positives
NEGATIVE_PATTERNS = {
    "bed": [r"flowerbed", r"riverbed"],   # keep minimal, you can extend later
    "box": [r"mailbox", r"toolbox"],      # optional but helpful
}

def normalize_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, list):
        return " ".join([str(i) for i in x if i is not None]).lower()
    return str(x).lower()


def safe_get(d, *keys):
    """Safely traverse nested dicts: safe_get(meta, 'sketchfab', 'name')"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def extract_search_text(meta: dict) -> str:
    """
    Pipeline A:
      - Prefer source-specific nested fields for sketchfab / thingiverse
      - Fallback to uri / file_identifier / source
    """
    parts = []

    source = normalize_text(meta.get("source"))

    # Sketchfab nested fields (common)
    sf_name = safe_get(meta, "sketchfab", "name")
    sf_tags = safe_get(meta, "sketchfab", "tags")
    sf_categories = safe_get(meta, "sketchfab", "categories")
    if sf_name:
        parts.append(sf_name)
    if sf_tags:
        parts.append(sf_tags)
    if sf_categories:
        parts.append(sf_categories)

    # Thingiverse nested fields (common)
    tv_name = safe_get(meta, "thingiverse", "name")
    tv_tags = safe_get(meta, "thingiverse", "tags")
    if tv_name:
        parts.append(tv_name)
    if tv_tags:
        parts.append(tv_tags)

    # Smithsonian nested fields (often not useful for bedroom, but harmless)
    sm_title = safe_get(meta, "smithsonian", "title")
    if sm_title:
        parts.append(sm_title)

    # Fallback fields
    for f in ["name", "title", "caption", "description", "tags", "category", "categories"]:
        v = meta.get(f)
        if v:
            parts.append(v)

    # Often contains useful tokens like ".../bed_..." or repo paths
    for f in ["uri", "file_identifier", "repo", "path", "url"]:
        v = meta.get(f)
        if v:
            parts.append(v)

    # include source string itself (low value)
    if source:
        parts.append(source)

    text = " ".join([normalize_text(p) for p in parts if p is not None])
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def match_class(search_text: str):
    """Return (class, matched_keyword) if any minimal keyword is found."""
    for cls, kws in ALL_CLASSES.items():
        for kw in kws:
            if kw in search_text:
                # Apply minimal negative filters for this cls
                for neg in NEGATIVE_PATTERNS.get(cls, []):
                    if re.search(neg, search_text):
                        return None, None
                return cls, kw
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_dir", type=str, default="./objaverse_bedroom",
                    help="Output directory for csv and downloaded objects.")
    ap.add_argument("--per_class", type=int, default=20,
                    help="How many objects to sample/download per class.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--processes", type=int, default=16,
                    help="Parallel download processes.")
    ap.add_argument("--max_scan", type=int, default=0,
                    help="If >0, only scan first N annotations (for quick testing).")
    ap.add_argument("--skip_sources", type=str, default="github",
                    help="Comma-separated sources to skip (default: github). Use '' to skip none.")
    args = ap.parse_args()

    random.seed(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)
    download_dir = os.path.join(args.out_dir, "downloads")
    os.makedirs(download_dir, exist_ok=True)

    skip_sources = set([s.strip().lower() for s in args.skip_sources.split(",") if s.strip()]) \
        if args.skip_sources is not None else set(SKIP_SOURCES_DEFAULT)

    print("Loading Objaverse-XL annotations (metadata)...")
    annotations = oxl.get_annotations(download_dir=args.out_dir)
    print(f"Total annotations loaded: {len(annotations):,}")

    items = list(annotations.items())
    if args.max_scan and args.max_scan > 0:
        items = items[:args.max_scan]
        print(f"Scanning only first {len(items):,} items (max_scan).")

    buckets = defaultdict(list)

    print("Filtering by minimal bedroom keywords (Pipeline A: source-specific fields)...")
    for uid, meta in items:
        source = normalize_text(meta.get("source", ""))
        if source in skip_sources:
            continue

        text = extract_search_text(meta)
        if not text:
            continue

        cls, kw = match_class(text)
        if cls is None:
            continue

        license_str = meta.get("license", meta.get("licence", ""))
        source_url = meta.get("url", meta.get("uri", ""))

        # Also store source for debugging
        buckets[cls].append({
            "uid": uid,
            "class": cls,
            "matched_keyword": kw,
            "source": source,
            "license": license_str,
            "source_url": source_url,
            "search_text_snippet": text[:200],  # quick peek
        })

    print("\nCandidate counts:")
    selected = []
    for cls in ALL_CLASSES.keys():
        cands = buckets.get(cls, [])
        print(f"  {cls:10s}: {len(cands):,}")
        if len(cands) == 0:
            continue
        k = min(args.per_class, len(cands))
        chosen = random.sample(cands, k)
        selected.extend(chosen)

    if not selected:
        raise SystemExit("No assets matched. Try setting --skip_sources '' or increase --max_scan=0 (scan all).")

    df = pd.DataFrame(selected).sort_values(["class", "uid"])
    csv_path = os.path.join(args.out_dir, "bedroom_candidates.csv")
    df.to_csv(csv_path, index=False)
    print(f"\nSaved candidate CSV: {csv_path}")

    uids_to_download = df["uid"].tolist()
    print(f"Downloading {len(uids_to_download)} objects to: {download_dir}")

    paths = oxl.download_objects(
        uids=uids_to_download,
        download_dir=download_dir,
        processes=args.processes
    )

    paths_df = pd.DataFrame([{"uid": uid, "local_path": paths.get(uid, "")} for uid in uids_to_download])
    paths_csv = os.path.join(args.out_dir, "bedroom_downloaded_paths.csv")
    paths_df.to_csv(paths_csv, index=False)
    print(f"Saved downloaded paths CSV: {paths_csv}")

    merged = df.merge(paths_df, on="uid", how="left")
    summary_path = os.path.join(args.out_dir, "bedroom_summary.csv")
    merged.to_csv(summary_path, index=False)
    print(f"Saved merged summary CSV: {summary_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()

