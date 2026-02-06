# scene_json_generator.py
# Generate a crowded bedroom scene JSON using asset_registry.csv (AABB + dst_usd).
#
# Update in this version:
# - Corridor is still reserved for BIG objects (bed/wardrobe/suitcase/desk/cabinet/chair, etc.)
# - BUT small "step-over" clutter (blanket, shoes) is allowed to be placed inside corridor
# - They still MUST NOT overlap with anything else
# - Reachability check treats "step-over" clutter as NON-blocking (ignored as obstacles)
#
# Run:
#   python scene_json_generator.py --registry "C:\...\asset_registry.csv" --output "C:\...\bedroom01.json"
# Options:
#   --seed 123
#   --attempts 200
#   --tier small|medium|large|random
#   --no_reachability
#   --require_side_only

import argparse
import csv
import json
import math
import random
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# -------------------- Defaults --------------------
GLOBAL_SEED = 20260130

WALL_THICKNESS = 0.10
FLOOR_THICKNESS = 0.05
ROOM_HEIGHT = 2.6

# Placement margins
WALL_MARGIN = 0.06
OBJ_MARGIN = 0.05
ZONE_MARGIN = 0.10

# Humanoid standing zone (rect)
HUMANOID_ZONE_XY = (1.0, 1.0)  # meters

# Clearance model
HUMANOID_RADIUS_MAIN = 0.35
SIDEWAYS_MIN_WIDTH_M = 0.60
HUMANOID_RADIUS_SIDE = SIDEWAYS_MIN_WIDTH_M / 2.0  # 0.30m

GRID_RES = 0.10  # reachability grid resolution (meters)

# Room tiers
ROOM_TIERS = {
    "small":  {"w": (3.0, 3.8), "l": (3.6, 4.6)},
    "medium": {"w": (3.8, 4.8), "l": (4.2, 5.8)},
    "large":  {"w": (4.8, 6.8), "l": (5.5, 7.5)},
}
ROOM_TIER_PROBS = [0.35, 0.45, 0.20]  # small/medium/large

# Crowdedness
TIER_COUNTS = {
    "large":  {"chairs": (0, 1), "small_items": (10, 16), "cluster_items": (5, 8)},
    "small": {"chairs": (1, 2), "small_items": (18, 30), "cluster_items": (8, 12)},
    "medium":  {"chairs": (2, 3), "small_items": (28, 44), "cluster_items": (10, 16)},
}

# Corridor width: must be >0.6 and <1.2
CORRIDOR_WIDTH_RANGE_BY_TIER = {
    "small":  (0.62, 0.85),
    "medium": (0.62, 0.95),
    "large":  (0.62, 1.10),
}
CORRIDOR_MARGIN = 0.05  # inflate corridor "reserved" rect a bit (for big objects)

BIG_YAW_CHOICES = [0.0, 90.0, 180.0, 270.0]

# Wall-hugging big items: each selected CLASS appears at most ONE instance
WALL_BIG_CLASS_PROBS = {
    "small":  {"wardrobe": 0.35, "suitcase": 0.45, "desk": 0.30, "cabinet": 0.25},
    "medium": {"wardrobe": 0.55, "suitcase": 0.55, "desk": 0.55, "cabinet": 0.45},
    "large":  {"wardrobe": 0.70, "suitcase": 0.65, "desk": 0.75, "cabinet": 0.65},
}
WALL_BIG_MAX_BY_TIER = {"small": 2, "medium": 3, "large": 4}

# Step-over clutter rules:
# - allowed to be placed inside corridor
# - ignored in reachability obstacles (robot can step over)
PASSABLE_ON_CORRIDOR_CLASSES = {"blanket", "shoes", "pillow"}


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


# -------------------- Registry Loading --------------------
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
def choose_room_tier(rng: random.Random) -> str:
    tiers = ["small", "medium", "large"]
    x = rng.random()
    cum = 0.0
    for t, p in zip(tiers, ROOM_TIER_PROBS):
        cum += p
        if x <= cum:
            return t
    return tiers[-1]


