# batch_glb_to_usd_and_bbox.py  (kitchen)
# Run:
#   blender -b -P kitchen/batch_glb_to_usd_and_bbox.py --% -- --input ".\kitchen\kitchen_assets_raw" --output ".\kitchen\kitchen_assets_usd"
#
# Produces two output directories:
#   {output}_original/    — full polygon count (for Isaac Sim high-fidelity rendering)
#   {output}_simplified/  — decimated meshes  (for PICO VR / real-time)
# Each directory contains its own asset_registry.csv.

import os
import sys
import csv
import argparse
import random
import hashlib
import time
from pathlib import Path

import bpy
from mathutils import Vector

GLOBAL_SEED = 20260218

# Max face counts per class (soft objects get lower limits)
CLASS_MAX_FACES = {
    "dining_table": 30000,
    "fridge":       30000,
    "cabinet":      30000,
    "oven":         25000,
    "dishwasher":   20000,
    "chair":        20000,
    "stool":        15000,
    "trash_can":    10000,
    "cart":         15000,
    "pot":           5000,
    "pan":           5000,
    "bottle":        5000,
    "mug":           5000,
    "plate":         3000,
    "bowl":          5000,
    "cutting_board": 3000,
    "towel":        10000,
    "shoes":         5000,
    "box":           5000,
    "bag":          10000,
}

# Realistic size ranges (meters) for each kitchen class
CLASS_RANGES = {
    "dining_table":  {"x": (1.0, 1.8), "y": (0.7, 1.2),  "z": (0.7, 0.85)},
    "fridge":        {"x": (0.55, 0.85), "y": (0.55, 0.75), "z": (1.5, 2.0)},
    "cabinet":       {"x": (0.6, 1.5), "y": (0.35, 0.60), "z": (0.7, 2.2)},
    "oven":          {"x": (0.55, 0.75), "y": (0.55, 0.70), "z": (0.85, 1.0)},
    "dishwasher":    {"x": (0.55, 0.65), "y": (0.55, 0.65), "z": (0.80, 0.90)},
    "chair":         {"x": (0.4, 0.6), "y": (0.4, 0.6),  "z": (0.7, 1.0)},
    "stool":         {"x": (0.3, 0.5), "y": (0.3, 0.5),  "z": (0.5, 0.8)},
    "trash_can":     {"x": (0.25, 0.40), "y": (0.25, 0.40), "z": (0.5, 0.8)},
    "cart":          {"x": (0.4, 0.7), "y": (0.3, 0.5),  "z": (0.7, 1.0)},
    "pot":           {"x": (0.15, 0.35), "y": (0.15, 0.35), "z": (0.10, 0.25)},
    "pan":           {"x": (0.20, 0.35), "y": (0.35, 0.60), "z": (0.04, 0.10)},
    "bottle":        {"x": (0.05, 0.12), "y": (0.05, 0.12), "z": (0.15, 0.35)},
    "mug":           {"x": (0.07, 0.10), "y": (0.07, 0.10), "z": (0.08, 0.12)},
    "plate":         {"x": (0.20, 0.30), "y": (0.20, 0.30), "z": (0.015, 0.035)},
    "bowl":          {"x": (0.12, 0.25), "y": (0.12, 0.25), "z": (0.06, 0.15)},
    "cutting_board": {"x": (0.20, 0.40), "y": (0.15, 0.30), "z": (0.01, 0.03)},
    "towel":         {"x": (0.40, 0.70), "y": (0.30, 0.50), "z": (0.01, 0.04)},
    "shoes":         {"x": (0.20, 0.35), "y": (0.08, 0.15), "z": (0.08, 0.15)},
    "box":           {"x": (0.2, 0.6),   "y": (0.2, 0.6),   "z": (0.2, 0.6)},
    "bag":           {"x": (0.20, 0.45), "y": (0.10, 0.30), "z": (0.25, 0.45)},
}

