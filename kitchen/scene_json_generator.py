# scene_json_generator.py
# Difficulty (0..3) is locomotion-action-driven.
# The criterion is the BOTTLENECK (narrowest point) along the best viable path:
#
# 0: bottleneck >= 1m  (FRONT pass),  corridor CLEAR  (no step-over)
# 1: bottleneck >= 1m  (FRONT pass),  step-over obstacles ON the wide corridor
# 2: bottleneck < 1m but >= 0.5m (SIDE pass forced), corridor CLEAR (no step-over)
# 3: bottleneck < 1m but >= 0.5m (SIDE pass forced), step-over AT the narrow section
#
# Validation is via BFS reachability over the whole room (not just the corridor):
#   main BFS (radius 0.50m) checks for >= 1m passages
#   side BFS (radius 0.25m) checks for >= 0.5m passages
# d0/d1 require main=pass;  d2/d3 require main=fail AND side=pass.
#
# Adds annealing for crowdedness: after repeated failures, reduce item counts.
# Ensures: bed always exists; chair count >= 1 even under annealing.
# Improves chair placement robustness with 2-stage placement:
#   Stage A: avoid corridor_reserved (preferred)
#   Stage B: allow corridor_reserved (fallback), reachability will filter.

import argparse
import csv
import json
import math
import random
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from room_configs import get_config, RoomConfig


# -------------------- Defaults --------------------
GLOBAL_SEED = 202602081736

WALL_THICKNESS = 0.10
FLOOR_THICKNESS = 0.05
ROOM_HEIGHT = 2.6

# Placement margins
WALL_MARGIN = 0.06
OBJ_MARGIN = 0.05
ZONE_MARGIN = 0.10

# Humanoid standing zone (rect)
HUMANOID_ZONE_XY = (1.0, 1.0)  # meters

# -------------------- Locomotion Difficulty Params --------------------
# Per-difficulty corridor object height budget (meters).
# max:        tallest object allowed on the corridor
# prefer_min: forced corridor items prefer height >= this (makes step-over meaningful)
CORRIDOR_HEIGHT_BUDGET = {
    0: {"max": 0.0,  "prefer_min": 0.0},   # corridor clear — no objects
    1: {"max": 0.10, "prefer_min": 0.0},    # easy step-over  (<=10 cm): thin blankets, books, closed laptops
    2: {"max": 0.0,  "prefer_min": 0.0},    # corridor clear — no objects
    3: {"max": 0.20, "prefer_min": 0.05},   # hard step-over  (<=20 cm): shoes, thick books, thin pillows
}

# Width requirements (meters)
FRONT_PASS_WIDTH_M = 1.00  # 100 cm
SIDE_PASS_WIDTH_M = 0.50   # 50 cm

# For grid reachability we use "inflation radius" ~ half-width
HUMANOID_RADIUS_MAIN = FRONT_PASS_WIDTH_M / 2.0   # 0.50
HUMANOID_RADIUS_SIDE = SIDE_PASS_WIDTH_M / 2.0    # 0.25

GRID_RES = 0.10  # reachability grid resolution (meters)

# -------------------- Annealing Params --------------------
# every FAIL_WINDOW failed attempts => one anneal level
FAIL_WINDOW = 15

# Room tiers
ROOM_TIERS = {
    "small":  {"w": (3.0, 3.8), "l": (3.6, 4.6)},
    "medium": {"w": (3.8, 4.8), "l": (4.2, 5.8)},
    "large":  {"w": (4.8, 6.8), "l": (5.5, 7.5)},
}
ROOM_TIER_PROBS = [0.35, 0.45, 0.20]  # small/medium/large

# Crowdedness (high initial counts — annealing will reduce if needed)
TIER_COUNTS = {
    "small":  {"chairs": (1, 2), "small_items": (5, 10),  "cluster_items": (3, 6)},
    "medium": {"chairs": (1, 3), "small_items": (8, 16),  "cluster_items": (5, 10)},
    "large":  {"chairs": (2, 3), "small_items": (12, 20), "cluster_items": (6, 12)},
}

# Corridor width outer ranges (must span both d0/d1 wide and d2/d3 narrow).
# Upper bounds raised so d0/d1 can reliably pass main BFS (corridor >= 1.12m).
CORRIDOR_WIDTH_RANGE_BY_TIER = {
    "small":  (0.62, 1.20),
    "medium": (0.62, 1.30),
    "large":  (0.62, 1.40),
}
CORRIDOR_MARGIN = 0.05  # inflate corridor "reserved" rect a bit (for big objects)

BIG_YAW_CHOICES = [0.0, 90.0, 180.0, 270.0]

# Yaw (degrees) that makes the furniture front (-Y in local coords) face into the room.
WALL_FACING_YAW = {
    "LEFT":  90.0,   # front faces +X
    "RIGHT": 270.0,  # front faces -X
    "BACK":  180.0,  # front faces +Y
    "FRONT": 0.0,    # front faces -Y
}
FACE_ROOM_YAW_JITTER = 5.0  # degrees of random jitter for visual variety

# Wall-hugging big items: each selected CLASS appears at most ONE instance
WALL_BIG_CLASS_PROBS = {
    "small":  {"wardrobe": 0.60, "suitcase": 0.65, "desk": 0.55, "cabinet": 0.50},
    "medium": {"wardrobe": 0.80, "suitcase": 0.75, "desk": 0.80, "cabinet": 0.70},
    "large":  {"wardrobe": 0.90, "suitcase": 0.85, "desk": 0.90, "cabinet": 0.85},
}
WALL_BIG_MAX_BY_TIER = {"small": 3, "medium": 4, "large": 5}

# Candidate classes that may be placed on the corridor as step-over obstacles.
STEP_OVER_CANDIDATE_CLASSES = {
    "blanket", "shoes", "pillow",
    "book", "laptop",
    "sock", "socks",
}


# -------------------- Data Structures --------------------
@dataclass
class AssetAABB:
    cls: str
    asset: str
    dst_usd: str
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def height_m(self) -> float:
        return float(self.max_z - self.min_z)


@dataclass
class Placed:
    name: str
    cls: str
    usd_path: str
    prim_path: str
    pos: Tuple[float, float, float]
    rot_deg: Tuple[float, float, float]
    rigid_body: bool
    rect: Tuple[float, float, float, float]  # (minx,miny,maxx,maxy) in world
    height_m: float
    is_step_over: bool


# -------------------- Geometry Helpers --------------------
def rotate_xy(x: float, y: float, yaw_deg: float) -> Tuple[float, float]:
    rad = math.radians(yaw_deg)
    c, s = math.cos(rad), math.sin(rad)
    return c * x - s * y, s * x + c * y


def aabb2d_after_yaw(aabb: AssetAABB, yaw_deg: float) -> Tuple[float, float, float, float]:
    corners = [
        (aabb.min_x, aabb.min_y),
        (aabb.min_x, aabb.max_y),
        (aabb.max_x, aabb.min_y),
        (aabb.max_x, aabb.max_y),
    ]
    rx, ry = [], []
    for x, y in corners:
        xx, yy = rotate_xy(x, y, yaw_deg)
        rx.append(xx)
        ry.append(yy)
    return min(rx), min(ry), max(rx), max(ry)


def rect_translate(rect_local: Tuple[float, float, float, float], tx: float, ty: float) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect_local
    return (x0 + tx, y0 + ty, x1 + tx, y1 + ty)


def rects_overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float], margin: float = 0.0) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ax0 -= margin; ay0 -= margin; ax1 += margin; ay1 += margin
    bx0 -= margin; by0 -= margin; bx1 += margin; by1 += margin
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def within_room(rect: Tuple[float, float, float, float], room_w: float, room_l: float, wall_margin: float) -> bool:
    x0, y0, x1, y1 = rect
    return (x0 >= wall_margin and y0 >= wall_margin and x1 <= (room_w - wall_margin) and y1 <= (room_l - wall_margin))


def inflate_rect(rect: Tuple[float, float, float, float], r: float) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    return (x0 - r, y0 - r, x1 + r, y1 + r)


def place_on_floor_z(aabb: AssetAABB, spawn_extra: float = 0.0) -> float:
    return -aabb.min_z + spawn_extra


def rect_intersects_any(rect: Tuple[float, float, float, float], rects: List[Tuple[float, float, float, float]], margin: float = 0.0) -> bool:
    return any(rects_overlap(rect, r, margin=margin) for r in rects)


# -------------------- Shoe-pair helper --------------------
SHOE_PAIR_GAP = 0.04        # gap between two shoes (meters)
SHOE_PAIR_YAW_JITTER = 8.0  # degrees of random yaw difference


