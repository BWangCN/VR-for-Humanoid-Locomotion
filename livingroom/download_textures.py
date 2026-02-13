"""
Download PBR textures from ambientCG (CC0 license) for living room floor and walls.

Downloads 1K-JPG texture packs, extracts Color/Normal/Roughness maps,
and generates a texture_catalog.json used by scene_json_generator.py.

Usage:
    python livingroom/download_textures.py
    python livingroom/download_textures.py --output livingroom/textures
"""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve
from urllib.error import URLError

# ---- Texture definitions (living room appropriate) ----
TEXTURES = {
    "floor": [
        {"id": "WoodFloor006",  "category": "wood",    "tiles_per_meter": 1.0},
        {"id": "WoodFloor040",  "category": "wood",    "tiles_per_meter": 1.0},
        {"id": "WoodFloor051",  "category": "wood",    "tiles_per_meter": 1.0},
        {"id": "WoodFloor024",  "category": "wood",    "tiles_per_meter": 1.0},
        {"id": "Tiles074",      "category": "tile",    "tiles_per_meter": 2.0},
        {"id": "Tiles101",      "category": "tile",    "tiles_per_meter": 2.0},
        {"id": "Carpet004",     "category": "carpet",  "tiles_per_meter": 0.5},
        {"id": "Carpet008",     "category": "carpet",  "tiles_per_meter": 0.5},
    ],
    "wall": [
        {"id": "Plaster001",         "category": "plaster",   "tiles_per_meter": 0.5},
        {"id": "Plaster003",         "category": "plaster",   "tiles_per_meter": 0.5},
        {"id": "Plaster017",         "category": "plaster",   "tiles_per_meter": 0.5},
        {"id": "PaintedPlaster017",  "category": "paint",     "tiles_per_meter": 0.5},
        {"id": "Wallpaper001",       "category": "wallpaper", "tiles_per_meter": 1.0},
        {"id": "Wallpaper003",       "category": "wallpaper", "tiles_per_meter": 1.0},
        {"id": "PaintedWall001",     "category": "paint",     "tiles_per_meter": 0.5},
        {"id": "Concrete034",        "category": "concrete",  "tiles_per_meter": 0.5},
    ],
}

DOWNLOAD_URL = "https://ambientcg.com/get?file={id}_1K-JPG.zip"

REQUIRED_SUFFIXES = ["_Color.jpg", "_NormalGL.jpg", "_Roughness.jpg"]


def download_and_extract(tex_id: str, surface: str, output_root: Path) -> dict:
    dest_dir = output_root / surface / tex_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    expected_files = [f"{tex_id}_1K-JPG{suffix}" for suffix in REQUIRED_SUFFIXES]
    if all((dest_dir / f).exists() for f in expected_files):
        print(f"  [skip] {tex_id} already exists")
        return _make_catalog_entry(tex_id, surface, expected_files)

    url = DOWNLOAD_URL.format(id=tex_id)
    print(f"  Downloading {tex_id} from {url} ...")

    try:
        tmp_path = dest_dir / f"{tex_id}_1K-JPG.zip"
        urlretrieve(url, str(tmp_path))
    except URLError as e:
        print(f"  [ERROR] Failed to download {tex_id}: {e}")
        raise

    with zipfile.ZipFile(str(tmp_path), "r") as zf:
        for member in zf.namelist():
            basename = os.path.basename(member)
            if any(basename.endswith(suffix) for suffix in REQUIRED_SUFFIXES):
                data = zf.read(member)
                (dest_dir / basename).write_bytes(data)

    tmp_path.unlink()

    missing = [f for f in expected_files if not (dest_dir / f).exists()]
    if missing:
        print(f"  [WARN] {tex_id}: missing files after extraction: {missing}")

    return _make_catalog_entry(tex_id, surface, expected_files)


def _make_catalog_entry(tex_id: str, surface: str, filenames: list) -> dict:
    entry = {"id": tex_id}
    for fname in filenames:
        rel = f"{surface}/{tex_id}/{fname}"
        if "_Color.jpg" in fname:
            entry["color"] = rel
        elif "_NormalGL.jpg" in fname:
            entry["normal"] = rel
        elif "_Roughness.jpg" in fname:
            entry["roughness"] = rel
    return entry


def main():
    parser = argparse.ArgumentParser(description="Download PBR textures from ambientCG (living room)")
    parser.add_argument("--output", type=str,
                        default=str(Path(__file__).resolve().parent / "textures"),
                        help="Output directory for textures (default: livingroom/textures)")
    args = parser.parse_args()

    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    catalog = {"floor": [], "wall": []}

    for surface in ("floor", "wall"):
        print(f"\n=== {surface.upper()} textures ===")
        for tex_def in TEXTURES[surface]:
            tex_id = tex_def["id"]
            try:
                entry = download_and_extract(tex_id, surface, output_root)
                entry["tiles_per_meter"] = tex_def["tiles_per_meter"]
                catalog[surface].append(entry)
            except Exception as e:
                print(f"  [FAIL] {tex_id}: {e}")

    catalog_path = output_root / "texture_catalog.json"
    with open(str(catalog_path), "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    n_floor = len(catalog["floor"])
    n_wall = len(catalog["wall"])
    print(f"\nDone. Catalog written to {catalog_path}")
    print(f"  floor textures: {n_floor}/{len(TEXTURES['floor'])}")
    print(f"  wall  textures: {n_wall}/{len(TEXTURES['wall'])}")

    if n_floor == 0 or n_wall == 0:
        print("WARNING: Some surfaces have zero textures. Check download errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