# Height distribution tiers for step-over candidate classes.
# Ensures a balanced spread of heights for each difficulty level.
CLASS_HEIGHT_TIERS = {
    "towel": [
        (0.50, (0.01, 0.02)),   # flat towel   — diff 1 suitable
        (0.30, (0.015, 0.03)),  # folded       — diff 1 suitable
        (0.20, (0.025, 0.04)),  # bunched      — diff 3 suitable
    ],
    "shoes": [
        (0.35, (0.06, 0.10)),   # low shoes  — diff 1 suitable
        (0.40, (0.08, 0.13)),   # medium     — diff 3 suitable
        (0.25, (0.11, 0.15)),   # taller     — diff 3 suitable
    ],
    "bottle": [
        (0.40, (0.15, 0.22)),   # short bottle  — tipover obstacle
        (0.35, (0.20, 0.28)),   # medium bottle
        (0.25, (0.25, 0.35)),   # tall bottle
    ],
    "pot": [
        (0.45, (0.08, 0.14)),   # small pot  — diff 1 suitable
        (0.35, (0.12, 0.18)),   # medium pot — diff 3 suitable
        (0.20, (0.16, 0.25)),   # large pot  — borderline step-over
    ],
    "pan": [
        (0.55, (0.04, 0.06)),   # flat pan   — diff 1 suitable
        (0.45, (0.05, 0.10)),   # deeper pan — diff 3 suitable
    ],
    "bag": [
        (0.40, (0.15, 0.25)),   # small bag
        (0.35, (0.20, 0.35)),   # medium bag
        (0.25, (0.30, 0.45)),   # large bag — non-step-over obstacle
    ],
    "box": [
        (0.40, (0.10, 0.20)),   # small box  — diff 1/3 suitable
        (0.35, (0.18, 0.35)),   # medium box
        (0.25, (0.30, 0.60)),   # large box  — non-step-over obstacle
    ],
}


def get_height_tier_z_range(cls: str, idx_in_class: int, total_in_class: int):
    """Return a z-range override for this asset, or None if class has no tiers."""
    if cls not in CLASS_HEIGHT_TIERS:
        return None
    tiers = CLASS_HEIGHT_TIERS[cls]
    frac = idx_in_class / max(1, total_in_class)
    cum = 0.0
    for tier_frac, z_range in tiers:
        cum += tier_frac
        if frac < cum:
            return z_range
    return tiers[-1][1]


# ---------------- CLI ----------------

def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="Root folder like kitchen_assets_raw/")
    p.add_argument("--output", required=True, help="Root folder like kitchen_assets_usd/")
    p.add_argument("--unit_scale", type=float, default=None,
                   help="Unit conversion scale (e.g., 0.01 for cm->m). If omitted, auto-infer.")
    p.add_argument("--auto_unit_scale", action="store_true", default=True,
                   help="Auto-infer unit scale from raw AABB magnitude (default True).")
    p.add_argument("--no_auto_unit_scale", action="store_true", default=False,
                   help="Disable auto unit inference; use --unit_scale as-is.")
    p.add_argument("--csv", default="asset_registry.csv", help="CSV filename written under output root")
    p.add_argument("--seed", type=int, default=GLOBAL_SEED, help="Global RNG seed for reproducibility")
    p.add_argument("--export_textures", action="store_true", default=True,
                   help="Export textures with USD (default True). Use --no_export_textures to disable.")
    p.add_argument("--no_export_textures", action="store_true", default=False,
                   help="Disable texture export (avoids Windows file-lock issues).")
    p.add_argument("--export_retries", type=int, default=3, help="USD export retries on failure (Windows locks).")
    p.add_argument("--max_faces", type=int, default=50000,
                   help="Default max face count per asset. CLASS_MAX_FACES overrides per class. (default: 50000)")
    p.add_argument("--no_decimate", action="store_true", default=False,
                   help="Disable automatic mesh decimation entirely.")
    return p.parse_args(argv)

# ---------------- Utils ----------------

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def stable_int_hash(s: str) -> int:
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)

def import_glb_capture(path: str):
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    after = set(bpy.context.scene.objects)
    imported = list(after - before)
    meshes = [o for o in imported if o.type == "MESH"]
    return imported, meshes

def get_roots(imported_objs):
    roots = [o for o in imported_objs if o.parent is None]
    return roots

def select_objects(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0] if objs else None

def apply_object_scale_only(objs):
    if not objs:
        return
    select_objects(objs)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

def scale_roots(imported_objs, s: float):
    roots = get_roots(imported_objs)
    for r in roots:
        r.scale = (r.scale.x * s, r.scale.y * s, r.scale.z * s)
    apply_object_scale_only(roots)

