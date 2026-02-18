"""
GLB File Viewer — browse, preview, and batch delete/keep GLB files.

Dependencies:
    pip install trimesh Pillow numpy

Usage:
    python glb_viewer.py                          # opens folder picker
    python glb_viewer.py --folder kitchen/kitchen_assets_raw
    python glb_viewer.py --folder kitchen/kitchen_assets_raw --recursive
"""

import argparse
import math
import os
import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

try:
    import trimesh
except ImportError:
    print("ERROR: trimesh is required.  pip install trimesh")
    sys.exit(1)


# ── Rendering ────────────────────────────────────────────────────────────────

def load_glb(path: str) -> trimesh.Scene | trimesh.Trimesh | None:
    """Load a GLB file, return a trimesh object or None on failure."""
    try:
        return trimesh.load(path, force="scene")
    except Exception as e:
        print(f"[WARN] Failed to load {path}: {e}")
        return None


def scene_to_single_mesh(scene) -> trimesh.Trimesh | None:
    """Flatten a trimesh.Scene into a single Trimesh for rendering."""
    if isinstance(scene, trimesh.Trimesh):
        return scene
    if isinstance(scene, trimesh.Scene):
        meshes = []
        for name, geom in scene.geometry.items():
            if isinstance(geom, trimesh.Trimesh):
                meshes.append(geom)
        if meshes:
            return trimesh.util.concatenate(meshes)
    return None


