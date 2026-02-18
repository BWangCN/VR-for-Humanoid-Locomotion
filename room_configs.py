# room_configs.py
# Room-type-specific parameters for scene generation.
# Each config defines placement classes, room sizes, and item counts.
# The scene_json_generator.py uses these to build scenes for any room type.

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class RoomConfig:
    """All room-type-specific parameters used by the scene generator."""

    name: str  # "bedroom", "livingroom", etc.
    room_height: float = 2.6

    # Room tiers: {"small": {"w": (min,max), "l": (min,max)}, ...}
    room_tiers: Dict[str, Dict[str, Tuple[float, float]]] = field(default_factory=dict)
    room_tier_probs: List[float] = field(default_factory=lambda: [0.35, 0.45, 0.20])

    # Item counts per tier (annealing reduces these)
    tier_counts: Dict[str, Dict[str, Tuple[int, int]]] = field(default_factory=dict)

    # Anchor item: 1 main piece placed against wall (bed / sofa)
    anchor_class: str = "bed"
    # Explicit asset names; None = use all assets in anchor_class
    anchor_candidates: Optional[List[str]] = None

    # Wall-big items (per-class probability of appearing, per tier)
    wall_big_class_probs: Dict[str, Dict[str, float]] = field(default_factory=dict)
    wall_big_max_by_tier: Dict[str, int] = field(default_factory=dict)

    # Free-medium placement stage (chairs, armchairs, etc.)
    free_medium_classes: List[str] = field(default_factory=lambda: ["chair"])

    # Step-over candidate classes (for corridor obstacles)
    step_over_candidate_classes: Set[str] = field(default_factory=set)

    # Cluster classes (placed near anchor)
    cluster_classes: List[str] = field(default_factory=list)

    # Scatter classes (placed anywhere in room)
    scatter_classes: List[str] = field(default_factory=list)

    # Pair classes: items that come in pairs (e.g. shoes)
    pair_classes: Set[str] = field(default_factory=lambda: {"shoes"})

    # Corridor width ranges per tier
    corridor_width_range_by_tier: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    # Classes whose front side should face the room when placed against a wall.
    # Objects not in this set get random cardinal yaw (current behavior).
    orientation_sensitive_classes: Set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
#  BEDROOM
# ---------------------------------------------------------------------------
BEDROOM = RoomConfig(
    name="bedroom",

    room_tiers={
        "small":  {"w": (3.0, 3.8), "l": (3.6, 4.6)},
        "medium": {"w": (3.8, 4.8), "l": (4.2, 5.8)},
        "large":  {"w": (4.8, 6.8), "l": (5.5, 7.5)},
    },
    room_tier_probs=[0.35, 0.45, 0.20],

    tier_counts={
        "small":  {"chairs": (1, 2), "small_items": (5, 10),  "cluster_items": (3, 6)},
        "medium": {"chairs": (1, 3), "small_items": (8, 16),  "cluster_items": (5, 10)},
        "large":  {"chairs": (2, 3), "small_items": (12, 20), "cluster_items": (6, 12)},
    },

    anchor_class="bed",
    anchor_candidates=[f"bed{i:02d}" for i in range(1, 20)],

    wall_big_class_probs={
        "small":  {"wardrobe": 0.60, "suitcase": 0.65, "desk": 0.55, "cabinet": 0.50},
        "medium": {"wardrobe": 0.80, "suitcase": 0.75, "desk": 0.80, "cabinet": 0.70},
        "large":  {"wardrobe": 0.90, "suitcase": 0.85, "desk": 0.90, "cabinet": 0.85},
    },
    wall_big_max_by_tier={"small": 3, "medium": 4, "large": 5},

    free_medium_classes=["chair"],

    step_over_candidate_classes={"blanket", "shoes", "pillow", "book", "laptop", "sock", "socks"},

    cluster_classes=["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"],
    scatter_classes=["shoes", "blanket", "box", "book", "bottle", "pillow", "laptop"],

    pair_classes={"shoes"},

    corridor_width_range_by_tier={
        "small":  (0.62, 1.20),
        "medium": (0.62, 1.30),
        "large":  (0.62, 1.40),
    },

    orientation_sensitive_classes={"bed", "desk", "wardrobe", "cabinet"},
)


