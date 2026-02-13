import os
import random
import pandas as pd
import objaverse

# ——— Config ———
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "objaverse_livingroom")
PER_CLASS = 50
# Use 1 process on Windows to avoid os.rename FileExistsError race condition
# in objaverse's _download_object when files are already cached.
DOWNLOAD_PROCESSES = 1

# Living room asset classes and LVIS keywords
wanted = {
    "sofa":         ["sofa", "couch"],
    "tv_stand":     ["television_receiver", "entertainment_center"],
    "bookshelf":    ["bookcase"],
    "cabinet":      ["cabinet", "sideboard"],
    "desk":         ["desk"],
    "piano":        ["piano"],
    "armchair":     ["armchair"],
    "coffee_table": ["coffee_table", "table"],
    "floor_lamp":   ["lamp", "floor_lamp"],
    "side_table":   ["table"],
    "plant_pot":    ["flowerpot", "pot"],
    "cushion":      ["cushion", "pillow"],
    "blanket":      ["blanket", "quilt"],
    "book":         ["book"],
    "magazine":     ["magazine"],
    "remote":       ["remote_control"],
    "mug":          ["mug", "cup"],
    "bottle":       ["bottle"],
    "shoes":        ["shoe", "shoes"],
    "box":          ["box"],
    "toy":          ["toy", "teddy_bear", "stuffed_animal"],
}


def main():
    random.seed(42)
    os.makedirs(OUT_DIR, exist_ok=True)

    # From LVIS annotations: category -> [uids]
    lvis = objaverse.load_lvis_annotations()

    selected_uids = []
    rows = []

    print("=== Sampling UIDs per class ===")
    for cls, keys in wanted.items():
        found = []
        for key in keys:
            if key in lvis:
                found.extend(lvis[key])
        cand_count = len(found)
        print(f"{cls:15s} candidates={cand_count}")

        if cand_count > 0:
            sample = random.sample(found, k=min(PER_CLASS, cand_count))
            for uid in sample:
                rows.append({"uid": uid, "class": cls})
            selected_uids.extend(sample)

    # Save candidate CSV
    df_cand = pd.DataFrame(rows)
    csv_path = os.path.join(OUT_DIR, "livingroom_candidates.csv")
    df_cand.to_csv(csv_path, index=False)
    print(f"Saved candidate CSV: {csv_path}")

    # ——— Download Models ———
    print(f"\n=== Downloading {len(selected_uids)} assets ===")
    paths = objaverse.load_objects(selected_uids, download_processes=DOWNLOAD_PROCESSES)
    print("Download finished")

    # Save local path CSV
    rows = []
    for uid, local_path in paths.items():
        rows.append({"uid": uid, "local_path": local_path})

    df_paths = pd.DataFrame(rows)
    paths_csv = os.path.join(OUT_DIR, "livingroom_downloaded_paths.csv")
    df_paths.to_csv(paths_csv, index=False)
    print(f"Saved downloaded paths: {paths_csv}")


if __name__ == "__main__":
    main()