def make_shoe_pair_placed(
    aabb: AssetAABB,
    base_name: str,
    base_prim: str,
    tx: float, ty: float, z: float,
    yaw: float,
    rect: Tuple[float, float, float, float],
    is_step_over: bool,
    room_w: float, room_l: float,
    hard_forbidden: List[Tuple[float, float, float, float]],
    rng: random.Random,
) -> Optional['Placed']:
    """Place a 2nd shoe next to the 1st.  Returns a Placed or None on failure."""
    # offset perpendicular to yaw direction by (shoe_width + gap)
    shoe_w = aabb.max_y - aabb.min_y          # width of shoe (y axis)
    offset_dist = shoe_w + SHOE_PAIR_GAP
    perp_deg = yaw + 90.0
    dx, dy = rotate_xy(offset_dist, 0.0, perp_deg)
    tx2 = tx + dx
    ty2 = ty + dy
    yaw2 = yaw + rng.uniform(-SHOE_PAIR_YAW_JITTER, SHOE_PAIR_YAW_JITTER)

    local_rect2 = aabb2d_after_yaw(aabb, yaw2)
    world_rect2 = rect_translate(local_rect2, tx2, ty2)
    if not within_room(world_rect2, room_w, room_l, WALL_MARGIN):
        return None
    if any(rects_overlap(world_rect2, r, margin=OBJ_MARGIN) for r in hard_forbidden):
        return None

    return Placed(
        name=base_name + "_pair",
        cls="shoes",
        usd_path=aabb.dst_usd,
        prim_path=base_prim + "_pair",
        pos=(tx2, ty2, z),
        rot_deg=(0.0, 0.0, yaw2),
        rigid_body=False,
        rect=world_rect2,
        height_m=aabb.height_m,
        is_step_over=is_step_over,
    )


def is_corridor_passable(aabb: AssetAABB, difficulty: int, step_over_classes: set = None) -> bool:
    """Can this asset be stepped over on the corridor at the given difficulty?"""
    max_h = CORRIDOR_HEIGHT_BUDGET[difficulty]["max"]
    if max_h <= 0:
        return False
    candidates = step_over_classes if step_over_classes is not None else STEP_OVER_CANDIDATE_CLASSES
    if aabb.cls not in candidates:
        return False
    return aabb.height_m <= max_h


def pick_corridor_asset(
    assets: Dict[str, Dict[str, AssetAABB]],
    step_classes: List[str],
    difficulty: int,
    rng: random.Random,
) -> Optional[AssetAABB]:
    """Pre-filter assets by corridor height budget; prefer taller (more challenging) ones."""
    cfg = CORRIDOR_HEIGHT_BUDGET[difficulty]
    pool: List[AssetAABB] = []
    preferred: List[AssetAABB] = []
    for cls in step_classes:
        for asset in assets.get(cls, {}).values():
            if asset.height_m <= cfg["max"]:
                pool.append(asset)
                if asset.height_m >= cfg["prefer_min"]:
                    preferred.append(asset)
    if preferred:
        return rng.choice(preferred)
    if pool:
        return rng.choice(pool)
    return None


# -------------------- Annealing: dynamic counts --------------------
def compute_dynamic_counts(
    tier: str,
    rng: random.Random,
    attempt_index: int,
    difficulty: int = 0,
    fail_window: int = FAIL_WINDOW,
    config: "RoomConfig" = None,
) -> Tuple[int, int, int, int, int]:
    """
    Returns (chair_n, small_item_n, cluster_item_n, forced_step_items_max, wall_big_max)

    Annealing reduces counts in priority order (chairs reduced LAST):
      Level 0:  no reduction
      Level 1+: scatter items reduced first
      Level 2+: cluster items also reduced
      Level 3+: wall-big items reduced
      Level 4+: forced step-over items reduced
      Level 5+: chairs reduced (last resort)

    Difficulty 0: chair minimum = 0  (simplest environment, chairs optional)
    Difficulty 1-3: chair minimum = 1
    Anchor item is always placed (handled outside this function).
    """
    tier_counts = config.tier_counts if config else TIER_COUNTS
    base = tier_counts[tier]
    anneal_level = attempt_index // fail_window  # 0,1,2,...

    chair_lo, chair_hi = base["chairs"]
    small_lo, small_hi = base["small_items"]
    clu_lo, clu_hi = base["cluster_items"]

    # baseline sample
    chair_n = rng.randint(chair_lo, chair_hi)
    small_item_n = rng.randint(small_lo, small_hi)
    cluster_item_n = rng.randint(clu_lo, clu_hi)

    # Chair minimum depends on difficulty
    chair_min = 0 if difficulty == 0 else 1
    chair_n = max(chair_min, chair_n)

    # Wall-big cap from tier default
    wbm = config.wall_big_max_by_tier if config else WALL_BIG_MAX_BY_TIER
    wall_big_base = wbm.get(tier, 3)

    # --- Annealing schedule (priority: scatter > cluster > wall_big > step > chairs) ---
    # Scatter: reduce starting level 1, floor at 0
    scatter_scale = max(0.0, 1.0 - 0.30 * anneal_level)
    small_item_n = max(0, int(round(small_item_n * scatter_scale)))

    # Cluster: reduce starting level 2, floor at 0
    cluster_scale = max(0.0, 1.0 - 0.30 * max(0, anneal_level - 1))
    cluster_item_n = max(0, int(round(cluster_item_n * cluster_scale)))

    # Wall-big: reduce starting level 3
    wall_big_max = max(0, wall_big_base - max(0, anneal_level - 2))

    # Forced step-over: reduce starting level 4 (keep >=1 for difficulty that needs it)
    forced_step_items_max = max(1, 4 - max(0, anneal_level - 3))

    # Chairs: reduce LAST, starting level 5
    chair_drop = max(0, anneal_level - 4)
    chair_n = max(chair_min, chair_n - chair_drop)

    return chair_n, small_item_n, cluster_item_n, forced_step_items_max, wall_big_max


# -------------------- Registry Loading --------------------
def _build_usd_path_map(
    assets_orig: Dict[str, Dict[str, "AssetAABB"]],
    assets_simp: Dict[str, Dict[str, "AssetAABB"]],
) -> Dict[str, str]:
    """Map original dst_usd -> simplified dst_usd for every matching asset."""
    mapping: Dict[str, str] = {}
    for cls, adict in assets_orig.items():
        for name, aabb in adict.items():
            if cls in assets_simp and name in assets_simp[cls]:
                mapping[aabb.dst_usd] = assets_simp[cls][name].dst_usd
    return mapping


def _remap_scene_usd_paths(scene: Dict, usd_path_map: Dict[str, str]) -> Dict:
    """Return a deep copy of scene with usd_path values replaced."""
    import copy
    s = copy.deepcopy(scene)
    for obj in s["objects"]:
        orig = obj.get("usd_path", "")
        if orig and orig in usd_path_map:
            obj["usd_path"] = usd_path_map[orig]
    return s


