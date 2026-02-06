import os
import random
import pandas as pd
import objaverse

# ——— Config ———
OUT_DIR = "./objaverse_bedroom_v1"
DOWNLOAD_DIR = os.path.join(OUT_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 每类最多选多少个
PER_CLASS = 20
# 并行下载使用几进程
DOWNLOAD_PROCESSES = 4

random.seed(0)

# 从 LVIS annotations 载入类别映射： category -> [uids]
lvis = objaverse.load_lvis_annotations()

# 定义我们关心的类别及 keywords
wanted = {
    "bed": ["bed"],
    "chair": ["chair"],
    "desk": ["desk"],
    "wardrobe": ["wardrobe", "armoire", "cabinet"],
    "suitcase": ["suitcase"],
    "book": ["book"],
    "laptop": ["laptop", "notebook"],
    "bottle": ["bottle"],
    "pillow": ["pillow"],
    "blanket": ["blanket", "quilt"],
    "box": ["box"],
    "shoes": ["shoe", "shoes"],
}

selected_uids = []
rows = []

print("=== Sampling UIDs per class ===")
for cls, keys in wanted.items():
    found = []
    for key in keys:
        # 如果 LVIS 有这个类别，就加进去
        if key in lvis:
            found.extend(lvis[key])
    cand_count = len(found)
    print(f"{cls:12s} candidates={cand_count}")

    if cand_count > 0:
        sample = random.sample(found, k=min(PER_CLASS, cand_count))
        for uid in sample:
            rows.append({"uid": uid, "class": cls})
        selected_uids.extend(sample)

# 保存候选 CSV
df_cand = pd.DataFrame(rows)
os.makedirs(OUT_DIR, exist_ok=True)
csv_path = os.path.join(OUT_DIR, "bedroom_candidates.csv")
df_cand.to_csv(csv_path, index=False)
print(f"Saved candidate CSV: {csv_path}")

# ——— Download Models ———
print(f"\n=== Downloading {len(selected_uids)} assets ===")
# load_objects 的标准签名是 (uids, processes, download_dir)
paths = objaverse.load_objects(selected_uids, download_processes=1)
print("Download finished")

# 保存本地路径 CSV
rows = []
for uid, local_path in paths.items():
    rows.append({"uid": uid, "local_path": local_path})

df_paths = pd.DataFrame(rows)
paths_csv = os.path.join(OUT_DIR, "bedroom_downloaded_paths.csv")
df_paths.to_csv(paths_csv, index=False)
print(f"Saved downloaded paths: {paths_csv}")


# import os
# import json
# import random
# from pathlib import Path

# import pandas as pd
# import pyarrow.parquet as pq

# OUT_DIR = Path(__file__).resolve().parent / "objaverse_bedroom"
# PARQUET_PATH = OUT_DIR / "sketchfab" / "sketchfab.parquet"

# PER_CLASS = 20
# SEED = 0
# random.seed(SEED)

# CLASSES = {
#     "bed": "bed",
#     "wardrobe": "wardrobe",
#     "desk": "desk",
#     "chair": "chair",
#     "shoes": "shoes",
#     "box": "box",
#     "suitcase": "suitcase",
#     "book": "book",
#     "laptop": "laptop",
#     "bottle": "bottle",
#     "pillow": "pillow",
#     "blanket": "blanket",
# }

# NEG_REGEX = {
#     "bed": ["flowerbed", "riverbed"],
#     "box": ["mailbox", "toolbox"],
# }

# def extract_text_and_uid(row):
#     """
#     This parquet schema doesn't have uid/name/tags.
#     We parse row['metadata'] (JSON string) to find a usable id + name/tags.
#     """
#     meta_raw = row.get("metadata", "")
#     name = ""
#     tags = ""

#     uid = None

#     if isinstance(meta_raw, str) and meta_raw:
#         try:
#             m = json.loads(meta_raw)
#         except Exception:
#             m = None

#         if isinstance(m, dict):
#             print("====== METADATA KEYS ======")
#             print(list(m.keys()))
#             print("====== METADATA SAMPLE ======")
#             print(str(m)[:1000])  # 打印前 1000 个字符
#             raise SystemExit

#         if isinstance(m, dict):
#             # Try common patterns (these may vary; we try a few)
#             # 1) Direct fields
#             name = str(m.get("name", "") or m.get("title", "") or "")
#             t = m.get("tags", "")
#             if isinstance(t, list):
#                 tags = " ".join([str(x) for x in t])
#             else:
#                 tags = str(t or "")

#             # 2) Nested sketchfab fields
#             sf = m.get("sketchfab", None)
#             if isinstance(sf, dict):
#                 name = name or str(sf.get("name", "") or sf.get("title", "") or "")
#                 t2 = sf.get("tags", "")
#                 if isinstance(t2, list):
#                     tags = tags or " ".join([str(x) for x in t2])
#                 else:
#                     tags = tags or str(t2 or "")

#             # 3) A uid may exist in metadata
#             uid = m.get("uid") or m.get("id") or m.get("object_uid")

#     # Fallback: use fileIdentifier as a stable identifier if no uid found
#     file_id = row.get("fileIdentifier", "")
#     if not uid:
#         uid = file_id

#     text = f"{name} {tags} {file_id}".lower()
#     return uid, name, tags, text


# def main():
#     PARQUETS = [
#         OUT_DIR / "sketchfab" / "sketchfab.parquet",
#         OUT_DIR / "thingiverse" / "thingiverse.parquet",
#     ]
#     dfs = []

#     for p in PARQUETS:
#         if p.exists():
#             t = pq.read_table(str(p), columns=["fileIdentifier", "source", "license", "metadata"])
#             d = t.to_pandas()
#             d["__parquet"] = str(p)
#             dfs.append(d)

#     df = pd.concat(dfs, ignore_index=True)
#     print("Total rows (merged):", len(df))

#     if not PARQUET_PATH.exists():
#         raise FileNotFoundError(f"Missing parquet: {PARQUET_PATH}")

#     # Read only existing columns (match your schema)
#     table = pq.read_table(str(PARQUET_PATH), columns=["fileIdentifier", "source", "license", "metadata"])
#     df = table.to_pandas()
#     print("Rows:", len(df))

#     # Extract search text
#     extracted = df.apply(extract_text_and_uid, axis=1, result_type="expand")
#     extracted.columns = ["uid", "name", "tags", "text"]
#     df2 = pd.concat([df[["source", "license", "fileIdentifier"]], extracted], axis=1)

#     # Quick sanity check: how many have non-empty text/name?
#     print("Non-empty name:", (df2["name"].astype(str).str.len() > 0).sum())

#     # Filter candidates per class
#     selected = []
#     for cls, kw in CLASSES.items():
#         cand = df2[df2["text"].str.contains(kw, regex=False)]
#         # negative filters
#         for neg in NEG_REGEX.get(cls, []):
#             cand = cand[~cand["text"].str.contains(neg, regex=False)]

#         print(f"{cls:10s} candidates:", len(cand))
#         if len(cand) == 0:
#             continue

#         sample = cand.sample(n=min(PER_CLASS, len(cand)), random_state=SEED).copy()
#         sample["class"] = cls
#         sample["matched_keyword"] = kw
#         selected.append(sample)

#     if not selected:
#         raise SystemExit("Still 0 matches. Next step is to inspect metadata JSON structure (print a few rows).")

#     out = pd.concat(selected, ignore_index=True)
#     out_csv = OUT_DIR / "bedroom_candidates.csv"
#     out.to_csv(out_csv, index=False)
#     print("Saved:", out_csv)


# if __name__ == "__main__":
#     main()
