import json
import math
import os

import omni
from pxr import UsdGeom, Usd, Gf, Sdf, UsdPhysics, PhysxSchema, UsdShade

stage = omni.usd.get_context().get_stage()
TEXTURES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "bedroom", "textures")

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

def make_cone_marker(prim_path: str, pos, radius: float, height: float, color_rgba):
    """Create a colored cone primitive as a visual marker (no physics)."""
    cone = UsdGeom.Cone.Define(stage, prim_path)
    cone.CreateRadiusAttr(radius)
    cone.CreateHeightAttr(height)
    cone.CreateAxisAttr("Z")

    # Position: cone center is at half-height, so shift up so base sits on floor
    xform = UsdGeom.Xformable(cone.GetPrim())
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(Gf.Vec3d(pos[0], pos[1], pos[2] + height / 2.0))

    # Display color
    cone.CreateDisplayColorAttr([Gf.Vec3f(color_rgba[0], color_rgba[1], color_rgba[2])])
    if len(color_rgba) > 3:
        cone.CreateDisplayOpacityAttr([color_rgba[3]])

    return cone.GetPrim()

def make_visual_quad(path, points, uvs):
    """Create a single-quad UsdGeom.Mesh with explicit UV coordinates.

    Args:
        path: USD prim path
        points: list of 4 Gf.Vec3f corners (CCW winding from visible side)
        uvs: list of 4 Gf.Vec2f UV coordinates
    """
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr("none")

    # Compute face normal from vertex winding (CCW cross product)
    e1 = points[1] - points[0]
    e2 = points[3] - points[0]
    n = Gf.Vec3f(
        e1[1]*e2[2] - e1[2]*e2[1],
        e1[2]*e2[0] - e1[0]*e2[2],
        e1[0]*e2[1] - e1[1]*e2[0],
    ).GetNormalized()
    mesh.CreateNormalsAttr([n] * 4)
    mesh.SetNormalsInterpolation("vertex")

    # UV coordinates as "st" primvar
    pv_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    st = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                              UsdGeom.Tokens.vertex)
    st.Set(uvs)

    return mesh.GetPrim()

def create_pbr_material(mat_path, color_path, normal_path, roughness_path,
                        tile_scale=(1.0, 1.0)):
    """Create a UsdPreviewSurface material with Color/Normal/Roughness maps.

    Shader graph:
        PrimvarReader_float2("st") -> UsdTransform2d(scale) ->
            UsdUVTexture(color)     -> diffuseColor
            UsdUVTexture(normal)    -> normal
            UsdUVTexture(roughness) -> roughness (r channel)
              -> UsdPreviewSurface -> Material(surface)
    """
    material = UsdShade.Material.Define(stage, mat_path)

    # UsdPreviewSurface
    surface = UsdShade.Shader.Define(stage, f"{mat_path}/PreviewSurface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    surface.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(0)
    material.CreateSurfaceOutput().ConnectToSource(
        surface.ConnectableAPI(), "surface")

    # PrimvarReader for UV
    st_reader = UsdShade.Shader.Define(stage, f"{mat_path}/st_reader")
    st_reader.CreateIdAttr("UsdPrimvarReader_float2")
    st_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")

    # Transform2d for tiling
    xf = UsdShade.Shader.Define(stage, f"{mat_path}/st_transform")
    xf.CreateIdAttr("UsdTransform2d")
    xf.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(
        st_reader.ConnectableAPI(), "result")
    xf.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(
        Gf.Vec2f(tile_scale[0], tile_scale[1]))

    def _add_tex(name, file_path):
        tex = UsdShade.Shader.Define(stage, f"{mat_path}/{name}")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(file_path)
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
            xf.ConnectableAPI(), "result")
        tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        return tex

    # Color -> diffuseColor
    color_tex = _add_tex("color_tex", color_path)
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f) \
        .ConnectToSource(color_tex.ConnectableAPI(), "rgb")

    # Normal -> normal
    normal_tex = _add_tex("normal_tex", normal_path)
    surface.CreateInput("normal", Sdf.ValueTypeNames.Normal3f) \
        .ConnectToSource(normal_tex.ConnectableAPI(), "rgb")

    # Roughness -> roughness (r channel)
    rough_tex = _add_tex("roughness_tex", roughness_path)
    surface.CreateInput("roughness", Sdf.ValueTypeNames.Float) \
        .ConnectToSource(rough_tex.ConnectableAPI(), "r")

    return material

