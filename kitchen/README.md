# Full pipeline:
```
python kitchen/asset_download.py --out_dir ./kitchen/objaverse_kitchen --per_class 20

python kitchen/rename_and_collect.py

blender -b -P kitchen/batch_glb_to_usd_and_bbox.py --% -- --input ".\kitchen\kitchen_assets_raw" --output ".\kitchen\kitchen_assets_usd"

python kitchen/download_textures.py
```

# Generate scenes:
python generate_scenes.py 50 --room_type kitchen

python glb_viewer.py