def world_aabb_for_meshes(mesh_objs):
    if not mesh_objs:
        return None

    min_v = Vector((1e18, 1e18, 1e18))
    max_v = Vector((-1e18, -1e18, -1e18))
    depsgraph = bpy.context.evaluated_depsgraph_get()

    for obj in mesh_objs:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        try:
            mw = eval_obj.matrix_world
            for v in mesh.vertices:
                wv = mw @ v.co
                min_v.x = min(min_v.x, wv.x); min_v.y = min(min_v.y, wv.y); min_v.z = min(min_v.z, wv.z)
                max_v.x = max(max_v.x, wv.x); max_v.y = max(max_v.y, wv.y); max_v.z = max(max_v.z, wv.z)
        finally:
            eval_obj.to_mesh_clear()

    size = max_v - min_v
    center = (max_v + min_v) * 0.5
    return min_v, max_v, size, center

def infer_unit_scale_from_raw_size(raw_size: Vector) -> float:
    max_dim = max(raw_size.x, raw_size.y, raw_size.z)
    if max_dim <= 0:
        return 1.0
    if 0.02 <= max_dim <= 50.0:
        return 1.0
    if 50.0 < max_dim <= 5000.0:
        return 0.01
    if max_dim > 5000.0:
        return 0.001
    return 1.0

def sample_target_box(cls: str, rng: random.Random, z_override=None):
    r = CLASS_RANGES.get(cls)
    if not r:
        return None
    tx = rng.uniform(r["x"][0], r["x"][1])
    ty = rng.uniform(r["y"][0], r["y"][1])
    if z_override:
        tz = rng.uniform(z_override[0], z_override[1])
    else:
        tz = rng.uniform(r["z"][0], r["z"][1])
    return Vector((tx, ty, tz))

def choose_uniform_scale_to_fit_random_box(cls: str, cur_size: Vector, rng: random.Random,
                                          max_trials: int = 32, clamp_min: float = 1e-4, clamp_max: float = 50.0,
                                          z_override=None):
    if cls not in CLASS_RANGES:
        return 1.0, None, 0.0

    eps = 1e-12
    if cur_size.x < eps or cur_size.y < eps or cur_size.z < eps:
        return 1.0, None, 0.0

    import math
    best_s = 1.0
    best_cost = 1e18
    best_tgt = None

    for _ in range(max_trials):
        tgt = sample_target_box(cls, rng, z_override=z_override)
        if tgt is None:
            break

        lr_x = math.log((tgt.x + eps) / (cur_size.x + eps))
        lr_y = math.log((tgt.y + eps) / (cur_size.y + eps))
        lr_z = math.log((tgt.z + eps) / (cur_size.z + eps))
        s = math.exp((lr_x + lr_y + lr_z) / 3.0)

        s = max(clamp_min, min(s, clamp_max))
        scaled = Vector((cur_size.x * s, cur_size.y * s, cur_size.z * s))

        cost = (math.log((scaled.x + eps) / (tgt.x + eps)) ** 2 +
                math.log((scaled.y + eps) / (tgt.y + eps)) ** 2 +
                math.log((scaled.z + eps) / (tgt.z + eps)) ** 2)

        if cost < best_cost:
            best_cost = cost
            best_s = s
            best_tgt = tgt

    return best_s, best_tgt, float(best_cost)

def export_usd(dst_path: str, export_textures: bool = True, retries: int = 3):
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    last_err = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            bpy.ops.wm.usd_export(
                filepath=dst_path,
                export_textures=export_textures,
                overwrite_textures=True,
                relative_paths=True
            )
            return True
        except Exception as e:
            last_err = e
            print(f"[WARN] USD export failed (attempt {attempt}/{retries}): {e}")
            time.sleep(0.6 * attempt)

    print(f"[ERROR] USD export permanently failed: {dst_path}\n  last_err={last_err}")
    return False

def count_total_faces(mesh_objs):
    total = 0
    for obj in mesh_objs:
        if obj.type == 'MESH' and obj.data:
            total += len(obj.data.polygons)
    return total