def load_texture_catalog(path: str) -> Dict:
    """Load and validate a texture_catalog.json file."""
    with open(path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    for surface in ("floor", "wall"):
        if surface not in catalog or not catalog[surface]:
            raise RuntimeError(f"Texture catalog missing or empty '{surface}' list in {path}")
        for entry in catalog[surface]:
            for key in ("id", "color", "normal", "roughness", "tiles_per_meter"):
                if key not in entry:
                    raise RuntimeError(f"Texture entry missing '{key}': {entry}")
    return catalog


def load_registry(csv_path: str) -> Dict[str, Dict[str, AssetAABB]]:
    assets: Dict[str, Dict[str, AssetAABB]] = {}
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        required = {
            "class", "asset", "dst_usd",
            "aabb_min_x_m", "aabb_min_y_m", "aabb_min_z_m",
            "aabb_max_x_m", "aabb_max_y_m", "aabb_max_z_m",
        }
        missing = [c for c in required if c not in (r.fieldnames or [])]
        if missing:
            raise RuntimeError(f"CSV missing required columns: {missing}")

        for row in r:
            cls = row["class"].strip()
            asset = row["asset"].strip()
            dst_usd = row["dst_usd"].strip().replace("\\", "/")

            aabb = AssetAABB(
                cls=cls,
                asset=asset,
                dst_usd=dst_usd,
                min_x=float(row["aabb_min_x_m"]),
                min_y=float(row["aabb_min_y_m"]),
                min_z=float(row["aabb_min_z_m"]),
                max_x=float(row["aabb_max_x_m"]),
                max_y=float(row["aabb_max_y_m"]),
                max_z=float(row["aabb_max_z_m"]),
            )
            assets.setdefault(cls, {})[asset] = aabb
    return assets


def stable_int_hash(s: str) -> int:
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def pick_existing_asset(assets: Dict[str, Dict[str, AssetAABB]], cls: str, candidates: List[str], rng: random.Random) -> AssetAABB:
    pool = [assets[cls][a] for a in candidates if cls in assets and a in assets[cls]]
    if not pool:
        raise RuntimeError(f"No assets found in registry for class={cls} candidates={candidates[:5]}...")
    return rng.choice(pool)


def pick_random_from_class(assets: Dict[str, Dict[str, AssetAABB]], cls: str, rng: random.Random) -> AssetAABB:
    if cls not in assets or not assets[cls]:
        raise RuntimeError(f"No assets for class={cls} in registry")
    return rng.choice(list(assets[cls].values()))


# -------------------- Sampling --------------------
def choose_room_tier(rng: random.Random, config: "RoomConfig" = None) -> str:
    tiers = list((config.room_tiers if config else ROOM_TIERS).keys())
    probs = config.room_tier_probs if config else ROOM_TIER_PROBS
    x = rng.random()
    cum = 0.0
    for t, p in zip(tiers, probs):
        cum += p
        if x <= cum:
            return t
    return tiers[-1]


def sample_room_size(tier: str, rng: random.Random, config: "RoomConfig" = None) -> Tuple[float, float]:
    room_tiers = config.room_tiers if config else ROOM_TIERS
    r = room_tiers[tier]
    w = rng.uniform(r["w"][0], r["w"][1])
    l = rng.uniform(r["l"][0], r["l"][1])
    return round(w, 3), round(l, 3)


def resolve_room_size(
    tier: str,
    rng: random.Random,
    fixed_w: Optional[float] = None,
    fixed_l: Optional[float] = None,
    config: "RoomConfig" = None,
) -> Tuple[float, float]:
    if (fixed_w is None) ^ (fixed_l is None):
        raise RuntimeError("You must provide BOTH --room_w and --room_l, or neither.")
    if fixed_w is not None and fixed_l is not None:
        return round(float(fixed_w), 3), round(float(fixed_l), 3)
    return sample_room_size(tier, rng, config=config)


def sample_free_zone_rect(
    room_w: float,
    room_l: float,
    zone_w: float,
    zone_l: float,
    forbidden_rects: List[Tuple[float, float, float, float]],
    rng: random.Random,
    prefer_corner: Optional[str] = None,
    max_tries: int = 900,
) -> Tuple[float, float, float, float]:
    def rect_from_center(cx, cy):
        x0 = cx - zone_w / 2
        y0 = cy - zone_l / 2
        x1 = cx + zone_w / 2
        y1 = cy + zone_l / 2
        return (x0, y0, x1, y1)

    corner_centers = {
        "BL": (WALL_MARGIN + ZONE_MARGIN + zone_w / 2, WALL_MARGIN + ZONE_MARGIN + zone_l / 2),
        "BR": (room_w - (WALL_MARGIN + ZONE_MARGIN + zone_w / 2), WALL_MARGIN + ZONE_MARGIN + zone_l / 2),
        "TL": (WALL_MARGIN + ZONE_MARGIN + zone_w / 2, room_l - (WALL_MARGIN + ZONE_MARGIN + zone_l / 2)),
        "TR": (room_w - (WALL_MARGIN + ZONE_MARGIN + zone_w / 2), room_l - (WALL_MARGIN + ZONE_MARGIN + zone_l / 2)),
    }

    if prefer_corner in corner_centers:
        cx, cy = corner_centers[prefer_corner]
        rect = rect_from_center(cx, cy)
        if within_room(rect, room_w, room_l, WALL_MARGIN) and not any(rects_overlap(rect, r, margin=OBJ_MARGIN) for r in forbidden_rects):
            return rect

    x_min = WALL_MARGIN + ZONE_MARGIN + zone_w / 2
    x_max = room_w - (WALL_MARGIN + ZONE_MARGIN + zone_w / 2)
    y_min = WALL_MARGIN + ZONE_MARGIN + zone_l / 2
    y_max = room_l - (WALL_MARGIN + ZONE_MARGIN + zone_l / 2)
    if x_max <= x_min or y_max <= y_min:
        raise RuntimeError("Room too small for humanoid zones")

    for _ in range(max_tries):
        cx = rng.uniform(x_min, x_max)
        cy = rng.uniform(y_min, y_max)
        rect = rect_from_center(cx, cy)
        if any(rects_overlap(rect, r, margin=OBJ_MARGIN) for r in forbidden_rects):
            continue
        return rect

    raise RuntimeError("Failed to sample free humanoid zone")


def place_against_wall(
    room_w: float,
    room_l: float,
    aabb: AssetAABB,
    forbidden_rects: List[Tuple[float, float, float, float]],
    rng: random.Random,
    yaw_choices: List[float],
    max_tries: int = 1600,
    face_room: bool = False,
) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float], str]:
    walls = ["LEFT", "RIGHT", "BACK", "FRONT"]
    for _ in range(max_tries):
        wall = rng.choice(walls)
        if face_room:
            yaw = WALL_FACING_YAW[wall] + rng.uniform(-FACE_ROOM_YAW_JITTER, FACE_ROOM_YAW_JITTER)
        else:
            yaw = rng.choice(yaw_choices)
        local_rect = aabb2d_after_yaw(aabb, yaw)
        lx0, ly0, lx1, ly1 = local_rect

        if wall == "LEFT":
            tx = WALL_MARGIN - lx0
            y_min = WALL_MARGIN - ly0
            y_max = (room_l - WALL_MARGIN) - ly1
            if y_max <= y_min:
                continue
            ty = rng.uniform(y_min, y_max)
        elif wall == "RIGHT":
            tx = (room_w - WALL_MARGIN) - lx1
            y_min = WALL_MARGIN - ly0
            y_max = (room_l - WALL_MARGIN) - ly1
            if y_max <= y_min:
                continue
            ty = rng.uniform(y_min, y_max)
        elif wall == "BACK":
            ty = WALL_MARGIN - ly0
            x_min = WALL_MARGIN - lx0
            x_max = (room_w - WALL_MARGIN) - lx1
            if x_max <= x_min:
                continue
            tx = rng.uniform(x_min, x_max)
        else:  # FRONT
            ty = (room_l - WALL_MARGIN) - ly1
            x_min = WALL_MARGIN - lx0
            x_max = (room_w - WALL_MARGIN) - lx1
            if x_max <= x_min:
                continue
            tx = rng.uniform(x_min, x_max)

        world_rect = rect_translate(local_rect, tx, ty)
        if not within_room(world_rect, room_w, room_l, WALL_MARGIN):
            continue
        if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in forbidden_rects):
            continue

        return (tx, ty), float(yaw), world_rect, wall

    raise RuntimeError(f"Failed to place {aabb.cls}/{aabb.asset} against wall without overlap")


def sample_free_pose_any_yaw(
    room_w: float,
    room_l: float,
    aabb: AssetAABB,
    forbidden_rects: List[Tuple[float, float, float, float]],
    rng: random.Random,
    yaw_range: Tuple[float, float] = (0.0, 360.0),
    max_tries: int = 9000,  # increased for robustness
) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float]]:
    for _ in range(max_tries):
        yaw = rng.uniform(yaw_range[0], yaw_range[1])
        local_rect = aabb2d_after_yaw(aabb, yaw)
        lx0, ly0, lx1, ly1 = local_rect

        x_min = WALL_MARGIN - lx0
        x_max = (room_w - WALL_MARGIN) - lx1
        y_min = WALL_MARGIN - ly0
        y_max = (room_l - WALL_MARGIN) - ly1
        if x_max <= x_min or y_max <= y_min:
            continue

        tx = rng.uniform(x_min, x_max)
        ty = rng.uniform(y_min, y_max)
        world_rect = rect_translate(local_rect, tx, ty)

        if not within_room(world_rect, room_w, room_l, WALL_MARGIN):
            continue
        if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in forbidden_rects):
            continue

        return (tx, ty), float(yaw), world_rect

    raise RuntimeError(f"Failed free placement for {aabb.cls}/{aabb.asset}")


# -------------------- Corridor Reservation --------------------
def make_L_corridor_rects(origin_zone, dest_zone, width: float):
    ox0, oy0, ox1, oy1 = origin_zone
    dx0, dy0, dx1, dy1 = dest_zone
    ocx, ocy = (ox0 + ox1) * 0.5, (oy0 + oy1) * 0.5
    dcx, dcy = (dx0 + dx1) * 0.5, (dy0 + dy1) * 0.5

    elbow1 = (dcx, ocy)
    elbow2 = (ocx, dcy)

    def corridor_rect(p0, p1):
        x0, y0 = p0
        x1, y1 = p1
        if abs(x1 - x0) >= abs(y1 - y0):
            xa, xb = sorted([x0, x1])
            return (xa, y0 - width / 2, xb, y0 + width / 2)
        else:
            ya, yb = sorted([y0, y1])
            return (x0 - width / 2, ya, x0 + width / 2, yb)

    rects_opt1 = [corridor_rect((ocx, ocy), elbow1), corridor_rect(elbow1, (dcx, dcy))]
    rects_opt2 = [corridor_rect((ocx, ocy), elbow2), corridor_rect(elbow2, (dcx, dcy))]
    return rects_opt1, rects_opt2


def corridor_fits_room(rects, room_w, room_l, wall_margin):
    return all(within_room(r, room_w, room_l, wall_margin) for r in rects)


