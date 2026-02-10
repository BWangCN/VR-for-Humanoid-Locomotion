"""
Batch scene generation wrapper.

Usage:
    python generate_scenes.py 50                  # 50 combo scenes per difficulty (200 total), dual JSON
    python generate_scenes.py 50 -a 20            # 50 combo + 20 atom per difficulty (d2/d3 atom only)
    python generate_scenes.py 50 --difficulty 2   # 50 scenes for difficulty 2 only
    python generate_scenes.py 10 -d 0             # 10 scenes for difficulty 0 only

Output per scene (dual mode):
    bedroom_d1/bedroom_d1_003_original.json         (combo, high-fidelity)
    bedroom_d1/bedroom_d1_003_simplified.json       (combo, decimated)
    bedroom_d2_atom/bedroom_d2_003_atom_original.json   (atom, high-fidelity)
    bedroom_d2_atom/bedroom_d2_003_atom_simplified.json (atom, decimated)
"""
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
GENERATOR   = SCRIPT_DIR / "bedroom" / "scene_json_generator.py"
REGISTRY_ORIG = SCRIPT_DIR / "bedroom" / "bedroom_assets_usd_original"  / "asset_registry.csv"
REGISTRY_SIMP = SCRIPT_DIR / "bedroom" / "bedroom_assets_usd_simplified" / "asset_registry.csv"
OUTPUT_DIR  = SCRIPT_DIR / "bedroom" / "layout_json"


def main():
    p = argparse.ArgumentParser(description="Batch bedroom scene generator")
    p.add_argument("count", type=int, help="Number of scenes per difficulty")
    p.add_argument("-d", "--difficulty", type=int, default=None,
                   choices=[0, 1, 2, 3],
                   help="Difficulty level (0-3). Omit to generate all four.")
    p.add_argument("-a", "--atom_count", type=int, default=0,
                   help="Number of atom-action scenes per difficulty (d2/d3 only). "
                        "Atom scenes contain only the single difficulty action + basic walking.")
    args = p.parse_args()

    if not REGISTRY_ORIG.exists():
        print(f"ERROR: original registry not found at {REGISTRY_ORIG}")
        sys.exit(1)

    cmd = [
        sys.executable, str(GENERATOR),
        "--registry",  str(REGISTRY_ORIG),
        "--output",    str(OUTPUT_DIR),
        "--count",     str(args.count),
    ]

    # Enable dual output when simplified registry exists
    if REGISTRY_SIMP.exists():
        cmd += ["--registry_simplified", str(REGISTRY_SIMP)]
        mode = "dual (original + simplified)"
    else:
        print(f"WARNING: simplified registry not found at {REGISTRY_SIMP}")
        print("         Generating single JSON per scene (original only).\n")
        mode = "single (original only)"

    if args.difficulty is not None:
        cmd += ["--difficulty", str(args.difficulty)]

    if args.atom_count > 0:
        cmd += ["--atom_count", str(args.atom_count)]

    difficulties = [args.difficulty] if args.difficulty is not None else [0, 1, 2, 3]
    atom_diffs = [d for d in difficulties if d in (2, 3)]
    total_combo = args.count * len(difficulties)
    total_atom = args.atom_count * len(atom_diffs)
    total = total_combo + total_atom
    print(f"Generating {args.count} combo x {len(difficulties)} diff "
          f"+ {args.atom_count} atom x {len(atom_diffs)} diff = {total} scenes  [{mode}]")
    print(f"  registry original  : {REGISTRY_ORIG}")
    print(f"  registry simplified: {REGISTRY_SIMP}")
    print(f"  output             : {OUTPUT_DIR}")
    print()

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
