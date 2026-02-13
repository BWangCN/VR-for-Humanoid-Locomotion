#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Download living room assets from Objaverse (Sketchfab, ~800K objects).

Uses objaverse.load_annotations() which returns Dict[uid, metadata_dict]
with rich fields (name, tags, categories, description, etc.).

Install:
  pip install -U objaverse pandas

Run:
  python livingroom/asset_download.py --out_dir ./livingroom/objaverse_livingroom --per_class 20 --processes 16
"""

import argparse
import os
import random
import re
from collections import defaultdict

import pandas as pd
import objaverse


# --- Keyword sets for living room furniture ---
BIG_ASSETS = {
    "sofa":      ["sofa", "couch"],
    "tv_stand":  ["tv stand", "tv cabinet", "media console", "entertainment center"],
    "bookshelf": ["bookshelf", "bookcase"],
    "cabinet":   ["cabinet", "sideboard", "credenza"],
    "desk":      ["desk"],
    "piano":     ["piano", "upright piano"],
}

SMALL_ASSETS = {
    "armchair":    ["armchair", "arm chair", "lounge chair", "accent chair"],
    "coffee_table": ["coffee table"],
    "floor_lamp":  ["floor lamp", "standing lamp"],
    "side_table":  ["side table", "end table", "nightstand"],
    "plant_pot":   ["plant pot", "potted plant", "flower pot", "houseplant"],
    "cushion":     ["cushion", "throw pillow"],
    "blanket":     ["blanket", "throw blanket"],
    "book":        ["book"],
    "magazine":    ["magazine"],
    "remote":      ["remote control", "tv remote"],
    "mug":         ["mug", "coffee mug", "cup"],
    "bottle":      ["bottle"],
    "shoes":       ["shoes"],
    "box":         ["box"],
    "toy":         ["toy", "stuffed animal", "plush toy", "action figure"],
}

ALL_CLASSES = {**BIG_ASSETS, **SMALL_ASSETS}

# Simple negative filters to reduce obvious false positives
NEGATIVE_PATTERNS = {
    "box":   [r"mailbox", r"toolbox", r"xbox"],
    "desk":  [r"help desk", r"service desk"],
    "book":  [r"facebook", r"bookshelf", r"bookcase"],
    "mug":   [r"mugshot"],
    "piano": [r"piano bench", r"piano stool"],
}


def normalize_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, list):
        return " ".join([str(i) for i in x if i is not None]).lower()
    return str(x).lower()


def extract_search_text(meta: dict) -> str:
    """Extract searchable text from an Objaverse metadata dict."""
    parts = []

    # Primary fields
    for f in ["name", "title", "caption", "description",
              "tags", "category", "categories"]:
        v = meta.get(f)
        if v:
            parts.append(v)

    # URI / path fields (may contain useful tokens)
    for f in ["uri", "file_identifier", "repo", "path", "url"]:
        v = meta.get(f)
        if v:
            parts.append(v)

    text = " ".join([normalize_text(p) for p in parts if p is not None])
    text = re.sub(r"\s+", " ", text).strip()
    return text


def match_class(search_text: str):
    """Return (class, matched_keyword) if any keyword is found."""
    for cls, kws in ALL_CLASSES.items():
        for kw in kws:
            if kw in search_text:
                for neg in NEGATIVE_PATTERNS.get(cls, []):
                    if re.search(neg, search_text):
                        return None, None
                return cls, kw
    return None, None


def main():
    ap = argparse.ArgumentParser(description="Download living room assets from Objaverse")
    ap.add_argument("--out_dir", type=str, default="./livingroom/objaverse_livingroom",
                    help="Output directory for CSVs.")
    ap.add_argument("--per_class", type=int, default=20,
                    help="How many objects to sample/download per class.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--processes", type=int, default=16,
                    help="Parallel download processes.")
    ap.add_argument("--max_scan", type=int, default=0,
                    help="If >0, only scan first N annotations (for quick testing).")
    args = ap.parse_args()

    random.seed(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)

    # --- Load annotations: Dict[uid, metadata_dict] ---
    print("Loading Objaverse annotations (metadata)...")
    annotations = objaverse.load_annotations()
    print(f"Total annotations loaded: {len(annotations):,}")

    items = list(annotations.items())
    if args.max_scan and args.max_scan > 0:
        items = items[:args.max_scan]
        print(f"Scanning only first {len(items):,} items (max_scan).")

    buckets = defaultdict(list)

    print("Filtering by living room keywords...")
    for uid, meta in items:
        text = extract_search_text(meta)
        if not text:
            continue

        cls, kw = match_class(text)
        if cls is None:
            continue

        license_str = meta.get("license", meta.get("licence", ""))

        buckets[cls].append({
            "uid": uid,
            "class": cls,
            "matched_keyword": kw,
            "license": license_str,
            "search_text_snippet": text[:200],
        })

    print("\nCandidate counts:")
    selected = []
    for cls in ALL_CLASSES.keys():
        cands = buckets.get(cls, [])
        print(f"  {cls:15s}: {len(cands):,}")
        if len(cands) == 0:
            continue
        k = min(args.per_class, len(cands))
        chosen = random.sample(cands, k)
        selected.extend(chosen)

    if not selected:
        raise SystemExit("No assets matched. Increase --max_scan or check keywords.")

    df = pd.DataFrame(selected).sort_values(["class", "uid"])
    csv_path = os.path.join(args.out_dir, "livingroom_candidates.csv")
    df.to_csv(csv_path, index=False)
    print(f"\nSaved candidate CSV: {csv_path}")

    # --- Download: Dict[uid, local_path] ---
    uids_to_download = df["uid"].tolist()
    print(f"Downloading {len(uids_to_download)} objects...")

    paths = objaverse.load_objects(
        uids=uids_to_download,
        download_processes=args.processes,
    )

    paths_df = pd.DataFrame([{"uid": uid, "local_path": paths.get(uid, "")}
                              for uid in uids_to_download])
    paths_csv = os.path.join(args.out_dir, "livingroom_downloaded_paths.csv")
    paths_df.to_csv(paths_csv, index=False)
    print(f"Saved downloaded paths CSV: {paths_csv}")

    merged = df.merge(paths_df, on="uid", how="left")
    summary_path = os.path.join(args.out_dir, "livingroom_summary.csv")
    merged.to_csv(summary_path, index=False)
    print(f"Saved merged summary CSV: {summary_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