# -------------------- Reachability --------------------
def is_reachable(
    room_w: float,
    room_l: float,
    obstacles_rects: List[Tuple[float, float, float, float]],
    origin_zone: Tuple[float, float, float, float],
    dest_zone: Tuple[float, float, float, float],
    humanoid_radius: float,
    grid_res: float,
) -> bool:
    # The humanoid is modelled as a circle of `humanoid_radius`.  Its center
    # cannot be closer than `humanoid_radius` to any wall, just as it cannot
    # be closer than `humanoid_radius` to any obstacle (handled by inflation).
    # Using WALL_MARGIN (0.06m) here would let the BFS treat walls as
    # non-obstacles, allowing false "main" passages along walls.
    margin = max(WALL_MARGIN, humanoid_radius)
    x0 = margin
    y0 = margin
    x1 = room_w - margin
    y1 = room_l - margin
    if x1 <= x0 or y1 <= y0:
        return False

    nx = int(math.ceil((x1 - x0) / grid_res))
    ny = int(math.ceil((y1 - y0) / grid_res))
    if nx <= 2 or ny <= 2:
        return False

    inflated = [inflate_rect(r, humanoid_radius) for r in obstacles_rects]

    def cell_center(ix: int, iy: int) -> Tuple[float, float]:
        cx = x0 + (ix + 0.5) * grid_res
        cy = y0 + (iy + 0.5) * grid_res
        return cx, cy

    def point_in_rect(px: float, py: float, rect: Tuple[float, float, float, float]) -> bool:
        rx0, ry0, rx1, ry1 = rect
        return (px >= rx0 and py >= ry0 and px <= rx1 and py <= ry1)

    def blocked(ix: int, iy: int) -> bool:
        cx, cy = cell_center(ix, iy)
        if cx < x0 or cx > x1 or cy < y0 or cy > y1:
            return True
        for r in inflated:
            if point_in_rect(cx, cy, r):
                return True
        return False

    starts = []
    goals = set()
    for iy in range(ny):
        for ix in range(nx):
            cx, cy = cell_center(ix, iy)
            if point_in_rect(cx, cy, origin_zone) and not blocked(ix, iy):
                starts.append((ix, iy))
            if point_in_rect(cx, cy, dest_zone) and not blocked(ix, iy):
                goals.add((ix, iy))

    if not starts or not goals:
        return False

    from collections import deque
    q = deque(starts)
    visited = set(starts)
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    while q:
        ix, iy = q.popleft()
        if (ix, iy) in goals:
            return True
        for dx, dy in dirs:
            jx, jy = ix + dx, iy + dy
            if jx < 0 or jx >= nx or jy < 0 or jy >= ny:
                continue
            if (jx, jy) in visited:
                continue
            if blocked(jx, jy):
                continue
            visited.add((jx, jy))
            q.append((jx, jy))
    return False


def reachability_with_side_step(
    room_w: float,
    room_l: float,
    obstacles_rects,
    origin_zone,
    dest_zone,
    grid_res: float = GRID_RES,
) -> Tuple[bool, str]:
    ok_main = is_reachable(
        room_w, room_l, obstacles_rects, origin_zone, dest_zone,
        humanoid_radius=HUMANOID_RADIUS_MAIN, grid_res=grid_res
    )
    if ok_main:
        return True, "main"

    ok_side = is_reachable(
        room_w, room_l, obstacles_rects, origin_zone, dest_zone,
        humanoid_radius=HUMANOID_RADIUS_SIDE, grid_res=grid_res
    )
    if ok_side:
        return True, "side"

    return False, "blocked"


# -------------------- Wall-big class selection --------------------
def pick_wall_big_classes(
    tier: str,
    assets: Dict[str, Dict[str, AssetAABB]],
    rng: random.Random,
    config: "RoomConfig" = None,
) -> List[str]:
    wbcp = config.wall_big_class_probs if config else WALL_BIG_CLASS_PROBS
    probs = wbcp.get(tier, {})
    candidates = [c for c in probs.keys() if c in assets and assets[c]]
    selected = []
    for c in candidates:
        if rng.random() < probs[c]:
            selected.append(c)
    rng.shuffle(selected)
    wbm = config.wall_big_max_by_tier if config else WALL_BIG_MAX_BY_TIER
    cap = wbm.get(tier, 3)
    return selected[:cap]


# -------------------- Markers (cone primitives) --------------------
MARKER_RADIUS = 0.03   # meters
MARKER_HEIGHT = 0.15   # meters
MARKER_COLORS = {
    "origin": [0.0, 1.0, 0.0, 1.0],   # green
    "dest":   [1.0, 0.0, 0.0, 1.0],   # red
}


def make_marker_placed(
    name: str,
    prim_path: str,
    zone_rect: Tuple[float, float, float, float],
    marker_kind: str,
) -> Placed:
    """Create a cone marker at the center of a zone (no USD asset needed)."""
    zx0, zy0, zx1, zy1 = zone_rect
    cx = (zx0 + zx1) * 0.5
    cy = (zy0 + zy1) * 0.5

    r = MARKER_RADIUS
    world_rect = (cx - r, cy - r, cx + r, cy + r)

    return Placed(
        name=name,
        cls="marker",
        usd_path="",          # no USD file — generator_isaac creates geometry
        prim_path=prim_path,
        pos=(cx, cy, 0.0),    # base sits on floor
        rot_deg=(0.0, 0.0, 0.0),
        rigid_body=False,
        rect=world_rect,
        height_m=MARKER_HEIGHT,
        is_step_over=True,     # never block navigation
    )


# -------------------- Corridor step-over forced placement --------------------
def sample_pose_in_corridor(
    room_w: float,
    room_l: float,
    aabb: AssetAABB,
    hard_forbidden: List[Tuple[float, float, float, float]],
    corridor_rects: List[Tuple[float, float, float, float]],
    rng: random.Random,
    max_tries: int = 7000,
) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float]]:
    """Place step-over item anywhere on the corridor (d1: wide corridor)."""
    for _ in range(max_tries):
        yaw = rng.uniform(0.0, 360.0)
        local_rect = aabb2d_after_yaw(aabb, yaw)
        lx0, ly0, lx1, ly1 = local_rect
        lcx = (lx0 + lx1) * 0.5
        lcy = (ly0 + ly1) * 0.5

        cr = rng.choice(corridor_rects)
        cx = rng.uniform(cr[0], cr[2])
        cy = rng.uniform(cr[1], cr[3])

        tx = cx - lcx
        ty = cy - lcy
        world_rect = rect_translate(local_rect, tx, ty)

        if not within_room(world_rect, room_w, room_l, WALL_MARGIN):
            continue
        if not rect_intersects_any(world_rect, corridor_rects, margin=0.0):
            continue
        if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in hard_forbidden):
            continue

        return (tx, ty), float(yaw), world_rect

    raise RuntimeError(f"Failed to place step-over item in corridor: {aabb.cls}/{aabb.asset}")


def sample_spanning_pose_in_corridor(
    room_w: float,
    room_l: float,
    aabb: AssetAABB,
    hard_forbidden: List[Tuple[float, float, float, float]],
    corridor_rects: List[Tuple[float, float, float, float]],
    rng: random.Random,
    max_tries: int = 7000,
) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float]]:
    """Place step-over item spanning the corridor width (d3: simultaneous sidestep+step-over).

    The object is oriented perpendicular to the corridor direction and centered
    cross-wise, so the robot cannot sidestep around it.  Only the straight
    sections of the corridor are used (avoids the L-junction overlap area).
    """
    for _ in range(max_tries):
        cr = rng.choice(corridor_rects)
        cr_xlen = cr[2] - cr[0]
        cr_ylen = cr[3] - cr[1]
        is_horizontal = (cr_xlen > cr_ylen)

        # Pick yaw that maximises cross-corridor extent
        best_yaw = 0.0
        best_cross = 0.0
        for yaw_cand in [0.0, 90.0, 180.0, 270.0]:
            lr = aabb2d_after_yaw(aabb, yaw_cand)
            cross = (lr[3] - lr[1]) if is_horizontal else (lr[2] - lr[0])
            if cross > best_cross:
                best_cross = cross
                best_yaw = yaw_cand

        yaw = best_yaw + rng.uniform(-5.0, 5.0)
        local_rect = aabb2d_after_yaw(aabb, yaw)
        lx0, ly0, lx1, ly1 = local_rect
        lcx = (lx0 + lx1) * 0.5
        lcy = (ly0 + ly1) * 0.5

        cr_cx = (cr[0] + cr[2]) * 0.5
        cr_cy = (cr[1] + cr[3]) * 0.5

        if is_horizontal:
            # Corridor runs along X → center Y, random X (avoid ends / junction)
            along_half = (lx1 - lx0) * 0.5 + 0.15
            x_lo = cr[0] + along_half
            x_hi = cr[2] - along_half
            if x_hi <= x_lo:
                continue
            cx = rng.uniform(x_lo, x_hi)
            cy = cr_cy       # centred cross-wise
        else:
            # Corridor runs along Y → center X, random Y
            along_half = (ly1 - ly0) * 0.5 + 0.15
            y_lo = cr[1] + along_half
            y_hi = cr[3] - along_half
            if y_hi <= y_lo:
                continue
            cx = cr_cx       # centred cross-wise
            cy = rng.uniform(y_lo, y_hi)

        tx = cx - lcx
        ty = cy - lcy
        world_rect = rect_translate(local_rect, tx, ty)

        if not within_room(world_rect, room_w, room_l, WALL_MARGIN):
            continue
        if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in hard_forbidden):
            continue

        return (tx, ty), float(yaw), world_rect

    raise RuntimeError(f"Failed to place spanning step-over in corridor: {aabb.cls}/{aabb.asset}")


