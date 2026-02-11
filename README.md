<<<<<<< HEAD
# SimEnv - Procedural Indoor Scene Generator for Humanoid Locomotion

Procedural pipeline for generating cluttered indoor environments (currently bedrooms) in NVIDIA Isaac Sim and Unity VR, designed for humanoid robot locomotion training and evaluation.

## Overview

SimEnv downloads 3D assets from Objaverse, normalizes them to realistic furniture dimensions, procedurally generates room layouts with difficulty-graded navigation challenges, and instantiates the final scenes in Isaac Sim (high-fidelity) or Unity VR (simplified). Each scene is output as dual JSON files referencing both full-polygon and decimated USD assets.

## Pipeline

```
1. Asset Discovery    2. Rename & Collect    3. GLB -> USD (dual)     4. Scene JSON Gen       5. Isaac Sim Build
   asset_download.py     rename_and_collect.py   batch_glb_to_usd_       scene_json_              generator_isaac.py
   search_asset.py                                and_bbox.py             generator.py
        |                      |                       |                       |                      |
   objaverse_bedroom/     bedroom_assets_raw/    bedroom_assets_usd_     layout_json/            Isaac Sim scene
   (candidates CSV)       (renamed .glb)         _original/ + _simplified/  (*_original.json
                                                 (each with registry)        *_simplified.json)
```

### Stage 1: Asset Discovery & Download

**Scripts:** `bedroom/asset_download.py` (Objaverse-XL) and `bedroom/search_asset.py` (Objaverse v1 LVIS)

Two alternative approaches to find and download 3D assets from Objaverse:

- **`asset_download.py`** - Searches Objaverse-XL annotations using keyword matching across source-specific metadata fields (Sketchfab name/tags, Thingiverse name/tags, etc.). Filters by 12 asset classes split into big furniture (`bed`, `wardrobe`, `desk`, `chair`) and small items (`shoes`, `box`, `suitcase`, `book`, `laptop`, `bottle`, `pillow`, `blanket`). Applies negative pattern filtering (e.g., excludes "flowerbed" from bed matches). Samples up to `--per_class` objects per class and downloads them.
- **`search_asset.py`** - Simpler approach using Objaverse v1 LVIS category annotations for keyword-based asset lookup and download.

Both produce a `bedroom_candidates.csv` (uid + class mapping) and `bedroom_downloaded_paths.csv` (uid + local file path).

```bash
# Objaverse-XL (larger candidate pool, slower)
python bedroom/asset_download.py --out_dir ./objaverse_bedroom --per_class 50 --processes 16

# Objaverse v1 LVIS (pre-labeled categories, faster)
python bedroom/search_asset.py
```

### Stage 2: Rename & Collect

**Script:** `bedroom/rename_and_collect.py`

Reads the candidate and path CSVs, then copies each downloaded `.glb` file into a structured directory under `bedroom_assets_raw/`, renaming them with a class-prefixed zero-padded index (e.g., `bed00.glb`, `chair05.glb`).

```
bedroom_assets_raw/
  bed/       bed00.glb .. bed19.glb
  chair/     chair00.glb .. chair19.glb
  desk/      ...
  ...
```

```bash
python bedroom/rename_and_collect.py
```

### Stage 3: GLB to USD Conversion + Size Normalization

**Script:** `bedroom/batch_glb_to_usd_and_bbox.py` (runs inside Blender)

Produces **two output directories** from a single run: full-polygon USD for Isaac Sim high-fidelity rendering and decimated USD for VR headset real-time rendering.

For each `.glb` asset the script:

1. **Imports** the GLB into Blender and computes the raw world-space AABB (axis-aligned bounding box).
2. **Infers unit scale** automatically: Objaverse assets have inconsistent units (some in meters, some in centimeters or millimeters). A heuristic based on the raw AABB maximum dimension decides the conversion factor:
   - 0.02m - 50m: assume already in meters (scale = 1.0)
   - 50m - 5000m: likely centimeters (scale = 0.01)
   - \>5000m: likely millimeters (scale = 0.001)