def sample_room_size(tier: str, rng: random.Random) -> Tuple[float, float]:
    r = ROOM_TIERS[tier]
    w = rng.uniform(r["w"][0], r["w"][1])
    l = rng.uniform(r["l"][0], r["l"][1])
    return round(w, 3), round(l, 3)


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
) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float], str]:
    walls = ["LEFT", "RIGHT", "BACK", "FRONT"]
    for _ in range(max_tries):
        wall = rng.choice(walls)
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
    max_tries: int = 4200,
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
    x0 = WALL_MARGIN
    y0 = WALL_MARGIN
    x1 = room_w - WALL_MARGIN
    y1 = room_l - WALL_MARGIN
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
) -> List[str]:
    probs = WALL_BIG_CLASS_PROBS.get(tier, {})
    candidates = [c for c in probs.keys() if c in assets and assets[c]]
    selected = []
    for c in candidates:
        if rng.random() < probs[c]:
            selected.append(c)
    rng.shuffle(selected)
    cap = WALL_BIG_MAX_BY_TIER.get(tier, 3)
    return selected[:cap]


# -------------------- Markers (distinct) --------------------
def pick_preferred_marker_asset(
    assets: Dict[str, Dict[str, AssetAABB]],
    preferred_classes: List[str],
    rng: random.Random,
) -> AssetAABB:
    for cls in preferred_classes:
        if cls in assets and assets[cls]:
            return pick_random_from_class(assets, cls, rng)
    for cls in ["bottle", "laptop", "book", "pillow", "box", "shoes", "blanket"]:
        if cls in assets and assets[cls]:
            return pick_random_from_class(assets, cls, rng)
    raise RuntimeError("No suitable marker asset found in registry.")


def place_marker_in_zone(
    assets: Dict[str, Dict[str, AssetAABB]],
    name: str,
    prim_path: str,
    zone_rect: Tuple[float, float, float, float],
    rng: random.Random,
    marker_kind: str,  # "origin" | "dest"
) -> Placed:
    if marker_kind == "origin":
        aabb = pick_preferred_marker_asset(assets, ["bottle", "suitcase"], rng)
        marker_color = [0.0, 1.0, 0.0, 1.0]  # green
    else:
        aabb = pick_preferred_marker_asset(assets, ["laptop", "book", "pillow"], rng)
        marker_color = [1.0, 0.0, 0.0, 1.0]  # red

    yaw = 0.0
    local_rect = aabb2d_after_yaw(aabb, yaw)
    lx0, ly0, lx1, ly1 = local_rect
    lcx = (lx0 + lx1) * 0.5
    lcy = (ly0 + ly1) * 0.5

    zx0, zy0, zx1, zy1 = zone_rect
    zcx = (zx0 + zx1) * 0.5
    zcy = (zy0 + zy1) * 0.5

    tx = zcx - lcx
    ty = zcy - lcy
    world_rect = rect_translate(local_rect, tx, ty)

    z = place_on_floor_z(aabb, spawn_extra=0.02)

    p = Placed(
        name=name,
        cls=aabb.cls,
        usd_path=aabb.dst_usd,
        prim_path=prim_path,
        pos=(tx, ty, z),
        rot_deg=(0.0, 0.0, yaw),
        rigid_body=False,
        rect=world_rect,
    )
    p._marker_color = marker_color  # type: ignore[attr-defined]
    return p


# -------------------- Utility: Corridor checks --------------------
def rect_intersects_any(rect: Tuple[float, float, float, float], rects: List[Tuple[float, float, float, float]], margin: float = 0.0) -> bool:
    return any(rects_overlap(rect, r, margin=margin) for r in rects)


# -------------------- Scene Builder --------------------
def rng_seed_hint(rng: random.Random) -> int:
    return stable_int_hash(str(rng.random()))