def apply_room_materials(room_spec):
    """Create visual quads with PBR materials for floor and walls.

    Reads floor_material / wall_material from room_spec.
    No-op when both are null (backward compatible with old JSONs).
    """
    floor_mat_spec = room_spec.get("floor_material")
    wall_mat_spec = room_spec.get("wall_material")

    if floor_mat_spec is None and wall_mat_spec is None:
        return

    origin = room_spec.get("origin_m", [0.0, 0.0, 0.0])
    w, l, h = room_spec["size_m"]
    ox, oy, oz = origin[0], origin[1], origin[2]

    ensure_xform("/World/Room/Materials")
    ensure_xform("/World/Room/Visuals")

    # ---- Floor ----
    if floor_mat_spec is not None:
        tpm = floor_mat_spec.get("tiles_per_meter", 1.0)

        floor_pts = [
            Gf.Vec3f(ox,     oy,     oz),
            Gf.Vec3f(ox + w, oy,     oz),
            Gf.Vec3f(ox + w, oy + l, oz),
            Gf.Vec3f(ox,     oy + l, oz),
        ]
        floor_uvs = [
            Gf.Vec2f(0,       0),
            Gf.Vec2f(w * tpm, 0),
            Gf.Vec2f(w * tpm, l * tpm),
            Gf.Vec2f(0,       l * tpm),
        ]
        make_visual_quad("/World/Room/Visuals/Floor", floor_pts, floor_uvs)

        color_abs = os.path.join(TEXTURES_ROOT,
                                 floor_mat_spec["color"]).replace("\\", "/")
        normal_abs = os.path.join(TEXTURES_ROOT,
                                  floor_mat_spec["normal"]).replace("\\", "/")
        rough_abs = os.path.join(TEXTURES_ROOT,
                                 floor_mat_spec["roughness"]).replace("\\", "/")

        floor_material = create_pbr_material(
            "/World/Room/Materials/FloorMat",
            color_abs, normal_abs, rough_abs,
        )
        UsdShade.MaterialBindingAPI.Apply(
            stage.GetPrimAtPath("/World/Room/Visuals/Floor")
        ).Bind(floor_material)

        # Hide the collision-only floor cube so textured quad is visible
        UsdGeom.Imageable(
            stage.GetPrimAtPath("/World/Room/Floor")
        ).MakeInvisible()

    # ---- Walls ----
    if wall_mat_spec is not None:
        tpm = wall_mat_spec.get("tiles_per_meter", 1.0)

        color_abs = os.path.join(TEXTURES_ROOT,
                                 wall_mat_spec["color"]).replace("\\", "/")
        normal_abs = os.path.join(TEXTURES_ROOT,
                                  wall_mat_spec["normal"]).replace("\\", "/")
        rough_abs = os.path.join(TEXTURES_ROOT,
                                 wall_mat_spec["roughness"]).replace("\\", "/")

        wall_material = create_pbr_material(
            "/World/Room/Materials/WallMat",
            color_abs, normal_abs, rough_abs,
        )

        # Left wall (x=ox, inner face → +X normal)
        make_visual_quad("/World/Room/Visuals/Wall_Left", [
            Gf.Vec3f(ox, oy,     oz),
            Gf.Vec3f(ox, oy + l, oz),
            Gf.Vec3f(ox, oy + l, oz + h),
            Gf.Vec3f(ox, oy,     oz + h),
        ], [
            Gf.Vec2f(0, 0),       Gf.Vec2f(l * tpm, 0),
            Gf.Vec2f(l * tpm, h * tpm), Gf.Vec2f(0, h * tpm),
        ])

        # Right wall (x=ox+w, inner face → -X normal)
        make_visual_quad("/World/Room/Visuals/Wall_Right", [
            Gf.Vec3f(ox + w, oy + l, oz),
            Gf.Vec3f(ox + w, oy,     oz),
            Gf.Vec3f(ox + w, oy,     oz + h),
            Gf.Vec3f(ox + w, oy + l, oz + h),
        ], [
            Gf.Vec2f(0, 0),       Gf.Vec2f(l * tpm, 0),
            Gf.Vec2f(l * tpm, h * tpm), Gf.Vec2f(0, h * tpm),
        ])

        # Back wall (y=oy, inner face → +Y normal)
        make_visual_quad("/World/Room/Visuals/Wall_Back", [
            Gf.Vec3f(ox + w, oy, oz),
            Gf.Vec3f(ox,     oy, oz),
            Gf.Vec3f(ox,     oy, oz + h),
            Gf.Vec3f(ox + w, oy, oz + h),
        ], [
            Gf.Vec2f(0, 0),       Gf.Vec2f(w * tpm, 0),
            Gf.Vec2f(w * tpm, h * tpm), Gf.Vec2f(0, h * tpm),
        ])

        # Front wall (y=oy+l, inner face → -Y normal)
        make_visual_quad("/World/Room/Visuals/Wall_Front", [
            Gf.Vec3f(ox,     oy + l, oz),
            Gf.Vec3f(ox + w, oy + l, oz),
            Gf.Vec3f(ox + w, oy + l, oz + h),
            Gf.Vec3f(ox,     oy + l, oz + h),
        ], [
            Gf.Vec2f(0, 0),       Gf.Vec2f(w * tpm, 0),
            Gf.Vec2f(w * tpm, h * tpm), Gf.Vec2f(0, h * tpm),
        ])

        # Bind wall material and hide collision cubes
        for name in ("Wall_Left", "Wall_Right", "Wall_Back", "Wall_Front"):
            UsdShade.MaterialBindingAPI.Apply(
                stage.GetPrimAtPath(f"/World/Room/Visuals/{name}")
            ).Bind(wall_material)
            UsdGeom.Imageable(
                stage.GetPrimAtPath(f"/World/Room/{name}")
            ).MakeInvisible()