3. **Normalizes to realistic dimensions** by sampling a random target bounding box from class-specific ranges and computing the best uniform scale factor (log-space least-squares fit over 32 trials). Target size ranges (meters):

   | Class | X (width) | Y (depth) | Z (height) |
   |-------|-----------|-----------|------------|
   | bed | 2.0 - 2.8 | 2.0 - 2.8 | 0.6 - 1.2 |
   | chair | 0.4 - 0.8 | 0.4 - 0.8 | 0.7 - 1.2 |
   | desk | 0.8 - 1.6 | 0.4 - 0.9 | 0.65 - 0.9 |
   | wardrobe | 0.6 - 1.5 | 0.4 - 0.7 | 1.5 - 2.4 |
   | box | 0.2 - 0.6 | 0.2 - 0.6 | 0.2 - 0.6 |
   | book | 0.15 - 0.30 | 0.20 - 0.35 | 0.02 - 0.08 |
   | bottle | 0.05 - 0.12 | 0.05 - 0.12 | 0.15 - 0.35 |
   | pillow | 0.40 - 0.80 | 0.30 - 0.60 | 0.08 - 0.25 |
   | blanket | 1.2 - 2.4 | 1.2 - 2.4 | 0.01 - 0.08 |
   | shoes | 0.20 - 0.35 | 0.08 - 0.15 | 0.08 - 0.15 |
   | suitcase | 0.45 - 0.80 | 0.25 - 0.45 | 0.15 - 0.35 |
   | laptop | 0.25 - 0.40 | 0.18 - 0.30 | 0.01 - 0.04 |

4. **Height-tier distribution** for step-over candidate classes (blanket, shoes, pillow, book, laptop). Assets within each class are assigned to height tiers based on their index, ensuring a balanced spread of heights for all difficulty levels:

   | Class | Tier distribution |
   |-------|------------------|
   | blanket | 50% very flat (1-4cm), 30% thin (3-6cm), 20% thicker (5-8cm) |
   | shoes | 35% low (6-10cm), 40% medium (8-13cm), 25% tall (11-15cm) |
   | book | 45% thin (2-5cm), 35% medium (4-7cm), 20% thick (6-8cm) |
   | pillow | 30% flat (6-10cm), 40% medium (8-18cm), 30% thick (15-25cm) |
   | laptop | 55% closed (1-2.5cm), 45% slightly open (2-4cm) |

5. **Exports original USD** (full polygon count) to `bedroom_assets_usd_original/`.
6. **Decimates meshes** if polygon count exceeds class-specific limits (e.g., bed: 30k, book: 3k).
7. **Exports simplified USD** (decimated) to `bedroom_assets_usd_simplified/`.
8. **Writes `asset_registry.csv`** in each output directory, recording class, paths, scales applied, final AABB dimensions, face counts, etc.

```bash
blender -b -P bedroom/batch_glb_to_usd_and_bbox.py --% -- --input ./bedroom/bedroom_assets_raw --output ./bedroom/bedroom_assets_usd
```

Output:
```
bedroom/
  bedroom_assets_usd_original/       # Full polygon USD (Isaac Sim)
    asset_registry.csv
    bed/bed00/bed00.usd
    chair/chair00/chair00.usd
    ...
  bedroom_assets_usd_simplified/     # Decimated USD (VR headset)
    asset_registry.csv
    bed/bed00/bed00.usd
    ...
```

### Stage 4: Procedural Scene Layout Generation

**Script:** `bedroom/scene_json_generator.py`

Generates JSON scene descriptions with rule-based procedural placement. When both original and simplified registries are available, outputs **dual JSON files** per scene with identical layouts but different USD paths.

**Dual Output:**
- `bedroom_d1_003_original.json` — references `bedroom_assets_usd_original/` (for Isaac Sim)
- `bedroom_d1_003_simplified.json` — references `bedroom_assets_usd_simplified/` (for VR headset)

**Room Generation:**
- Three room size tiers with weighted random selection:
  - Small (35%): 3.0-3.8m x 3.6-4.6m
  - Medium (45%): 3.8-4.8m x 4.2-5.8m
  - Large (20%): 4.8-6.8m x 5.5-7.5m
- Room height fixed at 2.6m

**Difficulty System (0-3):**
The difficulty is defined by locomotion challenge, controlling how the humanoid must navigate from origin to destination:

| Difficulty | Passage Type | Step-over on Corridor | Corridor Height Budget |
|------------|-------------|----------------------|----------------------|
| 0 | Front pass (>=1.0m wide) | No | 0 cm (clear) |
| 1 | Front pass (>=1.0m wide) | Yes | max 10cm |
| 2 | Side pass only (0.5-1.0m) | No | 0 cm (clear) |
| 3 | Side pass only (0.5-1.0m) | Yes | max 20cm, prefer >=5cm |

- **Front pass**: corridor wide enough (>=1.0m) for normal walking
- **Side pass**: corridor narrow (0.5-1.0m), requiring sideways locomotion
- **Step-over**: low objects placed in the corridor; height limits enforced per difficulty via `CORRIDOR_HEIGHT_BUDGET`

**Placement Rules:**
1. **Origin & destination zones** (1.0m x 1.0m each) placed at diagonally opposite corners
2. **L-shaped corridor** reserved between zones with width determined by difficulty
3. **Bed** always placed against a wall, avoiding the corridor
4. **Wall-hugging big items** (wardrobe, suitcase, desk, cabinet) placed against walls with tier-dependent probability
5. **Chairs** placed with 2-stage fallback: first try avoiding corridor, then allow corridor overlap
6. **Forced step-over items** placed in corridor for difficulty 1 and 3, pre-filtered by height budget
7. **Cluster clutter** (shoes, blankets, books, bottles, etc.) placed near the bed
8. **Scattered small items** distributed randomly across the room
9. **Cone markers** at origin (green) and destination (red) — visual-only, no physics collision

**Shoe Pairs:**
Shoes are always placed as pairs. When a shoe is placed, a matching second shoe is automatically positioned alongside it with a small gap (4cm) and slight yaw jitter (up to 8 degrees).

**Reachability Verification:**
- BFS grid-based pathfinding (10cm resolution) verifies the humanoid can navigate origin to destination
- Tests both front-pass (radius 0.5m) and side-pass (radius 0.25m) and enforces the correct mode per difficulty
- Only non-step-over obstacles block the path

**Annealing:**
- If placement fails repeatedly, the generator reduces item counts (every 15 failures = one anneal level)
- Reduction order: scatter > cluster > wall-big > forced step-over > chairs (last)
- Difficulty 0 allows 0 chairs; difficulty 1-3 keeps minimum 1 chair
- Maximum 2000 attempts, guaranteeing convergence via progressive reduction

**Collision Avoidance:**
- All objects use 2D AABB rectangles with margins (wall margin 0.06m, object margin 0.05m)
- Rotated AABBs are recomputed per yaw angle
- Objects must not overlap each other or reserved zones

```bash
# Single scene (dual JSON when --registry_simplified is provided)
python bedroom/scene_json_generator.py \
  --registry bedroom/bedroom_assets_usd_original/asset_registry.csv \
  --registry_simplified bedroom/bedroom_assets_usd_simplified/asset_registry.csv \
  --output bedroom/layout_json/bedroom01.json --difficulty 2 --tier medium

# Batch: 50 scenes per difficulty (200 total, 400 JSONs in dual mode)
python bedroom/scene_json_generator.py \
  --registry bedroom/bedroom_assets_usd_original/asset_registry.csv \
  --registry_simplified bedroom/bedroom_assets_usd_simplified/asset_registry.csv \
  --output bedroom/layout_json --count 50
```

**Convenience wrapper** (`generate_scenes.py`):

```bash
# 50 scenes per difficulty (200 scenes, 400 JSONs), auto-detects both registries
python generate_scenes.py 50

# 10 scenes for difficulty 2 only
python generate_scenes.py 10 -d 2
```

### Stage 5: Isaac Sim Scene Instantiation

