"""
vibepack.py  -  Unified texture sheet packer for Godot 2D projects
========================================================================

Packs one or more folders of PNG images into a single power-of-2 texture
sheet and writes a companion JSON manifest.

Two packing modes
-----------------
  uniform   Grid-based packing. All images must be (or are resized to) the
            same tile size. JSON uses Godot-style atlas coords [col, row].
            Best for: tilesets, icon grids, uniform sprite sheets.

  variable  MaxRects bin-packing. Images may be any size. JSON stores pixel
            coords and dimensions (x, y, w, h) per entry.
            Best for: character limbs, clothing layers, mixed-size sprites.

Variant grouping
----------------
  Files whose stems share a base name and end with _<number> are grouped:
    dirt_0.png + dirt_1.png  →  "dirt": [{"index":0,...}, {"index":1,...}]
  Plain files become single-object entries.

Namespace collision handling
----------------------------
  When the same filename appears in multiple input folders, the folder name
  is prepended as a namespace:  torso.png in "player/" → "player/torso".
  Use --no-namespace to disable and keep bare filenames (last-write wins on
  collision).

Usage
-----
  python vibepack.py uniform  <dir> [dir ...]  [options]
  python vibepack.py variable <dir> [dir ...]  [options]

Common options:
  -o, --output DIR        Output directory (default: current working dir).
  -n, --name STEM         Output file stem, e.g. "terrain" → terrain.png +
                          terrain.json (default: "sheet").
  -p, --padding N         Transparent pixels around each sprite (default: 1).
      --max-size N        Maximum atlas dimension in pixels (default: 4096).
  -r, --recurse           Recurse into subdirectories of each input folder.
      --trim              Trim transparent border pixels before packing
                          (variable mode only; records trim offsets in JSON).
      --no-namespace      Disable folder-name prefixing on name collisions.

Uniform-only options:
      --tile-size N       Expected tile size in pixels (default: 32).
                          Mismatched images are resized with nearest-neighbour.

Examples
--------
  # Pack a tiles folder as a 32x32 grid
  python vibepack.py uniform ./tiles -n terrain -o ./output

  # Pack limb sprites from two folders, variable size, with padding
  python vibepack.py variable ./body ./clothing -n characters -o ./output -p 2

  # Recursively pack all PNGs under ./assets into one variable atlas
  python vibepack.py variable ./assets -r -n atlas -o ./output
"""

from __future__ import annotations

import sys
import re
import json
import math
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required.  Install with:  pip install Pillow")


# ============================================================================
# Shared utilities
# ============================================================================

VARIANT_RE = re.compile(r"^(.+?)_(\d+)$")


def next_po2(n: int) -> int:
    """Smallest power of 2 that is >= n."""
    if n <= 1:
        return 1
    return 2 ** math.ceil(math.log2(n))


def collect_paths(dirs: list[Path], recurse: bool) -> list[Path]:
    """Return a sorted, deduplicated list of PNG paths from all input dirs."""
    seen: set[Path] = set()
    paths: list[Path] = []
    for d in dirs:
        glob = d.rglob("*.png") if recurse else d.glob("*.png")
        for p in sorted(glob):
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                paths.append(rp)
    return paths


def make_name(path: Path, source_dirs: list[Path], use_namespace: bool) -> str:
    """
    Derive a sprite key from a file path.

    If use_namespace is True and the file's parent dir name is one of the
    source dirs, the dir stem is prepended: "player/torso".
    Otherwise just the file stem is used.
    """
    stem = path.stem
    if not use_namespace:
        return stem
    for d in source_dirs:
        try:
            rel = path.relative_to(d)
            parts = rel.parts[:-1]          # subdirs relative to input dir
            prefix = "/".join([d.name] + list(parts))
            return f"{prefix}/{stem}"
        except ValueError:
            continue
    return stem


def insert_variant(registry: dict, key: str, entry: dict) -> None:
    """
    Insert a variant entry (has 'index' key) or plain entry into registry,
    grouping variants into lists and sorting at the end.
    """
    m = VARIANT_RE.match(key)
    if m:
        base, idx = m.group(1), int(m.group(2))
        full_entry = {"index": idx, **entry}
        if base not in registry:
            registry[base] = []
        elif not isinstance(registry[base], list):
            # A plain entry already occupied this base name; wrap it
            registry[base] = [registry[base]]
        registry[base].append(full_entry)
    else:
        if key in registry and isinstance(registry[key], list):
            registry[key].append(entry)
        else:
            registry[key] = entry


def sort_variants(registry: dict) -> None:
    for key, val in registry.items():
        if isinstance(val, list):
            registry[key] = sorted(val, key=lambda e: e.get("index", 0))


