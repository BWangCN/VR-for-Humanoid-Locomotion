"""
Batch scene generation wrapper.

Usage:
    python generate_scenes.py 50                       # 50 bedroom scenes per difficulty (200 total)
    python generate_scenes.py 50 -a 20                 # 50 combo + 20 atom per difficulty (d2/d3 atom only)
    python generate_scenes.py 50 --difficulty 2        # 50 scenes for difficulty 2 only
    python generate_scenes.py 10 -d 0                  # 10 scenes for difficulty 0 only
    python generate_scenes.py 50 --room_type livingroom  # 50 living room scenes per difficulty

Output per scene (dual mode):
    {room}_d1/{room}_d1_003_original.json         (combo, high-fidelity)
    {room}_d1/{room}_d1_003_simplified.json       (combo, decimated)
    {room}_d2_atom/{room}_d2_003_atom_original.json   (atom, high-fidelity)
    {room}_d2_atom/{room}_d2_003_atom_simplified.json (atom, decimated)
"""
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GENERATOR  = SCRIPT_DIR / "bedroom" / "scene_json_generator.py"

# Per-room-type path templates
ROOM_PATHS = {
    "bedroom": {
        "registry_orig": SCRIPT_DIR / "bedroom" / "bedroom_assets_usd_original"  / "asset_registry.csv",
        "registry_simp": SCRIPT_DIR / "bedroom" / "bedroom_assets_usd_simplified" / "asset_registry.csv",
        "output_dir":    SCRIPT_DIR / "bedroom" / "layout_json",
        "textures":      SCRIPT_DIR / "bedroom" / "textures" / "texture_catalog.json",
    },
    "livingroom": {
        "registry_orig": SCRIPT_DIR / "livingroom" / "livingroom_assets_usd_original"  / "asset_registry.csv",
        "registry_simp": SCRIPT_DIR / "livingroom" / "livingroom_assets_usd_simplified" / "asset_registry.csv",
        "output_dir":    SCRIPT_DIR / "livingroom" / "layout_json",
        "textures":      SCRIPT_DIR / "livingroom" / "textures" / "texture_catalog.json",
    },
}


def main():
    p = argparse.ArgumentParser(description="Batch scene generator (multi-room-type)")
    p.add_argument("count", type=int, help="Number of scenes per difficulty")
    p.add_argument("-d", "--difficulty", type=int, default=None,
                   choices=[0, 1, 2, 3],
                   help="Difficulty level (0-3). Omit to generate all four.")
    p.add_argument("-a", "--atom_count", type=int, default=0,
                   help="Number of atom-action scenes per difficulty (d2/d3 only). "
                        "Atom scenes contain only the single difficulty action + basic walking.")
    p.add_argument("-r", "--room_type", type=str, default="bedroom",
                   choices=list(ROOM_PATHS.keys()),
                   help="Room type to generate (default: bedroom).")
    args = p.parse_args()

    room_type = args.room_type
    paths = ROOM_PATHS[room_type]
    registry_orig = paths["registry_orig"]
    registry_simp = paths["registry_simp"]
    output_dir = paths["output_dir"]
    textures_catalog = paths["textures"]

    if not registry_orig.exists():
        print(f"ERROR: original registry not found at {registry_orig}")
        sys.exit(1)

    cmd = [
        sys.executable, str(GENERATOR),
        "--registry",   str(registry_orig),
        "--output",     str(output_dir),
        "--count",      str(args.count),
        "--room_type",  room_type,
    ]

    # Enable dual output when simplified registry exists
    if registry_simp.exists():
        cmd += ["--registry_simplified", str(registry_simp)]
        mode = "dual (original + simplified)"
    else:
        print(f"WARNING: simplified registry not found at {registry_simp}")
        print("         Generating single JSON per scene (original only).\n")
        mode = "single (original only)"

    # Pass texture catalog when available
    if textures_catalog.exists():
        cmd += ["--textures", str(textures_catalog)]
        print(f"Textures enabled: {textures_catalog}")
    else:
        print(f"NOTE: texture catalog not found at {textures_catalog}")
        print(f"      Run texture download script to enable PBR textures.\n")

    if args.difficulty is not None:
        cmd += ["--difficulty", str(args.difficulty)]

    if args.atom_count > 0:
        cmd += ["--atom_count", str(args.atom_count)]

    difficulties = [args.difficulty] if args.difficulty is not None else [0, 1, 2, 3]
    atom_diffs = [d for d in difficulties if d in (2, 3)]
    total_combo = args.count * len(difficulties)
    total_atom = args.atom_count * len(atom_diffs)
    total = total_combo + total_atom
    print(f"Room type: {room_type}")
    print(f"Generating {args.count} combo x {len(difficulties)} diff "
          f"+ {args.atom_count} atom x {len(atom_diffs)} diff = {total} scenes  [{mode}]")
    print(f"  registry original  : {registry_orig}")
    print(f"  registry simplified: {registry_simp}")
    print(f"  output             : {output_dir}")
    print()

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
