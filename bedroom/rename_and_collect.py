import os
import shutil
import pandas as pd
from collections import defaultdict

# -------- Paths --------
BASE_DIR = r"C:\Users\Wayne\Desktop\GMU\PhD\Humanoid\SimEnv\bedroom"
CANDIDATES_CSV = os.path.join(BASE_DIR, "objaverse_bedroom", "bedroom_candidates.csv")
PATHS_CSV = os.path.join(BASE_DIR, "objaverse_bedroom", "bedroom_downloaded_paths.csv")

# 输出目录（当前工程下）
OUTPUT_DIR = os.path.join(BASE_DIR, "bedroom_assets_raw")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# -------- Load CSVs --------
df_cand = pd.read_csv(CANDIDATES_CSV)
df_paths = pd.read_csv(PATHS_CSV)

# uid -> local_path
uid_to_path = dict(zip(df_paths["uid"], df_paths["local_path"]))

# class -> list[(uid, src_path)]
by_class = defaultdict(list)

for _, row in df_cand.iterrows():
    uid = row["uid"]
    cls = row["class"]

    if uid not in uid_to_path:
        print(f"[WARN] UID not found in paths CSV: {uid}")
        continue

    src_path = uid_to_path[uid]
    if not os.path.exists(src_path):
        print(f"[WARN] File does not exist: {src_path}")
        continue

    by_class[cls].append((uid, src_path))

# -------- Copy & Rename --------
print("Renaming and copying assets...\n")

for cls, items in by_class.items():
    class_dir = os.path.join(OUTPUT_DIR, cls)
    os.makedirs(class_dir, exist_ok=True)

    for idx, (uid, src_path) in enumerate(items):
        new_name = f"{cls}{idx:02d}.glb"
        dst_path = os.path.join(class_dir, new_name)

        if os.path.exists(dst_path):
            print(f"[SKIP] Exists: {dst_path}")
            continue

        shutil.copy2(src_path, dst_path)
        print(f"[OK] {uid}  ->  {dst_path}")

print("\nDone.")
print(f"Assets collected under: {OUTPUT_DIR}")