def print_summary(registry: dict, mode: str, sheet_w: int, sheet_h: int,
                  used_px: int) -> None:
    plain   = sum(1 for v in registry.values() if not isinstance(v, list))
    groups  = sum(1 for v in registry.values() if     isinstance(v, list))
    total_v = sum(len(v) for v in registry.values() if isinstance(v, list))
    eff     = 100.0 * used_px / (sheet_w * sheet_h)
    print(f"\n{'─'*52}")
    print(f"  Mode:       {mode}")
    print(f"  Sheet:      {sheet_w}×{sheet_h} px")
    print(f"  Sprites:    {plain} plain,  {groups} variant group(s) "
          f"({total_v} variant frame(s))")
    print(f"  Efficiency: {eff:.1f}%")
    print(f"{'─'*52}")


# ============================================================================
# UNIFORM MODE  –  fixed-grid packing
# ============================================================================

def run_uniform(args: argparse.Namespace) -> None:
    tile_size   = args.tile_size
    padding     = args.padding
    source_dirs = [Path(d).resolve() for d in args.dirs]
    use_ns      = not args.no_namespace

    for d in source_dirs:
        if not d.is_dir():
            sys.exit(f"Not a directory: {d}")

    paths = collect_paths(source_dirs, args.recurse)
    if not paths:
        sys.exit("No PNG files found in the specified directories.")

    print(f"[uniform] {len(paths)} image(s) found, tile size={tile_size}px, "
          f"padding={padding}px")

    # Load & validate ---------------------------------------------------------
    images: list[tuple[str, Image.Image]] = []
    for p in paths:
        img = Image.open(p).convert("RGBA")
        name = make_name(p, source_dirs, use_ns)
        if img.size != (tile_size, tile_size):
            print(f"  WARNING: {p.name} is {img.width}x{img.height}, "
                  f"expected {tile_size}x{tile_size} - resizing.")
            img = img.resize((tile_size, tile_size), Image.NEAREST)
        images.append((name, img))
        print(f"  {name}")

    count   = len(images)
    padded  = tile_size + padding * 2

    # Calculate grid dimensions (square-ish, power-of-2 columns & rows) ------
    cols = next_po2(math.ceil(math.sqrt(count)))
    rows = next_po2(math.ceil(count / cols))
    while cols * rows < count:
        rows = next_po2(rows + 1)

    sheet_w = cols * padded
    sheet_h = rows * padded
    # Snap to power-of-2 overall dimensions
    sheet_w = next_po2(sheet_w)
    sheet_h = next_po2(sheet_h)

    print(f"\n[uniform] Sheet: {sheet_w}x{sheet_h}px  ({cols}x{rows} tiles)")

    # Render ------------------------------------------------------------------
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    registry: dict = {}
    used_px = 0

    for idx, (name, img) in enumerate(images):
        col = idx % cols
        row = idx // cols
        px  = col * padded + padding
        py  = row * padded + padding
        sheet.paste(img, (px, py))
        used_px += tile_size * tile_size
        insert_variant(registry, name, {"atlas_coords": [col, row]})

    sort_variants(registry)

    # Save --------------------------------------------------------------------
    out_dir  = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem     = args.name

    png_path  = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}.json"

    sheet.save(png_path, "PNG")
    print(f"  Saved PNG:  {png_path}")

    manifest = {
        "mode":             "uniform",
        "tile_size":        tile_size,
        "padding":          padding,
        "sheet_width_px":   sheet_w,
        "sheet_height_px":  sheet_h,
        "sheet_cols":       cols,
        "sheet_rows":       rows,
        "tiles":            registry,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Saved JSON: {json_path}")

    print_summary(registry, "uniform", sheet_w, sheet_h, used_px)


# ============================================================================
# MaxRects packer  (used by variable mode)
# ============================================================================

@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self)  -> int: return self.x + self.w
    @property
    def bottom(self) -> int: return self.y + self.h

    def contains(self, o: Rect) -> bool:
        return self.x <= o.x and self.y <= o.y and \
               self.right >= o.right and self.bottom >= o.bottom

    def intersects(self, o: Rect) -> bool:
        return not (o.right <= self.x or o.x >= self.right or
                    o.bottom <= self.y or o.y >= self.bottom)