def decimate_meshes(mesh_objs, max_faces, min_mesh_faces=100):
    original_total = count_total_faces(mesh_objs)
    if original_total <= max_faces or original_total == 0:
        return original_total, original_total, 1.0

    ratio = max_faces / original_total
    ratio = max(0.05, ratio)

    for obj in mesh_objs:
        if obj.type != 'MESH' or not obj.data:
            continue
        face_count = len(obj.data.polygons)
        if face_count < min_mesh_faces:
            continue

        if obj.data.shape_keys:
            print(f"  [DECIMATE] Skipping '{obj.name}' (has shape keys, {face_count} faces)")
            continue

        try:
            mod = obj.modifiers.new(name="AutoDecimate", type='DECIMATE')
            mod.decimate_type = 'COLLAPSE'
            mod.ratio = ratio

            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as e:
            print(f"  [DECIMATE] Failed on '{obj.name}': {e}")
            if "AutoDecimate" in obj.modifiers:
                obj.modifiers.remove(obj.modifiers["AutoDecimate"])

    final_total = count_total_faces(mesh_objs)
    actual_ratio = final_total / original_total if original_total > 0 else 1.0
    return original_total, final_total, actual_ratio

def assert_scale_effect(old_size: Vector, new_size: Vector, s_applied: float) -> bool:
    eps = 1e-9
    old_max = max(old_size.x, old_size.y, old_size.z, eps)
    new_max = max(new_size.x, new_size.y, new_size.z, eps)
    ratio = new_max / old_max
    return (0.5 * s_applied) <= ratio <= (2.0 * s_applied)

# ---------------- Main ----------------

def _build_row(cls, asset_stem, glb_path, dst_usd, seed, key,
               unit_scale, s2, tgt_box, fit_cost,
               min_v, max_v, size2, center2,
               faces_before, faces_after, decimate_ratio, z_override):
    return {
        "class": cls,
        "asset": asset_stem,
        "src_glb": str(glb_path),
        "dst_usd": str(dst_usd),
        "global_seed": int(seed),
        "asset_key": key,
        "unit_scale": float(unit_scale),
        "semantic_scale": float(s2),
        "final_scale": float(unit_scale * s2),
        "target_x_m": float(tgt_box.x) if tgt_box else "",
        "target_y_m": float(tgt_box.y) if tgt_box else "",
        "target_z_m": float(tgt_box.z) if tgt_box else "",
        "fit_cost": float(fit_cost),
        "aabb_size_x_m": float(size2.x),
        "aabb_size_y_m": float(size2.y),
        "aabb_size_z_m": float(size2.z),
        "aabb_center_x_m": float(center2.x),
        "aabb_center_y_m": float(center2.y),
        "aabb_center_z_m": float(center2.z),
        "aabb_min_x_m": float(min_v.x),
        "aabb_min_y_m": float(min_v.y),
        "aabb_min_z_m": float(min_v.z),
        "aabb_max_x_m": float(max_v.x),
        "aabb_max_y_m": float(max_v.y),
        "aabb_max_z_m": float(max_v.z),
        "faces_before": int(faces_before),
        "faces_after": int(faces_after),
        "decimate_ratio": float(decimate_ratio),
        "height_tier_z_override": str(z_override) if z_override else "",
    }