**Script:** `generator_isaac.py` (runs inside Isaac Sim's Script Editor)

Reads a generated JSON (`*_original.json` for high-fidelity) and builds the scene in Isaac Sim:

1. Creates a physics scene with gravity (-Z, 9.81 m/s^2)
2. Builds the room shell: floor (static collider at z=0) and 4 walls (static collider boxes)
3. For each regular object: adds a USD reference at the specified prim path, applies translation + XYZ rotation, and optionally applies rigid body + collision physics
4. For marker objects (`class: "marker"`): creates a `UsdGeom.Cone` primitive with `displayColor` (green for origin, red for destination) instead of loading a USD reference. Markers have no collision and are purely visual
5. All USD paths are normalized to forward slashes for cross-platform compatibility

```python
# In Isaac Sim Script Editor:
JSON_PATH = r"path/to/bedroom_d1_003_original.json"
build_scene_from_json(JSON_PATH)
```

## Directory Structure

```
SimEnv/
  README.md
  generate_scenes.py                # Convenience wrapper for batch scene generation
  generator_isaac.py                # Stage 5: Isaac Sim scene builder
  bedroom/
    asset_download.py               # Stage 1a: Objaverse-XL asset search & download
    search_asset.py                 # Stage 1b: Objaverse v1 LVIS asset search & download
    rename_and_collect.py           # Stage 2: Rename and organize downloaded GLBs
    batch_glb_to_usd_and_bbox.py    # Stage 3: Blender GLB->USD + size normalization (dual output)
    scene_json_generator.py         # Stage 4: Procedural scene layout generation (dual JSON)
    scene_test.json                 # Example manually-created scene JSON
    bedroom_assets_raw/             # Renamed .glb files organized by class
    bedroom_assets_usd_original/    # Full-polygon .usd files + asset_registry.csv (Isaac Sim)
    bedroom_assets_usd_simplified/  # Decimated .usd files + asset_registry.csv (VR headset)
    layout_json/                    # Generated scene layout JSONs (*_original + *_simplified)
    objaverse_bedroom/              # Objaverse-XL download cache
    objaverse_bedroom_v1/           # Objaverse v1 download cache
  livingroom/                       # (Placeholder for future room types)
```

## Dependencies

- **Stage 1**: `objaverse`, `pandas`
- **Stage 2**: `pandas`
- **Stage 3**: Blender 4.x (run as `blender -b -P ...`)
- **Stage 4**: Python standard library only
- **Stage 5**: NVIDIA Isaac Sim (Omniverse USD API, `pxr`, `PhysxSchema`)

## Notes

**Unity VR texture issue:** When importing USD into Unity, textures may appear white because Unity's USD Importer does not always resolve `UsdPreviewSurface` relative texture paths correctly. Workarounds include:
- Packaging USD + textures as `.usdz` via `usdzip`
- Converting USD back to GLB via Blender for better Unity material support
- Re-linking textures in Unity with a post-import script
- The cone markers use `displayColor` (vertex color), which does not depend on textures and renders correctly in both Isaac Sim and Unity
=======
# Pipeline
## Download Raw Assets from Objaverse
Modify `asset_download.py`, especially change the `BIG_ASSETS` and `SMALL_ASSERS` list to your own. Also modify the `csv_path` and `paths_csv` to your local address.

This code will iterate through the large objaverse asset library and select candidate objects' uid for download. The `csv_path` stores the asset candidates' uid, and `paths_csv` stores the local download address for each candidate. After this step, you should be able to see a folder named `objaverse_bedroom`.

Run `rename_and_collect.py`, it will iterate through the uids and download the assets, and also renaming them according to their class (e.g., the asset after renaming will be bed01, bed02, etc.).

## Convert Raw Assets to USD
The raw assets from objaverse are glb files. However, Isaac Sim only supports USD format. Make sure you have Blender (4.5 recommended) downloaded on your system.

Run `blender -b -P batch_glb_to_usd_and_bbox.py -- --input ".\assets_raw" --output ".\assets_usd"`. Notice that this 'blender' command might need to be changed to `YOUR_LOCAL_PATH/blender.exe` if you are using Windows system.

This script will convert glb assets to usd format. Note that assets from Objaverse have extremely large size variations due to inconsistent unit scales. Therefore, in this code, we add a normalization step that rescales assets into a reasonable size range, using meters as the unified unit in USD. If you download or use your own assets, please make sure to also set an appropriate size range for them.

## Scene Json Generator
Before the scene is generated in isaac sim, it should be first generated as a simple json file containing the location information for each object.

`scene_json_generator.py` will choose randomly from the assets, having access to the size of their bounding boxes, and determine where to place them according to hand crafted rules. Feel free to modify the rules to create environments of your style, or call LLM API to automate this process.

## Scene Builder
Check `generator_isaac.py`. Make sure `JSON_PATH` points to the json path created. Open isaac sim, Window --> Script Editor.

Directly copy and paste generator_isaac.py into it and Run. Create --> Light --> Dome Light for better view.
>>>>>>> 61a0a218b964964605c8a2c3fc1b1470971e096c