class MaxRectsPacker:
    """
    Best-Short-Side-Fit MaxRects bin packer.
    Grows the bin in power-of-2 steps up to max_size.
    """

    def __init__(self, max_size: int = 4096, padding: int = 1):
        self.max_size  = max_size
        self.padding   = padding
        self.width     = 0
        self.height    = 0
        self._free:       list[Rect]                         = []
        self.placements:  list[tuple[str, Rect, tuple]]      = []
        # tuple: (name, inner_rect_no_padding, trim_offsets_or_None)

    # ------------------------------------------------------------------ pack
    def pack(self, sprites: list[tuple[str, Image.Image,
                                       Optional[tuple]]]) -> bool:
        """
        sprites: list of (name, pil_image, trim_offset_or_None)
        Returns True on success.
        """
        pad2    = self.padding * 2
        max_dim = max(max(img.width, img.height) + pad2 for _, img, _ in sprites)
        start   = next_po2(max_dim)
        self.width  = start
        self.height = start
        self._free  = [Rect(0, 0, self.width, self.height)]
        self.placements = []

        for name, img, trim in sprites:
            pw = img.width  + pad2
            ph = img.height + pad2
            placed = self._find(pw, ph)
            if placed is None:
                placed = self._grow_and_find(pw, ph)
                if placed is None:
                    return False
            self._place(placed)
            inner = Rect(placed.x + self.padding,
                         placed.y + self.padding,
                         img.width, img.height)
            self.placements.append((name, inner, trim))

        # Shrink height to used region (still power-of-2)
        max_y = max(r.bottom for _, r, _ in self.placements)
        self.height = next_po2(max_y)
        return True

    # --------------------------------------------------------- internal helpers
    def _find(self, w: int, h: int) -> Optional[Rect]:
        best, best_short = None, math.inf
        for fr in self._free:
            if fr.w >= w and fr.h >= h:
                short = min(fr.w - w, fr.h - h)
                if short < best_short:
                    best_short = short
                    best = Rect(fr.x, fr.y, w, h)
        return best

    def _grow_and_find(self, w: int, h: int) -> Optional[Rect]:
        candidates = []
        if self.width  < self.max_size:
            candidates.append((next_po2(self.width  + 1), self.height))
        if self.height < self.max_size:
            candidates.append((self.width, next_po2(self.height + 1)))
        if not candidates:
            return None

        # Prefer squarer results
        candidates.sort(key=lambda d: abs(d[0] - d[1]))

        for new_w, new_h in candidates:
            extra: list[Rect] = []
            if new_w > self.width:
                extra.append(Rect(self.width, 0, new_w - self.width, new_h))
            if new_h > self.height:
                extra.append(Rect(0, self.height, new_w, new_h - self.height))
            self.width, self.height = new_w, new_h
            self._free.extend(extra)
            placed = self._find(w, h)
            if placed is not None:
                return placed
        return None

    def _place(self, rect: Rect) -> None:
        new_free: list[Rect] = []
        for fr in self._free:
            if rect.intersects(fr):
                if rect.x      > fr.x:      new_free.append(Rect(fr.x,      fr.y, rect.x - fr.x,         fr.h))
                if rect.right  < fr.right:  new_free.append(Rect(rect.right, fr.y, fr.right - rect.right, fr.h))
                if rect.y      > fr.y:      new_free.append(Rect(fr.x,      fr.y, fr.w,      rect.y - fr.y))
                if rect.bottom < fr.bottom: new_free.append(Rect(fr.x, rect.bottom, fr.w, fr.bottom - rect.bottom))
            else:
                new_free.append(fr)

        # Prune rects fully contained by another
        pruned: list[Rect] = []
        for i, a in enumerate(new_free):
            if not any(j != i and b.contains(a) for j, b in enumerate(new_free)):
                pruned.append(a)
        self._free = pruned


# ============================================================================
# VARIABLE MODE  –  MaxRects packing
# ============================================================================