def _write_registry(csv_path, rows):
    if not rows:
        return
    print(f"  Writing registry: {csv_path}  ({len(rows)} assets)")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    args = parse_args()
    if args.no_auto_unit_scale:
        args.auto_unit_scale = False
    if args.no_export_textures:
        args.export_textures = False

    in_root = Path(args.input).resolve()

    out_base = str(Path(args.output).resolve())
    out_original   = Path(out_base + "_original")
    out_simplified = Path(out_base + "_simplified")
    out_original.mkdir(parents=True, exist_ok=True)
    out_simplified.mkdir(parents=True, exist_ok=True)

    print(f"Output (original)  : {out_original}")
    print(f"Output (simplified): {out_simplified}")

    glbs = sorted(in_root.rglob("*.glb"))
    if not glbs:
        print(f"[ERROR] No .glb found under {in_root}")
        return

    # Pre-count assets per class for height-tier assignment
    from collections import Counter, defaultdict
    _class_totals = Counter()
    for _p in glbs:
        _r = _p.relative_to(in_root)
        _c = _r.parts[0] if len(_r.parts) >= 2 else "unknown"
        _class_totals[_c] += 1
    _class_idx = defaultdict(int)

    rows_original   = []
    rows_simplified = []

    for i, glb_path in enumerate(glbs, 1):
        rel = glb_path.relative_to(in_root)
        cls = rel.parts[0] if len(rel.parts) >= 2 else "unknown"
        asset_stem = glb_path.stem

        dst_dir_orig = out_original / rel.parent / asset_stem
        dst_usd_orig = (dst_dir_orig / asset_stem).with_suffix(".usd")
        dst_dir_simp = out_simplified / rel.parent / asset_stem
        dst_usd_simp = (dst_dir_simp / asset_stem).with_suffix(".usd")

        key = f"{cls}/{asset_stem}"
        rng = random.Random(args.seed + stable_int_hash(key))

        print(f"\n[{i}/{len(glbs)}] {rel}")

        reset_scene()

        imported_all, meshes = import_glb_capture(str(glb_path))
        if not meshes:
            print(f"[WARN] No mesh objects imported from {glb_path}")
            continue

        # A) raw AABB (pre-unit-scale)
        aabb0 = world_aabb_for_meshes(meshes)
        if aabb0 is None:
            print(f"[WARN] Failed raw AABB: {glb_path}")
            continue
        _, _, raw_size, _ = aabb0

        # B) decide unit scale
        if args.unit_scale is not None:
            unit_scale = float(args.unit_scale)
        else:
            unit_scale = infer_unit_scale_from_raw_size(raw_size) if args.auto_unit_scale else 1.0

        # C) apply unit scale to ROOTS
        scale_roots(imported_all, unit_scale)

        aabb1 = world_aabb_for_meshes(meshes)
        if aabb1 is None:
            print(f"[WARN] Failed AABB after unit scale: {glb_path}")
            continue
        _, _, size1, _ = aabb1

        # D) normalize into class range
        z_override = get_height_tier_z_range(cls, _class_idx[cls], _class_totals[cls])
        _class_idx[cls] += 1
        s2, tgt_box, fit_cost = choose_uniform_scale_to_fit_random_box(cls, size1, rng, z_override=z_override)
        scale_roots(imported_all, s2)

        aabb2 = world_aabb_for_meshes(meshes)
        if aabb2 is None:
            print(f"[WARN] Failed final AABB: {glb_path}")
            continue
        min_v, max_v, size2, center2 = aabb2

        # E) sanity check
        if not assert_scale_effect(size1, size2, s2):
            print(f"[WARN] Scale sanity check suspicious: s2={s2:.6f} size1={tuple(size1)} size2={tuple(size2)}")

        faces_full = count_total_faces(meshes)

        # --- Export ORIGINAL ---
        print(f"  [ORIGINAL]   {faces_full} faces -> {dst_usd_orig.relative_to(out_original)}")
        ok_orig = export_usd(str(dst_usd_orig), export_textures=args.export_textures, retries=args.export_retries)
        if ok_orig:
            rows_original.append(_build_row(
                cls, asset_stem, glb_path, dst_usd_orig, args.seed, key,
                unit_scale, s2, tgt_box, fit_cost,
                min_v, max_v, size2, center2,
                faces_full, faces_full, 1.0, z_override,
            ))

        # --- Decimate, then export SIMPLIFIED ---
        faces_after = faces_full
        decimate_ratio = 1.0
        if not args.no_decimate:
            cls_max = CLASS_MAX_FACES.get(cls, args.max_faces)
            if faces_full > cls_max:
                _, faces_after, decimate_ratio = decimate_meshes(meshes, cls_max)
                print(f"  [DECIMATE]   {faces_full} -> {faces_after} faces (ratio={decimate_ratio:.3f}, limit={cls_max})")
            else:
                print(f"  [SIMPLIFIED] {faces_full} faces <= {cls_max} limit, no decimation needed")

        print(f"  [SIMPLIFIED] {faces_after} faces -> {dst_usd_simp.relative_to(out_simplified)}")
        ok_simp = export_usd(str(dst_usd_simp), export_textures=args.export_textures, retries=args.export_retries)
        if ok_simp:
            rows_simplified.append(_build_row(
                cls, asset_stem, glb_path, dst_usd_simp, args.seed, key,
                unit_scale, s2, tgt_box, fit_cost,
                min_v, max_v, size2, center2,
                faces_full, faces_after, decimate_ratio, z_override,
            ))

        print(f"  unit_scale={unit_scale}  semantic_scale={s2:.6f}  final_size=({size2.x:.3f},{size2.y:.3f},{size2.z:.3f})"
              f"  target={tuple(tgt_box) if tgt_box else None}")

    # Write registries
    print(f"\n{'='*60}")
    _write_registry(out_original   / args.csv, rows_original)
    _write_registry(out_simplified / args.csv, rows_simplified)

    print(f"\nDone.  original={len(rows_original)}  simplified={len(rows_simplified)}")

if __name__ == "__main__":
    main()