# ---------------------------------------------------------------------------
#  LIVING ROOM
# ---------------------------------------------------------------------------
LIVINGROOM = RoomConfig(
    name="livingroom",

    room_tiers={
        "small":  {"w": (3.5, 4.5), "l": (4.0, 5.0)},
        "medium": {"w": (4.5, 5.5), "l": (5.0, 6.5)},
        "large":  {"w": (5.5, 7.5), "l": (6.5, 8.5)},
    },
    room_tier_probs=[0.30, 0.45, 0.25],

    tier_counts={
        "small":  {"chairs": (1, 2), "small_items": (5, 10),  "cluster_items": (3, 6)},
        "medium": {"chairs": (1, 3), "small_items": (8, 16),  "cluster_items": (5, 10)},
        "large":  {"chairs": (2, 4), "small_items": (12, 22), "cluster_items": (6, 14)},
    },

    anchor_class="sofa",
    anchor_candidates=None,  # use all sofa assets

    wall_big_class_probs={
        "small":  {"bookshelf": 0.65, "cabinet": 0.55, "desk": 0.50, "piano": 0.10},
        "medium": {"bookshelf": 0.80, "cabinet": 0.70, "desk": 0.65, "piano": 0.15},
        "large":  {"bookshelf": 0.90, "cabinet": 0.80, "desk": 0.75, "piano": 0.20},
    },
    wall_big_max_by_tier={"small": 3, "medium": 4, "large": 5},

    free_medium_classes=["armchair", "coffee_table", "floor_lamp", "side_table", "plant_pot"],

    step_over_candidate_classes={"blanket", "shoes", "book", "magazine", "remote", "toy", "cushion"},

    cluster_classes=["cushion", "blanket", "book", "magazine", "remote", "mug", "bottle"],
    scatter_classes=["shoes", "box", "toy", "book", "bottle", "magazine", "remote"],

    pair_classes={"shoes"},

    corridor_width_range_by_tier={
        "small":  (0.62, 1.20),
        "medium": (0.62, 1.30),
        "large":  (0.62, 1.40),
    },

    orientation_sensitive_classes={"sofa", "desk", "bookshelf", "cabinet", "piano"},
)


# ---------------------------------------------------------------------------
#  KITCHEN
# ---------------------------------------------------------------------------
KITCHEN = RoomConfig(
    name="kitchen",

    room_tiers={
        "small":  {"w": (2.5, 3.5), "l": (3.0, 4.0)},
        "medium": {"w": (3.5, 4.5), "l": (4.0, 5.5)},
        "large":  {"w": (4.5, 6.0), "l": (5.5, 7.0)},
    },
    room_tier_probs=[0.35, 0.45, 0.20],

    tier_counts={
        "small":  {"chairs": (1, 2), "small_items": (5, 10),  "cluster_items": (3, 6)},
        "medium": {"chairs": (1, 3), "small_items": (8, 16),  "cluster_items": (5, 10)},
        "large":  {"chairs": (2, 4), "small_items": (12, 20), "cluster_items": (6, 12)},
    },

    anchor_class="dining_table",
    anchor_candidates=None,  # use all dining_table assets

    wall_big_class_probs={
        "small":  {"fridge": 0.90, "cabinet": 0.70, "oven": 0.60, "dishwasher": 0.30},
        "medium": {"fridge": 0.95, "cabinet": 0.85, "oven": 0.75, "dishwasher": 0.45},
        "large":  {"fridge": 0.95, "cabinet": 0.90, "oven": 0.85, "dishwasher": 0.55},
    },
    wall_big_max_by_tier={"small": 3, "medium": 4, "large": 5},

    free_medium_classes=["chair", "stool", "trash_can"],

    step_over_candidate_classes={"towel", "shoes", "bottle", "box", "pot", "pan", "bag"},

    cluster_classes=["pot", "pan", "bottle", "mug", "plate", "bowl", "cutting_board"],
    scatter_classes=["shoes", "bottle", "box", "bag", "towel", "mug", "bowl"],

    pair_classes={"shoes"},

    corridor_width_range_by_tier={
        "small":  (0.62, 1.20),
        "medium": (0.62, 1.30),
        "large":  (0.62, 1.40),
    },

    orientation_sensitive_classes={"dining_table", "fridge", "cabinet", "oven", "dishwasher"},
)


# ---------------------------------------------------------------------------
#  Registry
# ---------------------------------------------------------------------------
CONFIGS: Dict[str, RoomConfig] = {
    "bedroom": BEDROOM,
    "livingroom": LIVINGROOM,
    "kitchen": KITCHEN,
}


def get_config(room_type: str) -> RoomConfig:
    """Look up a room config by name. Raises KeyError if not found."""
    if room_type not in CONFIGS:
        valid = ", ".join(sorted(CONFIGS.keys()))
        raise KeyError(f"Unknown room_type '{room_type}'. Available: {valid}")
    return CONFIGS[room_type]