def render_mesh_to_image(mesh: trimesh.Trimesh, width: int, height: int) -> Image.Image:
    """Software-render a mesh to a PIL Image using simple Z-buffer projection."""
    if mesh is None or len(mesh.vertices) == 0:
        img = Image.new("RGB", (width, height), (40, 40, 40))
        d = ImageDraw.Draw(img)
        d.text((width // 2 - 30, height // 2), "No mesh", fill=(180, 180, 180))
        return img

    verts = np.array(mesh.vertices, dtype=np.float64)
    faces = np.array(mesh.faces, dtype=np.int64)

    # Centre and scale to fit
    centre = (verts.max(axis=0) + verts.min(axis=0)) / 2.0
    verts = verts - centre
    extent = verts.max() - verts.min()
    if extent > 1e-9:
        verts = verts / extent

    # Simple isometric-ish camera: rotate slightly
    angle_x = math.radians(25)
    angle_y = math.radians(35)
    cx, sx = math.cos(angle_x), math.sin(angle_x)
    cy, sy = math.cos(angle_y), math.sin(angle_y)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    verts = verts @ Ry.T @ Rx.T

    # Orthographic projection
    scale = min(width, height) * 0.40
    proj_x = (verts[:, 0] * scale + width / 2).astype(np.int32)
    proj_y = (-verts[:, 1] * scale + height / 2).astype(np.int32)  # flip Y

    img = Image.new("RGB", (width, height), (40, 40, 40))
    draw = ImageDraw.Draw(img)

    # Compute face normals for simple shading
    light_dir = np.array([0.3, 0.8, 0.5])
    light_dir = light_dir / np.linalg.norm(light_dir)

    # Sort faces back-to-front (painter's algorithm on Z centroid)
    face_z = verts[faces].mean(axis=1)[:, 2]
    order = np.argsort(face_z)

    for fi in order:
        f = faces[fi]
        v0, v1, v2 = verts[f[0]], verts[f[1]], verts[f[2]]
        normal = np.cross(v1 - v0, v2 - v0)
        n_len = np.linalg.norm(normal)
        if n_len < 1e-12:
            continue
        normal /= n_len
        brightness = max(0.15, float(np.dot(normal, light_dir)))
        r = int(100 * brightness + 80 * brightness)
        g = int(130 * brightness + 60 * brightness)
        b = int(160 * brightness + 40 * brightness)
        r, g, b = min(r, 255), min(g, 255), min(b, 255)

        pts = [(int(proj_x[f[0]]), int(proj_y[f[0]])),
               (int(proj_x[f[1]]), int(proj_y[f[1]])),
               (int(proj_x[f[2]]), int(proj_y[f[2]]))]
        draw.polygon(pts, fill=(r, g, b))

    return img


# ── GUI ──────────────────────────────────────────────────────────────────────

THUMB_SIZE = 128
DETAIL_SIZE = 512
CELL_PAD = 8  # padx + pady around each thumbnail cell


class GLBViewer:
    def __init__(self, root: tk.Tk, folder: str, recursive: bool):
        self.root = root
        self.folder = Path(folder).resolve()
        self.recursive = recursive

        self.root.title(f"GLB Viewer — {self.folder}")
        self.root.geometry("1280x750")
        self.root.minsize(900, 550)

        # ── Data ──
        self.glb_files: list[Path] = []
        self.thumb_images: list[ImageTk.PhotoImage] = []
        self.selected: set[int] = set()
        self._cols = 4  # current column count, recomputed on resize
        self._resize_after_id = None  # debounce timer for re-layout

        # ── Top bar ──
        top = ttk.Frame(root)
        top.pack(fill="x", padx=6, pady=4)

        ttk.Label(top, text="Folder:").pack(side="left")
        self.lbl_folder = ttk.Label(top, text=str(self.folder), foreground="gray")
        self.lbl_folder.pack(side="left", padx=4)

        ttk.Button(top, text="Refresh", command=self.scan_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Select All", command=self.select_all).pack(side="left", padx=2)
        ttk.Button(top, text="Deselect All", command=self.deselect_all).pack(side="left", padx=2)

        self.lbl_count = ttk.Label(top, text="")
        self.lbl_count.pack(side="right", padx=4)

        # ── Action bar ──
        act = ttk.Frame(root)
        act.pack(fill="x", padx=6, pady=2)

        self.btn_delete = ttk.Button(act, text="Delete Selected", command=self.delete_selected)
        self.btn_delete.pack(side="left", padx=4)
        self.btn_keep = ttk.Button(act, text="Keep Selected (delete rest)", command=self.keep_selected)
        self.btn_keep.pack(side="left", padx=4)

        self.lbl_sel = ttk.Label(act, text="0 selected")
        self.lbl_sel.pack(side="left", padx=10)

        # ── Main split: left grid | right detail ──
        # Use a plain Frame + grid layout so we control the initial proportions.
        main = ttk.Frame(root)
        main.pack(fill="both", expand=True, padx=6, pady=4)
        main.columnconfigure(0, weight=3, minsize=400)
        main.columnconfigure(1, weight=0)  # detail panel: fixed width
        main.rowconfigure(0, weight=1)

        # Left: scrollable grid
        left_frame = ttk.Frame(main)
        left_frame.grid(row=0, column=0, sticky="nsew")

        self.canvas = tk.Canvas(left_frame, bg="#2b2b2b", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")

        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Right: detail panel (fixed width)
        right_frame = ttk.Frame(main, width=DETAIL_SIZE + 20)
        right_frame.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        right_frame.grid_propagate(False)

        self.detail_label = ttk.Label(right_frame, text="Click a thumbnail to preview",
                                      anchor="center", foreground="gray")
        self.detail_label.pack(pady=10)

        self.detail_canvas = tk.Canvas(right_frame, bg="#1e1e1e", width=DETAIL_SIZE,
                                       height=DETAIL_SIZE, highlightthickness=0)
        self.detail_canvas.pack(padx=6, pady=4)
        self.detail_photo = None

        self.info_text = tk.Text(right_frame, height=8, bg="#1e1e1e", fg="#cccccc",
                                 font=("Consolas", 9), wrap="word", state="disabled",
                                 borderwidth=0, highlightthickness=0)
        self.info_text.pack(fill="x", padx=6, pady=4)

        # ── Start ──
        self.scan_folder()

    # ── Scanning ──

    def scan_folder(self):
        pattern = "**/*.glb" if self.recursive else "*.glb"
        # Also scan one level into class subdirectories (common layout)
        files = sorted(self.folder.rglob("*.glb")) if self.recursive else sorted(
            list(self.folder.glob("*.glb")) + list(self.folder.glob("*/*.glb"))
        )
        self.glb_files = files
        self.selected.clear()
        self.lbl_count.config(text=f"{len(self.glb_files)} files")
        self._update_sel_label()
        self._build_grid()

    def _compute_cols(self) -> int:
        """Compute how many columns fit in the current canvas width."""
        w = self.canvas.winfo_width()
        if w < 50:  # not yet mapped
            w = 700
        cell = THUMB_SIZE + CELL_PAD * 2
        return max(1, w // cell)

    def _build_grid(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.thumb_images.clear()

        self.thumb_frames: list[ttk.Frame] = []
        self.check_vars: list[tk.BooleanVar] = []
        self._cols = self._compute_cols()

        for idx, path in enumerate(self.glb_files):
            row, col = divmod(idx, self._cols)

            frame = ttk.Frame(self.grid_frame, relief="flat")
            frame.grid(row=row, column=col, padx=4, pady=4)
            self.thumb_frames.append(frame)

            # Placeholder thumbnail (grey)
            placeholder = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), (60, 60, 60))
            d = ImageDraw.Draw(placeholder)
            d.text((10, THUMB_SIZE // 2 - 5), path.stem[:16], fill=(150, 150, 150))
            photo = ImageTk.PhotoImage(placeholder)
            self.thumb_images.append(photo)

            lbl = tk.Label(frame, image=photo, cursor="hand2", bd=2, relief="flat",
                           bg="#2b2b2b")
            lbl.pack()
            lbl.bind("<Button-1>", lambda e, i=idx: self._on_thumb_click(i))

            var = tk.BooleanVar(value=False)
            self.check_vars.append(var)
            cb = ttk.Checkbutton(frame, variable=var, command=lambda i=idx: self._on_check(i))
            cb.pack()

            name_lbl = ttk.Label(frame, text=path.stem[:20], font=("Consolas", 8))
            name_lbl.pack()

        # Render thumbnails in background
        self.root.after(50, self._render_thumbs_batch, 0)

    def _reflow_grid(self):
        """Re-grid existing thumbnail frames using updated column count."""
        new_cols = self._compute_cols()
        if new_cols == self._cols:
            return
        self._cols = new_cols
        for idx, frame in enumerate(self.thumb_frames):
            row, col = divmod(idx, self._cols)
            frame.grid(row=row, column=col, padx=4, pady=4)

    def _render_thumbs_batch(self, start: int, batch: int = 8):
        """Render thumbnails in small batches to keep UI responsive."""
        end = min(start + batch, len(self.glb_files))
        for idx in range(start, end):
            scene = load_glb(str(self.glb_files[idx]))
            mesh = scene_to_single_mesh(scene) if scene else None
            img = render_mesh_to_image(mesh, THUMB_SIZE, THUMB_SIZE)
            photo = ImageTk.PhotoImage(img)
            self.thumb_images[idx] = photo

            lbl = self.thumb_frames[idx].winfo_children()[0]
            lbl.configure(image=photo)

        if end < len(self.glb_files):
            self.root.after(10, self._render_thumbs_batch, end)

    # ── Events ──

    def _on_thumb_click(self, idx: int):
        """Show detail view for clicked thumbnail."""
        path = self.glb_files[idx]
        self.detail_label.config(text=path.name)

        scene = load_glb(str(path))
        mesh = scene_to_single_mesh(scene) if scene else None

        img = render_mesh_to_image(mesh, DETAIL_SIZE, DETAIL_SIZE)
        self.detail_photo = ImageTk.PhotoImage(img)
        self.detail_canvas.delete("all")
        self.detail_canvas.create_image(DETAIL_SIZE // 2, DETAIL_SIZE // 2,
                                        image=self.detail_photo)

        # Info
        info_lines = [f"File: {path.name}"]
        info_lines.append(f"Path: {path.relative_to(self.folder)}")
        size_kb = path.stat().st_size / 1024
        info_lines.append(f"Size: {size_kb:.1f} KB")
        if mesh is not None:
            info_lines.append(f"Vertices: {len(mesh.vertices):,}")
            info_lines.append(f"Faces: {len(mesh.faces):,}")
            bb = mesh.bounding_box.extents
            info_lines.append(f"Bounding box: {bb[0]:.3f} × {bb[1]:.3f} × {bb[2]:.3f}")

        self.info_text.config(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(info_lines))
        self.info_text.config(state="disabled")

    def _on_check(self, idx: int):
        if self.check_vars[idx].get():
            self.selected.add(idx)
            self.thumb_frames[idx].winfo_children()[0].config(bg="#4488cc")
        else:
            self.selected.discard(idx)
            self.thumb_frames[idx].winfo_children()[0].config(bg="#2b2b2b")
        self._update_sel_label()

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)
        # Debounce reflow so we don't re-grid on every pixel of a drag
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(150, self._reflow_grid)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    # ── Selection ──

    def select_all(self):
        for i in range(len(self.glb_files)):
            self.check_vars[i].set(True)
            self.selected.add(i)
            self.thumb_frames[i].winfo_children()[0].config(bg="#4488cc")
        self._update_sel_label()

    def deselect_all(self):
        for i in range(len(self.glb_files)):
            self.check_vars[i].set(False)
            self.thumb_frames[i].winfo_children()[0].config(bg="#2b2b2b")
        self.selected.clear()
        self._update_sel_label()

    def _update_sel_label(self):
        n = len(self.selected)
        self.lbl_sel.config(text=f"{n} selected")

    # ── Delete / Keep ──

    def delete_selected(self):
        if not self.selected:
            messagebox.showinfo("Nothing selected", "Select files first.")
            return
        names = [self.glb_files[i].name for i in sorted(self.selected)]
        preview = "\n".join(names[:15])
        if len(names) > 15:
            preview += f"\n... and {len(names) - 15} more"
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete {len(names)} file(s)?\n\n{preview}"):
            return
        for i in sorted(self.selected, reverse=True):
            p = self.glb_files[i]
            try:
                p.unlink()
                print(f"[DELETED] {p}")
            except Exception as e:
                print(f"[ERROR] {p}: {e}")
        self.scan_folder()

    def keep_selected(self):
        if not self.selected:
            messagebox.showinfo("Nothing selected", "Select files to keep first.")
            return
        to_delete = [i for i in range(len(self.glb_files)) if i not in self.selected]
        if not to_delete:
            messagebox.showinfo("Nothing to delete", "All files are selected.")
            return
        names = [self.glb_files[i].name for i in to_delete]
        preview = "\n".join(names[:15])
        if len(names) > 15:
            preview += f"\n... and {len(names) - 15} more"
        if not messagebox.askyesno("Confirm Delete (keep selected)",
                                   f"Delete {len(names)} UNSELECTED file(s)?\n\n{preview}"):
            return
        for i in sorted(to_delete, reverse=True):
            p = self.glb_files[i]
            try:
                p.unlink()
                print(f"[DELETED] {p}")
            except Exception as e:
                print(f"[ERROR] {p}: {e}")
        self.scan_folder()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GLB file viewer with preview and batch ops")
    parser.add_argument("--folder", type=str, default=None,
                        help="Folder containing .glb files. Opens picker if omitted.")
    parser.add_argument("--recursive", action="store_true", default=False,
                        help="Scan subfolders recursively.")
    args = parser.parse_args()

    root = tk.Tk()

    if args.folder:
        folder = Path(args.folder).resolve()
    else:
        folder = filedialog.askdirectory(title="Select folder with GLB files")
        if not folder:
            print("No folder selected.")
            sys.exit(0)
        folder = Path(folder).resolve()

    if not folder.is_dir():
        print(f"ERROR: Not a directory: {folder}")
        sys.exit(1)

    GLBViewer(root, str(folder), args.recursive)
    root.mainloop()


if __name__ == "__main__":
    main()