def build_one_scene(
    assets: Dict[str, Dict[str, AssetAABB]],
    rng: random.Random,
    require_reachable: bool = True,
    require_side_only: bool = False,
    forced_tier: Optional[str] = None,
) -> Dict:
    # Tier
    if forced_tier is None or forced_tier == "random":
        tier = choose_room_tier(rng)
    else:
        tier = forced_tier

    room_w, room_l = sample_room_size(tier, rng)
    room_h = ROOM_HEIGHT

    counts = TIER_COUNTS[tier]
    chair_n = rng.randint(counts["chairs"][0], counts["chairs"][1])
    small_item_n = rng.randint(counts["small_items"][0], counts["small_items"][1])
    cluster_item_n = rng.randint(counts["cluster_items"][0], counts["cluster_items"][1])

    placed: List[Placed] = []

    # We maintain TWO forbidden sets:
    # - hard_forbidden: must avoid for ALL objects (zones + existing objects)
    # - corridor_reserved: avoid for BIG objects; SMALL passables may enter
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

    # 2) Corridor reservation
    w_lo, w_hi = CORRIDOR_WIDTH_RANGE_BY_TIER[tier]
    corridor_w = rng.uniform(w_lo, w_hi)

    opt1, opt2 = make_L_corridor_rects(origin_zone, dest_zone, corridor_w)
    if corridor_fits_room(opt1, room_w, room_l, WALL_MARGIN):
        corridor_rects = opt1
    elif corridor_fits_room(opt2, room_w, room_l, WALL_MARGIN):
        corridor_rects = opt2
    else:
        corridor_rects = opt1

    corridor_reserved = [inflate_rect(cr, CORRIDOR_MARGIN) for cr in corridor_rects]

    # Helper to decide which forbidden set to use
    def forbidden_for_class(cls: str) -> List[Tuple[float, float, float, float]]:
        # Everyone must avoid origin/dest zones and existing objects (hard_forbidden).
        # BIG objects additionally must avoid corridor_reserved.
        if cls in PASSABLE_ON_CORRIDOR_CLASSES:
            return hard_forbidden
        return hard_forbidden + corridor_reserved

    # Helper: add object rect into hard_forbidden after placing (so nobody overlaps).
    def commit_object_rect(rect: Tuple[float, float, float, float]):
        hard_forbidden.append(rect)

    # 3) Place bed (bed01-bed19) against wall (bed is BIG => must avoid corridor)
    bed_candidates = [f"bed{idx:02d}" for idx in range(1, 20)]
    bed_aabb = pick_existing_asset(assets, "bed", bed_candidates, rng)
    (tx, ty), yaw, bed_rect, _ = place_against_wall(room_w, room_l, bed_aabb, forbidden_for_class("bed"), rng, yaw_choices=BIG_YAW_CHOICES)
    bed_z = place_on_floor_z(bed_aabb, spawn_extra=0.0)
    bed_name = bed_aabb.asset
    placed.append(Placed(
        name=bed_name,
        cls="bed",
        usd_path=bed_aabb.dst_usd,
        prim_path=f"/World/Assets/{bed_name}",
        pos=(tx, ty, bed_z),
        rot_deg=(0.0, 0.0, yaw),
        rigid_body=False,
        rect=bed_rect
    ))
    commit_object_rect(bed_rect)

    # 4) Wall-hugging big items (each chosen class at most ONE instance)
    wall_big_classes = pick_wall_big_classes(tier, assets, rng)
    for cls in wall_big_classes:
        big_aabb = pick_random_from_class(assets, cls, rng)
        (tx, ty), yaw, rect, _ = place_against_wall(room_w, room_l, big_aabb, forbidden_for_class(cls), rng, yaw_choices=BIG_YAW_CHOICES)
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
            rect=rect
        ))
        commit_object_rect(rect)

    # 5) Chairs (treated as blocking => avoid corridor)
    if "chair" in assets and assets["chair"]:
        for k in range(chair_n):
            chair_aabb = pick_random_from_class(assets, "chair", rng)
            (tx, ty), yaw, rect = sample_free_pose_any_yaw(room_w, room_l, chair_aabb, forbidden_for_class("chair"), rng, yaw_range=(0.0, 360.0))
            z = place_on_floor_z(chair_aabb, spawn_extra=0.0)
            name = f"chair_{k:02d}_{chair_aabb.asset}"
            placed.append(Placed(
                name=name,
                cls="chair",
                usd_path=chair_aabb.dst_usd,
                prim_path=f"/World/Assets/{name}",
                pos=(tx, ty, z),
                rot_deg=(0.0, 0.0, yaw),
                rigid_body=False,
                rect=rect
            ))
            commit_object_rect(rect)

    # 6) Cluster clutter near bed (some can be passable)
    cluster_classes = ["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"]
    cluster_classes = [c for c in cluster_classes if c in assets and assets[c]]

    # pick a cluster center near bed (just use bed center)
    bx0, by0, bx1, by1 = bed_rect
    cluster_cx = (bx0 + bx1) * 0.5
    cluster_cy = (by0 + by1) * 0.5

    def jitter_around_point(cx: float, cy: float, radius: float) -> Tuple[float, float]:
        t = 2.0 * math.pi * rng.random()
        r = radius * math.sqrt(rng.random())
        return cx + r * math.cos(t), cy + r * math.sin(t)

    def place_biased_any_yaw(aabb: AssetAABB, cls: str, center: Tuple[float, float], spread: float) -> Tuple[Tuple[float, float], float, Tuple[float, float, float, float]]:
        # biased placement: try a bunch around center, then fall back
        for _ in range(1800):
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
            # overlap check: always use hard_forbidden (existing objects/zones), plus corridor if needed
            forb = forbidden_for_class(cls)
            if any(rects_overlap(world_rect, r, margin=OBJ_MARGIN) for r in forb):
                continue
            return (tx, ty), float(yaw), world_rect

        # fallback to global free
        return sample_free_pose_any_yaw(room_w, room_l, aabb, forbidden_for_class(cls), rng, yaw_range=(0.0, 360.0))

    for k in range(cluster_item_n):
        if not cluster_classes:
            break
        cls = rng.choice(cluster_classes)
        aabb = pick_random_from_class(assets, cls, rng)
        (tx, ty), yaw, rect = place_biased_any_yaw(aabb, cls, (cluster_cx, cluster_cy), spread=1.1)
        z = place_on_floor_z(aabb, spawn_extra=0.0)
        name = f"{cls}_cluster_{k:02d}_{aabb.asset}"
        placed.append(Placed(
            name=name,
            cls=cls,
            usd_path=aabb.dst_usd,
            prim_path=f"/World/Assets/{name}",
            pos=(tx, ty, z),
            rot_deg=(0.0, 0.0, yaw),
            rigid_body=False,
            rect=rect
        ))
        commit_object_rect(rect)

    # 7) Scattered small items (random yaw). blanket/shoes may land on corridor.
    scatter_classes = ["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"]
    scatter_classes = [c for c in scatter_classes if c in assets and assets[c]]

    for k in range(small_item_n):
        if not scatter_classes:
            break
        cls = rng.choice(scatter_classes)
        aabb = pick_random_from_class(assets, cls, rng)

        (tx, ty), yaw, rect = sample_free_pose_any_yaw(
            room_w, room_l, aabb,
            forbidden_for_class(cls),
            rng,
            yaw_range=(0.0, 360.0)
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
            rect=rect
        ))
        commit_object_rect(rect)

    # 8) Markers (distinct + recorded for Unity)
    born_marker = place_marker_in_zone(assets, "born_origin", "/World/Assets/born_origin", origin_zone, rng, marker_kind="origin")
    # NOTE: marker should not overlap, so we enforce it using hard_forbidden only (zones+objects)
    if any(rects_overlap(born_marker.rect, r, margin=OBJ_MARGIN) for r in hard_forbidden):
        # if unlucky, just don't place it physically; still record zone
        pass
    else:
        placed.append(born_marker)
        commit_object_rect(born_marker.rect)

    dest_marker = place_marker_in_zone(assets, "destination", "/World/Assets/destination", dest_zone, rng, marker_kind="dest")
    if any(rects_overlap(dest_marker.rect, r, margin=OBJ_MARGIN) for r in hard_forbidden):
        pass
    else:
        placed.append(dest_marker)
        commit_object_rect(dest_marker.rect)

    # 9) Reachability check:
    # Exclude passable-on-corridor clutter from obstacles.
    reach_mode = "unchecked"
    if require_reachable:
        obstacle_rects = [p.rect for p in placed if p.cls not in PASSABLE_ON_CORRIDOR_CLASSES]
        ok, reach_mode = reachability_with_side_step(room_w, room_l, obstacle_rects, origin_zone, dest_zone, grid_res=GRID_RES)
        if not ok:
            raise RuntimeError("Reachability failed (origin->destination blocked)")
        if require_side_only and reach_mode != "side":
            raise RuntimeError("Reachability did not require side-step (want side-only)")

    # 10) Build JSON
    scene = {
        "scene_name": f"bedroom_{tier}_seed{stable_int_hash(str(rng.random()))}",
        "room": {
            "size_m": [room_w, room_l, room_h],
            "wall_thickness_m": WALL_THICKNESS,
            "floor_thickness_m": FLOOR_THICKNESS,
            "origin_m": [0.0, 0.0, 0.0],
        },
        "nav": {
            "origin_zone_xyxy_m": [round(v, 4) for v in origin_zone],
            "dest_zone_xyxy_m": [round(v, 4) for v in dest_zone],
            "corridor_width_m": round(corridor_w, 3),
            "corridor_rects_xyxy_m": [[round(v, 4) for v in r] for r in corridor_rects],
            "corridor_reserved_xyxy_m": [[round(v, 4) for v in r] for r in corridor_reserved],
            "reachability_mode": reach_mode,
            "humanoid_radius_main_m": HUMANOID_RADIUS_MAIN,
            "humanoid_radius_side_m": HUMANOID_RADIUS_SIDE,
            "sideways_min_width_m": SIDEWAYS_MIN_WIDTH_M,
            "grid_res_m": GRID_RES,
            "passable_on_corridor_classes": sorted(list(PASSABLE_ON_CORRIDOR_CLASSES)),
            "markers": {
                "born_origin_prim": "/World/Assets/born_origin",
                "destination_prim": "/World/Assets/destination",
                "born_origin_color_rgba": getattr(born_marker, "_marker_color", [0, 1, 0, 1]),
                "destination_color_rgba": getattr(dest_marker, "_marker_color", [1, 0, 0, 1]),
            },
        },
        "objects": []
    }

    for p in placed:
        scene["objects"].append({
            "name": p.name,
            "class": p.cls,
            "usd_path": p.usd_path,
            "prim_path": p.prim_path,
            "pose_m": {
                "pos": [round(p.pos[0], 4), round(p.pos[1], 4), round(p.pos[2], 4)],
                "rot_deg": [round(p.rot_deg[0], 3), round(p.rot_deg[1], 3), round(p.rot_deg[2], 3)]
            },
            "rigid_body": bool(p.rigid_body)
        })

    scene["meta"] = {
        "tier": tier,
        "room_w_l_h": [room_w, room_l, room_h],
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
    max_attempts: int = 200,
    require_reachable: bool = True,
    require_side_only: bool = False,
    forced_tier: Optional[str] = None,
) -> Dict:
    last_err = None
    for k in range(max_attempts):
        rng = random.Random(base_seed + k * 1337)
        try:
            scene = build_one_scene(
                assets=assets,
                rng=rng,
                require_reachable=require_reachable,
                require_side_only=require_side_only,
                forced_tier=forced_tier,
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
    p.add_argument("--registry", required=True, help="Path to asset_registry.csv")
    p.add_argument("--output", required=True, help="Output scene JSON path")
    p.add_argument("--seed", type=int, default=GLOBAL_SEED)
    p.add_argument("--attempts", type=int, default=200)
    p.add_argument("--no_reachability", action="store_true", help="Disable reachability check")
    p.add_argument("--require_side_only", action="store_true", help="Require that only SIDE mode is reachable (main must fail)")
    p.add_argument("--tier", choices=["small", "medium", "large", "random"], default="random",
                   help="Force room size tier; default random.")
    return p.parse_args()


def main():
    args = parse_args()
    assets = load_registry(args.registry)
    forced_tier = None if args.tier == "random" else args.tier

    scene = generate_scene_with_retries(
        assets=assets,
        base_seed=args.seed,
        max_attempts=args.attempts,
        require_reachable=(not args.no_reachability),
        require_side_only=args.require_side_only and (not args.no_reachability),
        forced_tier=forced_tier,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scene, f, indent=2)

    print("Generated:", str(out_path))
    print("Scene:", scene["scene_name"])
    print("Tier:", scene["meta"]["tier"])
    print("Room:", scene["room"]["size_m"])
    print("Objects:", len(scene["objects"]))
    print("Reachability:", scene["nav"]["reachability_mode"])
    print("Corridor width (m):", scene["nav"]["corridor_width_m"])
    print("Passable-on-corridor:", scene["nav"]["passable_on_corridor_classes"])
    print("Origin zone:", scene["nav"]["origin_zone_xyxy_m"])
    print("Dest zone:", scene["nav"]["dest_zone_xyxy_m"])
    print("Wall big classes:", scene["meta"]["counts"]["wall_big_classes"])
    print("Meta:", {k: scene["meta"][k] for k in ["base_seed", "attempt_index", "final_seed"]})


if __name__ == "__main__":
    main()