def pick_spanning_corridor_asset(
    assets: Dict[str, Dict[str, AssetAABB]],
    step_classes: List[str],
    difficulty: int,
    corridor_w: float,
    rng: random.Random,
) -> Optional[AssetAABB]:
    """For d3: select step-over asset that best spans the corridor width.

    Prefers objects whose longest horizontal dimension covers >= 60% of the
    corridor width, so the robot cannot sidestep around it.
    """
    cfg = CORRIDOR_HEIGHT_BUDGET[difficulty]
    pool: List[Tuple[AssetAABB, float]] = []
    for cls in step_classes:
        for asset in assets.get(cls, {}).values():
            if asset.height_m <= cfg["max"]:
                max_extent = max(asset.max_x - asset.min_x, asset.max_y - asset.min_y)
                pool.append((asset, max_extent))
    if not pool:
        return None

    # Prefer objects spanning >=60% of corridor width
    good = [(a, e) for a, e in pool if e >= corridor_w * 0.6]
    if good:
        return rng.choice(good)[0]
    # Fallback: widest available
    pool.sort(key=lambda x: -x[1])
    return pool[0][0]


# -------------------- Scene Builder --------------------
def build_one_scene(
    assets: Dict[str, Dict[str, AssetAABB]],
    rng: random.Random,
    require_reachable: bool = True,
    forced_tier: Optional[str] = None,
    fixed_room_w: Optional[float] = None,
    fixed_room_l: Optional[float] = None,
    difficulty: Optional[int] = None,
    attempt_index: int = 0,
    atom: bool = False,
    texture_catalog: Optional[Dict] = None,
    config: Optional["RoomConfig"] = None,
) -> Dict:
    # Tier
    if forced_tier is None or forced_tier == "random":
        tier = choose_room_tier(rng, config=config)
    else:
        tier = forced_tier

    # Difficulty
    if difficulty is None:
        difficulty = rng.choice([0, 1, 2, 3])
    if difficulty not in (0, 1, 2, 3):
        raise RuntimeError("difficulty must be 0..3")

    room_w, room_l = resolve_room_size(tier, rng, fixed_room_w, fixed_room_l, config=config)
    room_h = config.room_height if config else ROOM_HEIGHT

    # Read config-derived sets for use throughout
    step_over_classes = config.step_over_candidate_classes if config else STEP_OVER_CANDIDATE_CLASSES
    pair_classes = config.pair_classes if config else {"shoes"}
    room_name = config.name if config else "bedroom"

    # Dynamic annealed counts (chairs reduced last; difficulty 0 may have 0 chairs)
    chair_n, small_item_n, cluster_item_n, forced_step_items_max, wall_big_max = compute_dynamic_counts(
        tier=tier, rng=rng, attempt_index=attempt_index, difficulty=difficulty, fail_window=FAIL_WINDOW,
        config=config,
    )

    placed: List[Placed] = []

    hard_forbidden: List[Tuple[float, float, float, float]] = []
    corridor_reserved: List[Tuple[float, float, float, float]] = []

    # 1) Origin + destination zones
    zone_w, zone_l = HUMANOID_ZONE_XY
    origin_corner = rng.choice(["BL", "BR", "TL", "TR"])
    dest_corner = {"BL": "TR", "TR": "BL", "BR": "TL", "TL": "BR"}[origin_corner]

    origin_zone = sample_free_zone_rect(room_w, room_l, zone_w, zone_l, hard_forbidden, rng, prefer_corner=origin_corner)
    hard_forbidden.append(origin_zone)
    dest_zone = sample_free_zone_rect(room_w, room_l, zone_w, zone_l, hard_forbidden, rng, prefer_corner=dest_corner)
    hard_forbidden.append(dest_zone)

    # 2) Corridor width sampled by difficulty
    #
    # BFS models the humanoid as a circle inflated around obstacles AND walls
    # (see is_reachable).  Grid discretisation (GRID_RES=0.10m) means the
    # free-zone width must exceed GRID_RES for reliable cell detection.
    #
    # d0/d1 (main BFS must PASS):  corridor_w >= 1.12m
    #   worst case (furniture both sides): free = corridor_w - 2*(R_main - CORR_MARGIN)
    #   = corridor_w - 0.90;  need >= GRID_RES+0.02 => corridor_w >= 1.12
    #
    # d2/d3 (main BFS must FAIL, side BFS must PASS):  0.62 <= corridor_w <= 0.76
    #   near-wall worst case: free_main = corridor_w/2 - 0.29; need < GRID_RES => cw < 0.78
    #   side free near-wall:  >= corridor_w/2 - 0.04;  need >= GRID_RES => cw >= 0.28 (easy)
    #   side free both-sides: corridor_w - 0.40;  need >= GRID_RES+0.02 => cw >= 0.52
    #   conservative: lo=0.62, hi=0.76
    cwr = config.corridor_width_range_by_tier if config else CORRIDOR_WIDTH_RANGE_BY_TIER
    w_lo, w_hi = cwr[tier]

    if difficulty in (0, 1):  # FRONT pass — wide corridor
        lo = max(w_lo, FRONT_PASS_WIDTH_M + GRID_RES + 0.02)  # 1.12m
        hi = w_hi
        if hi <= lo:
            raise RuntimeError("Tier corridor range cannot satisfy FRONT width.")
        corridor_w = rng.uniform(lo, hi)
    else:  # SIDE-only (d2, d3) — narrow bottleneck
        lo = max(w_lo, SIDE_PASS_WIDTH_M + GRID_RES + 0.02)   # 0.62m
        hi = min(w_hi, 0.76)                                   # main BFS must fail near walls
        if hi <= lo:
            raise RuntimeError("Tier corridor range cannot satisfy SIDE-only width.")
        corridor_w = rng.uniform(lo, hi)

    opt1, opt2 = make_L_corridor_rects(origin_zone, dest_zone, corridor_w)
    if corridor_fits_room(opt1, room_w, room_l, WALL_MARGIN):
        corridor_rects = opt1
    elif corridor_fits_room(opt2, room_w, room_l, WALL_MARGIN):
        corridor_rects = opt2
    else:
        corridor_rects = opt1

    corridor_reserved = [inflate_rect(cr, CORRIDOR_MARGIN) for cr in corridor_rects]

    def commit_object_rect(rect: Tuple[float, float, float, float]):
        hard_forbidden.append(rect)

    allow_step_on_corridor = (difficulty in (1, 3))
    forbid_step_on_corridor = (difficulty in (0, 2))
    # d2+d3 combo: place step-over items OUTSIDE corridor (subset inclusion of d1)
    # atom mode skips this — atom scenes contain ONLY the single difficulty action
    force_step_off_corridor = (difficulty in (2, 3)) and (not atom)

    # 3) Place anchor item (bed / sofa — always at least one)
    anchor_cls = config.anchor_class if config else "bed"
    anchor_cands = config.anchor_candidates if config else [f"bed{idx:02d}" for idx in range(1, 20)]
    if anchor_cands is not None:
        anchor_aabb = pick_existing_asset(assets, anchor_cls, anchor_cands, rng)
    else:
        anchor_aabb = pick_random_from_class(assets, anchor_cls, rng)
    anchor_forbidden = hard_forbidden + corridor_reserved
    _anchor_face = anchor_cls in (config.orientation_sensitive_classes if config else {"bed"})
    (tx, ty), yaw, anchor_rect, _ = place_against_wall(
        room_w, room_l, anchor_aabb, anchor_forbidden, rng,
        yaw_choices=BIG_YAW_CHOICES, face_room=_anchor_face)
    anchor_z = place_on_floor_z(anchor_aabb, spawn_extra=0.0)
    anchor_name = anchor_aabb.asset
    placed.append(Placed(
        name=anchor_name,
        cls=anchor_cls,
        usd_path=anchor_aabb.dst_usd,
        prim_path=f"/World/Assets/{anchor_name}",
        pos=(tx, ty, anchor_z),
        rot_deg=(0.0, 0.0, yaw),
        rigid_body=False,
        rect=anchor_rect,
        height_m=anchor_aabb.height_m,
        is_step_over=is_corridor_passable(anchor_aabb, difficulty, step_over_classes),
    ))
    commit_object_rect(anchor_rect)

    # 4) Wall-hugging big items (avoid corridor_reserved, capped by annealing)
    wall_big_classes = pick_wall_big_classes(tier, assets, rng, config=config)
    wall_big_classes = wall_big_classes[:wall_big_max]
    for cls in wall_big_classes:
        big_aabb = pick_random_from_class(assets, cls, rng)
        forb = hard_forbidden + corridor_reserved
        _big_face = cls in (config.orientation_sensitive_classes if config else {"bed", "desk", "wardrobe", "cabinet"})
        (tx, ty), yaw, rect, _ = place_against_wall(
            room_w, room_l, big_aabb, forb, rng,
            yaw_choices=BIG_YAW_CHOICES, face_room=_big_face)
        z = place_on_floor_z(big_aabb, spawn_extra=0.0)
        name = f"{cls}_{big_aabb.asset}"
        placed.append(Placed(
            name=name,
            cls=cls,
            usd_path=big_aabb.dst_usd,
            prim_path=f"/World/Assets/{name}",
            pos=(tx, ty, z),
            rot_deg=(0.0, 0.0, yaw),
            rigid_body=False,
            rect=rect,
            height_m=big_aabb.height_m,
            is_step_over=is_corridor_passable(big_aabb, difficulty, step_over_classes),
        ))
        commit_object_rect(rect)

    # 5) Free-medium items with 2-stage placement (reduced LAST by annealing)
    # Stage A: avoid corridor_reserved
    # Stage B: allow corridor_reserved (fallback), reachability later filters bad cases
    free_med_classes = config.free_medium_classes if config else ["chair"]
    # Filter to classes that exist in registry
    free_med_avail = [c for c in free_med_classes if c in assets and assets[c]]
    if chair_n > 0 and not free_med_avail:
        raise RuntimeError("No free-medium classes available in registry but chairs/items are required.")

    for k in range(chair_n):
        fm_cls = rng.choice(free_med_avail) if free_med_avail else "chair"
        fm_aabb = pick_random_from_class(assets, fm_cls, rng)

        # Stage A
        try:
            forb_A = hard_forbidden + corridor_reserved
            (tx, ty), yaw, rect = sample_free_pose_any_yaw(
                room_w, room_l, fm_aabb, forb_A, rng, yaw_range=(0.0, 360.0), max_tries=12000
            )
        except Exception:
            # Stage B (fallback)
            forb_B = hard_forbidden  # allow corridor_reserved
            (tx, ty), yaw, rect = sample_free_pose_any_yaw(
                room_w, room_l, fm_aabb, forb_B, rng, yaw_range=(0.0, 360.0), max_tries=18000
            )

        z = place_on_floor_z(fm_aabb, spawn_extra=0.0)
        name = f"{fm_cls}_{k:02d}_{fm_aabb.asset}"
        placed.append(Placed(
            name=name,
            cls=fm_cls,
            usd_path=fm_aabb.dst_usd,
            prim_path=f"/World/Assets/{name}",
            pos=(tx, ty, z),
            rot_deg=(0.0, 0.0, yaw),
            rigid_body=False,
            rect=rect,
            height_m=fm_aabb.height_m,
            is_step_over=is_corridor_passable(fm_aabb, difficulty, step_over_classes),
        ))
        commit_object_rect(rect)

    # 6) Forced step-over items on corridor (d1: random on wide corridor, d3: spanning narrow corridor)
    #
    # Difficulty semantics (subset / cumulative):
    #   d0 — front walk, clear corridor
    #   d1 — front walk + step-over on wide corridor  (includes d0-type walking)
    #   d2 — front walk + step-over in room + side pass through narrow corridor
    #         (includes d0 + d1 challenges, step-over and sidestep are SEPARATE)
    #   d3 — side pass + step-over SIMULTANEOUSLY on narrow corridor
    #         (object spans corridor width so robot MUST step over while sidestepping)
    forced_step_items = 0
    if allow_step_on_corridor:
        step_classes = [c for c in step_over_classes if c in assets and assets[c]]
        if step_classes:
            forced_step_items = rng.randint(1, forced_step_items_max)
            for kk in range(forced_step_items):
                if difficulty == 3:
                    # d3: pick wide object and span the narrow corridor
                    aabb = pick_spanning_corridor_asset(assets, step_classes, difficulty, corridor_w, rng)
                    if aabb is None:
                        continue
                    (tx, ty), yaw, rect = sample_spanning_pose_in_corridor(
                        room_w, room_l, aabb, hard_forbidden, corridor_rects, rng)
                else:
                    # d1: random placement on wide corridor
                    aabb = pick_corridor_asset(assets, step_classes, difficulty, rng)
                    if aabb is None:
                        continue
                    (tx, ty), yaw, rect = sample_pose_in_corridor(
                        room_w, room_l, aabb, hard_forbidden, corridor_rects, rng)

                z = place_on_floor_z(aabb, spawn_extra=0.0)
                name = f"{aabb.cls}_forcedcorr_{kk:02d}_{aabb.asset}"
                prim = f"/World/Assets/{name}"
                placed.append(Placed(
                    name=name,
                    cls=aabb.cls,
                    usd_path=aabb.dst_usd,
                    prim_path=prim,
                    pos=(tx, ty, z),
                    rot_deg=(0.0, 0.0, yaw),
                    rigid_body=False,
                    rect=rect,
                    height_m=aabb.height_m,
                    is_step_over=True,
                ))
                commit_object_rect(rect)
                # Shoe pair
                if aabb.cls in pair_classes:
                    pair = make_shoe_pair_placed(aabb, name, prim, tx, ty, z, yaw, rect, True, room_w, room_l, hard_forbidden, rng)
                    if pair:
                        placed.append(pair)
                        commit_object_rect(pair.rect)

    # 6b) d2/d3 combo: forced step-over OUTSIDE corridor (subset inclusion of d1)
    #     Placed in the room (not on narrow corridor), so step-over and sidestep
    #     are separate sequential challenges.
    #     Skipped in atom mode (atom = single difficulty action only).
    if force_step_off_corridor:
        step_classes = [c for c in step_over_classes if c in assets and assets[c]]
        if step_classes:
            n_forced_room = rng.randint(1, max(1, forced_step_items_max))
            for kk in range(n_forced_room):
                aabb = pick_corridor_asset(assets, step_classes, 1, rng)  # d1 height budget
                if aabb is None:
                    continue
                forb = hard_forbidden + corridor_reserved  # stay off corridor
                try:
                    (tx, ty), yaw, rect = sample_free_pose_any_yaw(
                        room_w, room_l, aabb, forb, rng, yaw_range=(0.0, 360.0), max_tries=5000)
                except RuntimeError:
                    continue
                z = place_on_floor_z(aabb, spawn_extra=0.0)
                name = f"{aabb.cls}_roomstep_{kk:02d}_{aabb.asset}"
                prim = f"/World/Assets/{name}"
                placed.append(Placed(
                    name=name,
                    cls=aabb.cls,
                    usd_path=aabb.dst_usd,
                    prim_path=prim,
                    pos=(tx, ty, z),
                    rot_deg=(0.0, 0.0, yaw),
                    rigid_body=False,
                    rect=rect,
                    height_m=aabb.height_m,
                    is_step_over=True,
                ))
                commit_object_rect(rect)
                if aabb.cls in pair_classes:
                    pair = make_shoe_pair_placed(aabb, name, prim, tx, ty, z, yaw, rect, True, room_w, room_l, hard_forbidden, rng)
                    if pair:
                        placed.append(pair)
                        commit_object_rect(pair.rect)

    # 7) Cluster clutter near bed
    cluster_classes_base = config.cluster_classes if config else ["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"]
    cluster_classes = [c for c in cluster_classes_base if c in assets and assets[c]]

    bx0, by0, bx1, by1 = anchor_rect
    cluster_cx = (bx0 + bx1) * 0.5
    cluster_cy = (by0 + by1) * 0.5

    def jitter_around_point(cx: float, cy: float, radius: float) -> Tuple[float, float]:
        t = 2.0 * math.pi * rng.random()
        r = radius * math.sqrt(rng.random())
        return cx + r * math.cos(t), cy + r * math.sin(t)

    def place_biased_any_yaw(aabb: AssetAABB, center: Tuple[float, float], spread: float) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float]]:
        passable = is_corridor_passable(aabb, difficulty, step_over_classes)
        if passable:
            forb = hard_forbidden + (corridor_reserved if forbid_step_on_corridor else [])
        else:
            forb = hard_forbidden + corridor_reserved

        for _ in range(2500):
            yaw = rng.uniform(0.0, 360.0)
            local_rect = aabb2d_after_yaw(aabb, yaw)
            lx0, ly0, lx1, ly1 = local_rect
            lcx = (lx0 + lx1) * 0.5
            lcy = (ly0 + ly1) * 0.5
            cx, cy = jitter_around_point(center[0], center[1], spread)
            tx = cx - lcx
            ty = cy - lcy
            world_rect = rect_translate(local_rect, tx, ty)

            if not within_room(world_rect, room_w, room_l, WALL_MARGIN):
                continue
            if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in forb):
                continue
            return (tx, ty), float(yaw), world_rect

        return sample_free_pose_any_yaw(room_w, room_l, aabb, forb, rng, yaw_range=(0.0, 360.0), max_tries=12000)

    for k in range(cluster_item_n):
        if not cluster_classes:
            break
        cls = rng.choice(cluster_classes)
        aabb = pick_random_from_class(assets, cls, rng)
        (tx, ty), yaw, rect = place_biased_any_yaw(aabb, (cluster_cx, cluster_cy), spread=1.1)
        z = place_on_floor_z(aabb, spawn_extra=0.0)
        name = f"{cls}_cluster_{k:02d}_{aabb.asset}"
        prim = f"/World/Assets/{name}"
        step_over = is_corridor_passable(aabb, difficulty, step_over_classes)
        placed.append(Placed(
            name=name,
            cls=cls,
            usd_path=aabb.dst_usd,
            prim_path=prim,
            pos=(tx, ty, z),
            rot_deg=(0.0, 0.0, yaw),
            rigid_body=False,
            rect=rect,
            height_m=aabb.height_m,
            is_step_over=step_over,
        ))
        commit_object_rect(rect)
        # Shoe pair
        if cls in pair_classes:
            pair = make_shoe_pair_placed(aabb, name, prim, tx, ty, z, yaw, rect, step_over, room_w, room_l, hard_forbidden, rng)
            if pair:
                placed.append(pair)
                commit_object_rect(pair.rect)

    # 8) Scattered small items
    scatter_classes_base = config.scatter_classes if config else ["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"]
    scatter_classes = [c for c in scatter_classes_base if c in assets and assets[c]]

    for k in range(small_item_n):
        if not scatter_classes:
            break
        cls = rng.choice(scatter_classes)
        aabb = pick_random_from_class(assets, cls, rng)
        passable = is_corridor_passable(aabb, difficulty, step_over_classes)

        if passable:
            forb = hard_forbidden + (corridor_reserved if forbid_step_on_corridor else [])
        else:
            forb = hard_forbidden + corridor_reserved

        (tx, ty), yaw, rect = sample_free_pose_any_yaw(
            room_w, room_l, aabb, forb, rng, yaw_range=(0.0, 360.0), max_tries=9000
        )
        z = place_on_floor_z(aabb, spawn_extra=0.0)
        name = f"{cls}_{k:02d}_{aabb.asset}"
        placed.append(Placed(
            name=name,
            cls=cls,
            usd_path=aabb.dst_usd,
            prim_path=f"/World/Assets/{name}",
            pos=(tx, ty, z),
            rot_deg=(0.0, 0.0, yaw),
            rigid_body=False,
            rect=rect,
            height_m=aabb.height_m,
            is_step_over=passable,
        ))
        commit_object_rect(rect)

        if cls in pair_classes:
            pair = make_shoe_pair_placed(aabb, name, f"/World/Assets/{name}", tx, ty, z, yaw, rect, passable, room_w, room_l, hard_forbidden, rng)
            if pair:
                placed.append(pair)
                commit_object_rect(pair.rect)

    # 9) Markers (colored cone primitives at zone centers)
    born_marker = make_marker_placed("born_origin", "/World/Markers/origin", origin_zone, marker_kind="origin")
    placed.append(born_marker)

    dest_marker = make_marker_placed("destination", "/World/Markers/destination", dest_zone, marker_kind="dest")
    placed.append(dest_marker)

    # 10) Reachability check using ONLY blocking obstacles (not step-over)
    reach_mode = "unchecked"
    if require_reachable:
        obstacle_rects = [p.rect for p in placed if (not p.is_step_over)]
        ok, reach_mode = reachability_with_side_step(room_w, room_l, obstacle_rects, origin_zone, dest_zone, grid_res=GRID_RES)
        if not ok:
            raise RuntimeError("Reachability failed (origin->destination blocked)")

        # enforce difficulty reach mode
        if difficulty in (0, 1) and reach_mode != "main":
            raise RuntimeError("Difficulty wants FRONT pass, but main is not reachable.")
        if difficulty in (2, 3) and reach_mode != "side":
            raise RuntimeError("Difficulty wants SIDE-only pass, but side is not reachable.")

    # 11) Corridor step-over evidence (exclude markers)
    step_on_corridor = 0
    for p in placed:
        if p.is_step_over and rect_intersects_any(p.rect, corridor_rects, margin=0.0):
            if p.name not in ("born_origin", "destination"):
                step_on_corridor += 1

    if allow_step_on_corridor and step_on_corridor <= 0:
        raise RuntimeError("Difficulty requires step-over on corridor, but none found.")
    if forbid_step_on_corridor and step_on_corridor > 0:
        raise RuntimeError("Difficulty requires NO step-over on corridor, but found some.")

    # 12) Pick textures (if catalog provided)
    if texture_catalog is not None:
        floor_tex = rng.choice(texture_catalog["floor"])
        wall_tex = rng.choice(texture_catalog["wall"])
        floor_material = {
            "texture_id": floor_tex["id"],
            "color": floor_tex["color"],
            "normal": floor_tex["normal"],
            "roughness": floor_tex["roughness"],
            "tiles_per_meter": floor_tex["tiles_per_meter"],
        }
        wall_material = {
            "texture_id": wall_tex["id"],
            "color": wall_tex["color"],
            "normal": wall_tex["normal"],
            "roughness": wall_tex["roughness"],
            "tiles_per_meter": wall_tex["tiles_per_meter"],
        }
    else:
        floor_material = None
        wall_material = None

    # 13) Build JSON
    scene = {
        "scene_name": f"{room_name}_{tier}_seed{stable_int_hash(str(rng.random()))}",
        "room": {
            "size_m": [room_w, room_l, room_h],
            "wall_thickness_m": WALL_THICKNESS,
            "floor_thickness_m": FLOOR_THICKNESS,
            "origin_m": [0.0, 0.0, 0.0],
            "floor_material": floor_material,
            "wall_material": wall_material,
        },
        "nav": {
            "origin_zone_xyxy_m": [round(v, 4) for v in origin_zone],
            "dest_zone_xyxy_m": [round(v, 4) for v in dest_zone],
            "corridor_width_m": round(corridor_w, 3),
            "corridor_rects_xyxy_m": [[round(v, 4) for v in r] for r in corridor_rects],
            "corridor_reserved_xyxy_m": [[round(v, 4) for v in r] for r in corridor_reserved],
            "reachability_mode": reach_mode,
            "grid_res_m": GRID_RES,

            "difficulty_level_0_to_3": int(difficulty),
            "corridor_obj_height_max_m": CORRIDOR_HEIGHT_BUDGET[difficulty]["max"],
            "corridor_obj_height_prefer_min_m": CORRIDOR_HEIGHT_BUDGET[difficulty]["prefer_min"],
            "front_pass_width_m": FRONT_PASS_WIDTH_M,
            "side_pass_width_m": SIDE_PASS_WIDTH_M,
            "humanoid_radius_main_m": HUMANOID_RADIUS_MAIN,
            "humanoid_radius_side_m": HUMANOID_RADIUS_SIDE,

            "forced_step_items_on_corridor": forced_step_items,
            "step_over_on_corridor_count": step_on_corridor,

            # annealing meta
            "anneal_fail_window": FAIL_WINDOW,
            "anneal_level": int(attempt_index // FAIL_WINDOW),
            "counts_used": {
                "chairs": int(chair_n),
                "small_items": int(small_item_n),
                "cluster_items": int(cluster_item_n),
                "forced_step_items_max": int(forced_step_items_max),
                "wall_big_max": int(wall_big_max),
            },

            "step_over_candidate_classes": sorted(list(step_over_classes)),
        },
        "objects": []
    }

    for p in placed:
        obj_dict = {
            "name": p.name,
            "class": p.cls,
            "usd_path": p.usd_path,
            "prim_path": p.prim_path,
            "pose_m": {
                "pos": [round(p.pos[0], 4), round(p.pos[1], 4), round(p.pos[2], 4)],
                "rot_deg": [round(p.rot_deg[0], 3), round(p.rot_deg[1], 3), round(p.rot_deg[2], 3)]
            },
            "rigid_body": bool(p.rigid_body),
            "aabb_height_m": round(float(p.height_m), 4),
            "is_step_over": bool(p.is_step_over),
        }
        # Marker objects carry colour info for the cone primitive
        if p.cls == "marker":
            kind = "origin" if "origin" in p.name else "dest"
            obj_dict["marker_color_rgba"] = MARKER_COLORS[kind]
            obj_dict["marker_radius_m"] = MARKER_RADIUS
            obj_dict["marker_height_m"] = MARKER_HEIGHT
        scene["objects"].append(obj_dict)

    scene["meta"] = {
        "tier": tier,
        "room_w_l_h": [room_w, room_l, room_h],
        "atom_mode": bool(atom),
        "counts": {
            "chairs": chair_n,
            "scatter_items": small_item_n,
            "cluster_items": cluster_item_n,
            "wall_big_classes": wall_big_classes,
        }
    }

    return scene


def generate_scene_with_retries(
    assets: Dict[str, Dict[str, AssetAABB]],
    base_seed: int,
    max_attempts: int = 2000,
    require_reachable: bool = True,
    forced_tier: Optional[str] = None,
    fixed_room_w: Optional[float] = None,
    fixed_room_l: Optional[float] = None,
    difficulty: Optional[int] = None,
    atom: bool = False,
    texture_catalog: Optional[Dict] = None,
    config: Optional["RoomConfig"] = None,
) -> Dict:
    last_err = None
    for k in range(max_attempts):
        rng = random.Random(base_seed + k * 1337)
        try:
            scene = build_one_scene(
                assets=assets,
                rng=rng,
                require_reachable=require_reachable,
                forced_tier=forced_tier,
                fixed_room_w=fixed_room_w,
                fixed_room_l=fixed_room_l,
                difficulty=difficulty,
                attempt_index=k,
                atom=atom,
                texture_catalog=texture_catalog,
                config=config,
            )
            scene.setdefault("meta", {})
            scene["meta"].update({
                "base_seed": base_seed,
                "attempt_index": k,
                "final_seed": base_seed + k * 1337,
            })
            return scene
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Failed to generate a valid scene after {max_attempts} attempts. Last error: {last_err}")


# -------------------- CLI --------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--registry", required=True,
                   help="Path to asset_registry.csv (original / high-fidelity).")
    p.add_argument("--registry_simplified", default=None,
                   help="Path to simplified asset_registry.csv.  When set, outputs dual JSONs "
                        "(*_original.json + *_simplified.json) per scene.")
    p.add_argument("--output", required=True,
                   help="Output path. Single scene: a .json file.  Batch (--count>1): a directory.")
    p.add_argument("--seed", type=int, default=GLOBAL_SEED)
    p.add_argument("--attempts", type=int, default=2000)
    p.add_argument("--no_reachability", action="store_true", help="Disable reachability check")
    p.add_argument("--tier", choices=["small", "medium", "large", "random"], default="random",
                   help="Force room size tier; default random.")
    p.add_argument("--room_w", type=float, default=None, help="Force room width (meters), e.g. 3.0")
    p.add_argument("--room_l", type=float, default=None, help="Force room length (meters), e.g. 5.0")
    p.add_argument("--difficulty", type=int, default=None, choices=[0, 1, 2, 3],
                   help="Force difficulty level 0..3. If not set, random.")
    p.add_argument("--count", type=int, default=1,
                   help="Number of scenes to generate (default 1). "
                        "When >1, --output is treated as a directory.")
    p.add_argument("--atom_count", type=int, default=0,
                   help="Number of atom-action scenes per difficulty (d2, d3 only). "
                        "Atom scenes contain ONLY the single difficulty action + basic walking. "
                        "Output to bedroom_d{N}_atom/ with _atom suffix in filenames.")
    p.add_argument("--textures", type=str, default=None,
                   help="Path to texture_catalog.json.  When set, each scene gets "
                        "random floor_material and wall_material entries in the room dict.")
    p.add_argument("--room_type", type=str, default="livingroom",
                   help="Room type config to use (bedroom, livingroom, etc.). Default: livingroom.")
    return p.parse_args()


