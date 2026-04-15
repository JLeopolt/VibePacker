# VibePacker
A vibe-coded 2D texture packer for whatever you want, but intended for Godot.

## Purpose
This simple python script will accept a folder of images (PNG) and efficiently pack them into a single large texture sheet.
It also spits out an atlas file (JSON) which contains metadata about each texture it bundled.

## About
I tried using some free texture packer tools, but I wasn't happy with them for various reasons, so I asked an AI to save me the hassle and to just write me a script.
This was written and iterated upon by Claude, with minimal intervention.
Don't expect it to be a masterpiece, but it works well enough.
If you have issues or concerns feel free to open an Issue on GitHub, but I don't plan on actively maintaining this script much.

## Usage
You can use the script in the `uniform` mode if all your textures are the same size.
This will let you reduce the metadata in the atlas file signficantly.
Otherwise if your textures are all different sizes, use the `variable` mode.
This will include the exact x,y coordinates and width/height in pixels in the atlas for each entry.
See the `Help` section for further usage instructions.

## Help
Use the `-h` argument to see all available options.
Open the main `vibepack.py` file to read the documentation, and peruse the code as you like.

## Licensing
Don't worry about it. (MIT)