def build_room(room_spec):
    origin = room_spec.get("origin_m", [0.0, 0.0, 0.0])
    w, l, h = room_spec["size_m"]
    wall_t = room_spec.get("wall_thickness_m", 0.1)
    floor_t = room_spec.get("floor_thickness_m", 0.001)

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

    # Apply PBR textures when material info is present in JSON
    apply_room_materials(room_spec)

def build_scene_from_json(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    ensure_physics_scene()

    # Build room
    build_room(spec["room"])

    # Ensure marker parent exists
    ensure_xform("/World/Markers")

    # Place objects
    for obj in spec["objects"]:
        prim_path = obj["prim_path"]
        pos = obj["pose_m"]["pos"]
        rot = obj["pose_m"]["rot_deg"]

        if obj.get("class") == "marker":
            # Create cone primitive instead of loading a USD reference
            radius = obj.get("marker_radius_m", 0.08)
            height = obj.get("marker_height_m", 0.40)
            color = obj.get("marker_color_rgba", [1.0, 1.0, 1.0, 1.0])
            make_cone_marker(prim_path, pos, radius, height, color)
        else:
            usd_path = obj["usd_path"]
            # Isaac likes forward slashes; Windows path in USD ref usually works with forward slashes.
            usd_path_fixed = usd_path.replace("\\", "/")

            usd_path_fixed = usd_path_fixed.replace('C:/Users/Wayne/Desktop/GMU/PhD/Humanoid/SimEnv', 'D:/Code/VR-for-Humanoid-Locomotion')

            add_reference(prim_path, usd_path_fixed)
            set_xform_xyz_rpy_deg(prim_path, pos, rot)

            if obj.get("rigid_body", False):
                apply_rigid_body(prim_path)

# ---- Run ----
# Put your json path here:
# PROJ_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT = "D:/Code/VR-for-Humanoid-Locomotion"
JSON_FILE = './bedroom/layout_json/bedroom_d3/bedroom_d3_000_original.json'
JSON_PATH = os.path.join(PROJ_ROOT, JSON_FILE)
build_scene_from_json(JSON_PATH)
print("Scene built from JSON:", JSON_PATH)