def run_variable(args: argparse.Namespace) -> None:
    padding     = args.padding
    source_dirs = [Path(d).resolve() for d in args.dirs]
    use_ns      = not args.no_namespace

    for d in source_dirs:
        if not d.is_dir():
            sys.exit(f"Not a directory: {d}")

    paths = collect_paths(source_dirs, args.recurse)
    if not paths:
        sys.exit("No PNG files found in the specified directories.")

    print(f"[variable] {len(paths)} image(s) found, padding={padding}px"
          + (" +trim" if args.trim else ""))

    # Load images -------------------------------------------------------------
    sprites: list[tuple[str, Image.Image, Optional[tuple]]] = []
    for p in paths:
        img  = Image.open(p).convert("RGBA")
        name = make_name(p, source_dirs, use_ns)
        trim = None
        if args.trim:
            orig_w, orig_h = img.size
            bbox = img.getbbox()
            if bbox:
                trim = bbox          # (left, top, right, bottom)
                img  = img.crop(bbox)
            else:
                trim = (0, 0, orig_w, orig_h)
        sprites.append((name, img, trim))
        size_str = f"{img.width}×{img.height}"
        trim_str = f"  [trimmed from {orig_w}×{orig_h}]" if trim and args.trim else ""
        print(f"  {name:50s}  {size_str}{trim_str}")

    # Sort largest-first for better packing -----------------------------------
    sprites.sort(key=lambda s: s[1].width * s[1].height, reverse=True)

    print(f"\n[variable] Packing (max-size={args.max_size}px) ...")
    packer = MaxRectsPacker(max_size=args.max_size, padding=padding)
    if not packer.pack(sprites):
        sys.exit("ERROR: Could not fit all sprites within the maximum atlas size. "
                 "Try --max-size with a larger value.")

    print(f"  Sheet: {packer.width}×{packer.height}px")

    # Render atlas ------------------------------------------------------------
    atlas = Image.new("RGBA", (packer.width, packer.height), (0, 0, 0, 0))
    sprite_lookup = {name: img for name, img, _ in sprites}
    for name, rect, _ in packer.placements:
        atlas.paste(sprite_lookup[name], (rect.x, rect.y))

    # Build JSON manifest -----------------------------------------------------
    registry: dict = {}
    used_px   = 0

    for name, rect, trim in packer.placements:
        used_px += rect.w * rect.h
        entry: dict = {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h}
        if trim:
            # Store trim offsets so the caller can reconstruct the original rect
            entry["trim"] = {
                "left": trim[0], "top": trim[1],
                "original_w": trim[2] - trim[0],   # won't be right – fix below
            }
            # Recalculate properly from the stored bbox
            entry["trim"] = {
                "left":       trim[0],
                "top":        trim[1],
                "original_w": sprite_lookup[name].width  + trim[0] + (trim[2] - sprite_lookup[name].width  - trim[0]),
                "original_h": sprite_lookup[name].height + trim[1] + (trim[3] - sprite_lookup[name].height - trim[1]),
            }
        insert_variant(registry, name, entry)

    sort_variants(registry)

    # Save --------------------------------------------------------------------
    out_dir  = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem     = args.name

    png_path  = out_dir / f"{stem}.png"
    json_path = out_dir / f"{stem}.json"

    atlas.save(png_path, "PNG")
    print(f"  Saved PNG:  {png_path}")

    manifest = {
        "mode":           "variable",
        "padding":        padding,
        "atlas_width":    packer.width,
        "atlas_height":   packer.height,
        "sprites":        registry,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Saved JSON: {json_path}")

    print_summary(registry, "variable", packer.width, packer.height, used_px)


# ============================================================================
# Argument parsing & entry point
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="vibepack.py",
        description="Pack PNG images into a power-of-2 texture sheet (uniform or variable).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = root.add_subparsers(dest="mode", metavar="MODE",
                              help="'uniform' (grid) or 'variable' (MaxRects)")
    sub.required = True

    # ── shared argument factory ──────────────────────────────────────────────
    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("dirs", nargs="+", metavar="DIR",
                       help="Input folder(s) containing PNG files.")
        p.add_argument("-o", "--output", default=".",  metavar="DIR",
                       help="Output directory (default: current dir).")
        p.add_argument("-n", "--name",   default="sheet", metavar="STEM",
                       help="Output file stem, e.g. 'terrain' → terrain.png + terrain.json "
                            "(default: sheet).")
        p.add_argument("-p", "--padding", type=int, default=1, metavar="N",
                       help="Transparent pixels around each sprite (default: 1).")
        p.add_argument("--max-size", type=int, default=4096, metavar="N",
                       help="Maximum atlas dimension in pixels (default: 4096).")
        p.add_argument("-r", "--recurse", action="store_true",
                       help="Recurse into subdirectories.")
        p.add_argument("--no-namespace", action="store_true",
                       help="Disable folder-name prefix on sprite keys.")

    # ── uniform subcommand ───────────────────────────────────────────────────
    p_uni = sub.add_parser(
        "uniform",
        help="Fixed-grid packing for tiles / uniform sprites.",
        description="Pack identically-sized tiles into a grid tilesheet.",
    )
    add_common(p_uni)
    p_uni.add_argument("--tile-size", type=int, default=32, metavar="N",
                       help="Expected tile size in pixels (default: 32). "
                            "Mismatched images are resized.")

    # ── variable subcommand ──────────────────────────────────────────────────
    p_var = sub.add_parser(
        "variable",
        help="MaxRects packing for variable-size sprites / limbs.",
        description="Pack variable-resolution sprites using MaxRects bin packing.",
    )
    add_common(p_var)
    p_var.add_argument("--trim", action="store_true",
                       help="Trim transparent borders before packing "
                            "(trim offsets are stored in the JSON).")

    return root


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.mode == "uniform":
        run_uniform(args)
    elif args.mode == "variable":
        run_variable(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
