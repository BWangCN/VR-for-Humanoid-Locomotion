import json
import math
import os

import omni
from pxr import UsdGeom, Usd, Gf, Sdf, UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()

def ensure_xform(path: str):
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        UsdGeom.Xform.Define(stage, path)
    return stage.GetPrimAtPath(path)

def set_xform_xyz_rpy_deg(prim_path: str, pos, rpy_deg):
    xform = UsdGeom.Xformable(stage.GetPrimAtPath(prim_path))
    # Clear existing ops for determinism
    xform.ClearXformOpOrder()

    t = xform.AddTranslateOp()
    t.Set(Gf.Vec3d(pos[0], pos[1], pos[2]))

    # USD uses XYZ rotations; we'll apply Roll-Pitch-Yaw in degrees as X,Y,Z
    r = xform.AddRotateXYZOp()
    r.Set(Gf.Vec3f(rpy_deg[0], rpy_deg[1], rpy_deg[2]))

def add_reference(prim_path: str, usd_path: str):
    prim = ensure_xform(prim_path)
    prim.GetReferences().ClearReferences()
    prim.GetReferences().AddReference(usd_path)
    return prim

def make_static_collider_box(path: str, center, size_xyz):
    # size_xyz in meters; Isaac/PhysX expects meters as well.
    box = UsdGeom.Cube.Define(stage, path)
    box.CreateSizeAttr(1.0)  # base cube is 1 unit; we scale it

    # set transform: translate + scale
    xform = UsdGeom.Xformable(box.GetPrim())
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(Gf.Vec3d(*center))
    xform.AddScaleOp().Set(Gf.Vec3f(size_xyz[0], size_xyz[1], size_xyz[2]))

    # collision
    UsdPhysics.CollisionAPI.Apply(box.GetPrim())

    # make it static by not applying RigidBodyAPI (static collider)
    return box.GetPrim()

def ensure_physics_scene():
    if not stage.GetPrimAtPath("/World/physics").IsValid():
        scene = UsdPhysics.Scene.Define(stage, "/World/physics")
        scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))
        scene.CreateGravityMagnitudeAttr().Set(9.81)
        PhysxSchema.PhysxSceneAPI.Apply(stage.GetPrimAtPath("/World/physics"))

def apply_rigid_body(prim_path: str):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return
    # Apply rigid body + collision to the referenced prim root.
    UsdPhysics.RigidBodyAPI.Apply(prim)
    UsdPhysics.CollisionAPI.Apply(prim)
    # Optional: add mass properties (can be tuned later)
    mass_api = UsdPhysics.MassAPI.Apply(prim)
    if not mass_api.GetMassAttr().HasAuthoredValueOpinion():
        mass_api.CreateMassAttr(10.0)

def build_room(room_spec):
    origin = room_spec.get("origin_m", [0.0, 0.0, 0.0])
    w, l, h = room_spec["size_m"]
    wall_t = room_spec.get("wall_thickness_m", 0.1)
    floor_t = room_spec.get("floor_thickness_m", 0.05)

    # Create root
    ensure_xform("/World")
    ensure_xform("/World/Room")
    ensure_xform("/World/Assets")

    # Floor (static collider box)
    # Center at z = -floor_t/2 so top surface is z=0 at origin
    floor_center = [origin[0] + w/2, origin[1] + l/2, origin[2] - floor_t/2]
    floor_size = [w, l, floor_t]
    make_static_collider_box("/World/Room/Floor", floor_center, floor_size)

    # Walls: 4 static boxes, centered on perimeter, height h
    # Put wall bottom at z=0 => center z = h/2
    cz = origin[2] + h/2

    # Left wall (x=0 side)
    make_static_collider_box(
        "/World/Room/Wall_Left",
        [origin[0] - wall_t/2, origin[1] + l/2, cz],
        [wall_t, l + 2*wall_t, h]
    )
    # Right wall (x=w side)
    make_static_collider_box(
        "/World/Room/Wall_Right",
        [origin[0] + w + wall_t/2, origin[1] + l/2, cz],
        [wall_t, l + 2*wall_t, h]
    )
    # Back wall (y=0 side)
    make_static_collider_box(
        "/World/Room/Wall_Back",
        [origin[0] + w/2, origin[1] - wall_t/2, cz],
        [w, wall_t, h]
    )
    # Front wall (y=l side)
    make_static_collider_box(
        "/World/Room/Wall_Front",
        [origin[0] + w/2, origin[1] + l + wall_t/2, cz],
        [w, wall_t, h]
    )

def build_scene_from_json(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    ensure_physics_scene()

    # Build room
    build_room(spec["room"])

    # Place objects
    for obj in spec["objects"]:
        usd_path = obj["usd_path"]
        prim_path = obj["prim_path"]
        pos = obj["pose_m"]["pos"]
        rot = obj["pose_m"]["rot_deg"]

        # Isaac likes forward slashes; Windows path in USD ref usually works with forward slashes.
        usd_path_fixed = usd_path.replace("\\", "/")

        add_reference(prim_path, usd_path_fixed)
        set_xform_xyz_rpy_deg(prim_path, pos, rot)

        if obj.get("rigid_body", False):
            apply_rigid_body(prim_path)

# ---- Run ----
# Put your json path here:
JSON_PATH = r"C:\Users\Wayne\Desktop\GMU\PhD\Humanoid\SimEnv\bedroom\layout_json\bedroom05.json"
build_scene_from_json(JSON_PATH)
print("Scene built from JSON:", JSON_PATH)
