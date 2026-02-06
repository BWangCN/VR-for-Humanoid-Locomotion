# Pipeline
## Download Raw Assets from Objaverse
Modify `asset_download.py`, especially change the `BIG_ASSETS` and `SMALL_ASSERS` list to your own. Also modify the `csv_path` and `paths_csv` to your local address.

This code will iterate through the large objaverse asset library and select candidate objects' uid for download. The `csv_path` stores the asset candidates' uid, and `paths_csv` stores the local download address for each candidate. After this step, you should be able to see a folder named `objaverse_bedroom`.

Run `rename_and_collect.py`, it will iterate through the uids and download the assets, and also renaming them according to their class (e.g., the asset after renaming will be bed01, bed02, etc.).

## Convert Raw Assets to USD
The raw assets from objaverse are glb files. However, Isaac Sim only supports USD format. Make sure you have Blender (4.5 recommended) downloaded on your system.

Run `blender -b -P batch_glb_to_usd_and_bbox.py -- --input ".\assets_raw" --output ".\assets_usd"`. Notice that this 'blender' command might need to be changed to `YOUR_LOCAL_PATH/blender.exe` if you are using Windows system.

This script will convert glb assets to usd format. Note that assets from Objaverse have extremely large size variations due to inconsistent unit scales. Therefore, in this code, we add a normalization step that rescales assets into a reasonable size range, using meters as the unified unit in USD. If you download or use your own assets, please make sure to also set an appropriate size range for them.

## Scene Json Generator
Before the scene is generated in isaac sim, it should be first generated as a simple json file containing the location information for each object.

`scene_json_generator.py` will choose randomly from the assets, having access to the size of their bounding boxes, and determine where to place them according to hand crafted rules. Feel free to modify the rules to create environments of your style, or call LLM API to automate this process.

## Scene Builder
Check `generator_isaac.py`. Make sure `JSON_PATH` points to the json path created. Open isaac sim, Window --> Script Editor.

Directly copy and paste generator_isaac.py into it and Run. Create --> Light --> Dome Light for better view.