def _print_scene_summary(scene: Dict, path: str):
    print(f"  {path}")
    d = scene["nav"]["difficulty_level_0_to_3"]
    n = len(scene["objects"])
    mode = scene["nav"]["reachability_mode"]
    ann = scene["nav"]["anneal_level"]
    print(f"    difficulty={d}  objects={n}  reach={mode}  anneal_level={ann}")


def _write_scene_json(scene, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scene, f, indent=2)


def _write_dual_or_single(scene, base_path, usd_path_map):
    """Write one or two JSONs depending on whether dual output is enabled.
    Returns list of paths written."""
    written = []
    if usd_path_map:
        p = Path(base_path)
        stem = p.stem
        parent = p.parent
        path_orig = parent / f"{stem}_original.json"
        path_simp = parent / f"{stem}_simplified.json"

        _write_scene_json(scene, path_orig)
        written.append(str(path_orig))

        scene_simp = _remap_scene_usd_paths(scene, usd_path_map)
        _write_scene_json(scene_simp, path_simp)
        written.append(str(path_simp))
    else:
        _write_scene_json(scene, base_path)
        written.append(str(base_path))
    return written


def main():
    args = parse_args()
    assets = load_registry(args.registry)
    forced_tier = None if args.tier == "random" else args.tier

    # Room config
    config = get_config(args.room_type)
    room_name = config.name
    print(f"Room type: {room_name}")

    # Texture catalog (optional)
    texture_catalog = None
    if args.textures:
        texture_catalog = load_texture_catalog(args.textures)
        print(f"Texture catalog loaded: {len(texture_catalog['floor'])} floor, "
              f"{len(texture_catalog['wall'])} wall textures")

    # Dual output: original + simplified
    usd_path_map = {}
    if args.registry_simplified:
        assets_simp = load_registry(args.registry_simplified)
        usd_path_map = _build_usd_path_map(assets, assets_simp)
        print(f"Dual output enabled: {len(usd_path_map)} asset path mappings loaded")

    count = max(1, args.count)

    if count == 1:
        # --- Single scene mode ---
        scene = generate_scene_with_retries(
            assets=assets,
            base_seed=args.seed,
            max_attempts=args.attempts,
            require_reachable=(not args.no_reachability),
            forced_tier=forced_tier,
            fixed_room_w=args.room_w,
            fixed_room_l=args.room_l,
            difficulty=args.difficulty,
            texture_catalog=texture_catalog,
            config=config,
        )

        out_path = Path(args.output)
        written = _write_dual_or_single(scene, str(out_path), usd_path_map)
        for w in written:
            _print_scene_summary(scene, w)
    else:
        # --- Batch mode ---
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)

        difficulties = [args.difficulty] if args.difficulty is not None else [0, 1, 2, 3]
        atom_count = max(0, args.atom_count)
        # Atom scenes only apply to d2 and d3
        atom_difficulties = [d for d in difficulties if d in (2, 3)]
        total_combo = count * len(difficulties)
        total_atom = atom_count * len(atom_difficulties)
        total = total_combo + total_atom
        generated = 0
        failed = 0

        mode = "dual (original + simplified)" if usd_path_map else "single"
        print(f"Batch: {count} combo x {len(difficulties)} diff + "
              f"{atom_count} atom x {len(atom_difficulties)} diff = {total} scenes  [{mode}]")
        print(f"Output directory: {out_dir}\n")

        # --- Combo scenes (full difficulty subset) ---
        for diff in difficulties:
            diff_dir = out_dir / f"{room_name}_d{diff}"
            diff_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(count):
                seed = args.seed + diff * 100000 + idx * 997
                base_name = f"{room_name}_d{diff}_{idx:03d}"
                base_path = diff_dir / f"{base_name}.json"
                try:
                    scene = generate_scene_with_retries(
                        assets=assets,
                        base_seed=seed,
                        max_attempts=args.attempts,
                        require_reachable=(not args.no_reachability),
                        forced_tier=forced_tier,
                        fixed_room_w=args.room_w,
                        fixed_room_l=args.room_l,
                        difficulty=diff,
                        atom=False,
                        texture_catalog=texture_catalog,
                        config=config,
                    )
                    written = _write_dual_or_single(scene, str(base_path), usd_path_map)
                    generated += 1
                    _print_scene_summary(scene, written[0])
                except Exception as e:
                    failed += 1
                    print(f"  [FAIL] {base_name}: {e}")

        # --- Atom scenes (single difficulty action only, d2 and d3) ---
        if atom_count > 0:
            print(f"\n--- Atom scenes (d2, d3 only) ---")
            for diff in atom_difficulties:
                atom_dir = out_dir / f"{room_name}_d{diff}_atom"
                atom_dir.mkdir(parents=True, exist_ok=True)
                for idx in range(atom_count):
                    seed = args.seed + diff * 100000 + 50000 + idx * 997
                    base_name = f"{room_name}_d{diff}_{idx:03d}_atom"
                    base_path = atom_dir / f"{base_name}.json"
                    try:
                        scene = generate_scene_with_retries(
                            assets=assets,
                            base_seed=seed,
                            max_attempts=args.attempts,
                            require_reachable=(not args.no_reachability),
                            forced_tier=forced_tier,
                            fixed_room_w=args.room_w,
                            fixed_room_l=args.room_l,
                            difficulty=diff,
                            atom=True,
                            texture_catalog=texture_catalog,
                            config=config,
                        )
                        written = _write_dual_or_single(scene, str(base_path), usd_path_map)
                        generated += 1
                        _print_scene_summary(scene, written[0])
                    except Exception as e:
                        failed += 1
                        print(f"  [FAIL] {base_name}: {e}")

        print(f"\nBatch done: {generated}/{total} generated, {failed} failed.")


if __name__ == "__main__":
    main()
