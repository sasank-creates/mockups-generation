import os, sys, argparse, tempfile, json, hashlib, time
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".pillow_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_pillow_cache.json")
TMP_DIR = tempfile.gettempdir()


# ── Timing helper ──────────────────────────────────────────────────────────
class Timer:
    def __init__(self):
        self.steps = []
        self._t = time.perf_counter()

    def mark(self, label):
        now = time.perf_counter()
        elapsed = now - self._t
        self.steps.append((label, elapsed))
        self._t = now
        return elapsed

    def report(self):
        total = sum(e for _, e in self.steps)
        print("\n  ⏱️  Timing Report:")
        for label, elapsed in self.steps:
            bar = "█" * int(elapsed / total * 30) if total > 0 else ""
            print(f"      {elapsed:6.3f}s  {bar:30s}  {label}")
        print(f"      {'─' * 40}")
        print(f"      {total:6.3f}s  TOTAL\n")


# ── Cache helpers ──────────────────────────────────────────────────────────
def _product_cache_key(product_path):
    abspath = os.path.abspath(product_path)
    stat = os.stat(abspath)
    raw = f"{abspath}|{stat.st_mtime}|{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except IOError:
        pass


def _cached_file_valid(path):
    return path and os.path.exists(path)


# ── Operator lookup ────────────────────────────────────────────────────────
def get_op(name):
    aliases = {
        "copy_opacity": ["copy_alpha", "copy_opacity"],
        "over": ["over"],
        "multiply": ["multiply"],
        "screen": ["screen"],
        "overlay": ["overlay"],
        "soft_light": ["soft_light", "soft-light"],
    }
    for candidate in aliases.get(name, [name]):
        if candidate in COMPOSITE_OPERATORS:
            return candidate
    return "over"


# ── Step 1: Detect pillow bounds ──────────────────────────────────────────
def detect_pillow_bounds(product_path, inset=15, fuzz_percent=5):
    print("  🔍  Detecting pillow boundaries (trim-based)...")

    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height

        with img.clone() as trimmed:
            trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
            trim_x = trimmed.page_x
            trim_y = trimmed.page_y
            trim_w = trimmed.width
            trim_h = trimmed.height

        lx = trim_x + inset
        ly = trim_y + inset
        lw = trim_w - inset * 2
        lh = trim_h - inset * 2

        lx = max(0, lx)
        ly = max(0, ly)
        lw = max(1, min(lw, pw - lx))
        lh = max(1, min(lh, ph - ly))

        print(f"  📐  Pillow bounds: x={lx} y={ly} w={lw} h={lh}  (product: {pw}×{ph})")
        return lx, ly, lw, lh, pw, ph


def get_bounds_cached(product_path, inset=15, fuzz_percent=5):
    cache = _load_cache()
    key = _product_cache_key(product_path)

    entry = cache.get(key)
    if entry and entry.get("inset") == inset and entry.get("fuzz") == fuzz_percent:
        b = entry["bounds"]
        lx, ly, lw, lh, pw, ph = b["lx"], b["ly"], b["lw"], b["lh"], b["pw"], b["ph"]
        print(f"  📐  Pillow bounds (CACHED): x={lx} y={ly} w={lw} h={lh}  "
              f"(product: {pw}×{ph})")
        return lx, ly, lw, lh, pw, ph

    lx, ly, lw, lh, pw, ph = detect_pillow_bounds(product_path, inset, fuzz_percent)

    cache[key] = {
        "product": os.path.abspath(product_path),
        "inset": inset,
        "fuzz": fuzz_percent,
        "bounds": {"lx": lx, "ly": ly, "lw": lw, "lh": lh, "pw": pw, "ph": ph},
    }
    _save_cache(cache)
    return lx, ly, lw, lh, pw, ph


# ── Step 2: Pillow mask ──────────────────────────────────────────────────
#
#  KEY FIX: Create the mask at LABEL size (lw × lh) directly,
#  not at full product size. This avoids crop misalignment and
#  ensures the ellipse fills the mask correctly.
#  The level() call is applied ONLY to the white ellipse interior
#  by doing blur first, then clamping — the outside stays at zero.
#
def create_pillow_mask(product_path, lx, ly, lw, lh, pw, ph):
    """
    Create a soft elliptical mask at exactly lw × lh.
    White = fully visible, Black = fully hidden.
    No full-product-size canvas needed — eliminates crop alignment bugs.
    """
    key = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(f"{lx},{ly},{lw},{lh}".encode()).hexdigest()[:8]
    mask_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_mask.png")

    if _cached_file_valid(mask_path):
        print("  🎭  Pillow mask (CACHED)")
        return mask_path

    with Image(width=lw, height=lh, background=Color('white')) as mask:
        with Drawing() as draw:
            # Ellipse centered in the mask canvas
            cx = lw // 2
            cy = lh // 2
            rx = lw // 2
            ry = lh // 2
            draw.fill_color = Color('white')
            draw.ellipse((cx, cy), (rx, ry))
            draw(mask)

        # Feather the edges with gaussian blur
        blur_sigma = min(lw, lh) * 0.04
        blur_sigma = max(5, min(blur_sigma, 40))
        mask.blur(radius=0, sigma=blur_sigma)

        # Boost contrast: make center fully white, edges fade to black
        # black=0.05 means anything below 5% gray becomes pure black
        # white=0.80 means anything above 80% gray becomes pure white
        mask.level(black=0.05, white=0.80)

        mask.save(filename=mask_path)

    print(f"  🎭  Pillow mask created {lw}×{lh} (blur σ={blur_sigma:.1f}, cached)")
    return mask_path


# ── Step 3: Displacement map (cached) ─────────────────────────────────────
def create_displacement_map(product_path, lx, ly, lw, lh):
    key = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(f"{lx},{ly},{lw},{lh}".encode()).hexdigest()[:8]
    disp_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_displacement.png")

    if _cached_file_valid(disp_path):
        print("  🌊  Displacement map (CACHED)")
        return disp_path

    with Image(filename=product_path) as img:
        img.crop(left=lx, top=ly, width=lw, height=lh)
        img.type = 'grayscale'
        img.normalize()
        img.blur(radius=0, sigma=3)
        img.negate()
        img.level(black=0.35, white=0.65)
        img.save(filename=disp_path)

    print("  🌊  Displacement map created (and cached)")
    return disp_path


# ── Step 4: Prepare design ────────────────────────────────────────────────
def prepare_design_for_pillow(design_path, lw, lh, scale=0.85,
                               barrel_amount=0.08, opacity=100):
    label_path = os.path.join(TMP_DIR, "_pillow_design.png")

    padding_ratio = 0.15  # 15% extra size

    target_w = max(1, int(lw * (scale + padding_ratio)))
    target_h = max(1, int(lh * (scale + padding_ratio)))

    with Image(filename=design_path) as img:
        img.transform(resize=f"{target_w}x{target_h}")
        fitted_w, fitted_h = img.width, img.height

        if barrel_amount > 0:
            img.virtual_pixel = 'transparent'
            img.distort('barrel', (barrel_amount, 0, 0, 1.0 - barrel_amount))

        offset_x = (lw - img.width) // 2
        offset_y = (lh - img.height) // 2

        with Image(width=lw, height=lh,
                   background=Color('transparent')) as canvas:
            canvas.composite(img, left=offset_x, top=offset_y, operator='over')

            if opacity < 100:
                canvas.evaluate('multiply', opacity / 100.0, channel='alpha')

            canvas.save(filename=label_path)

    print(f"  🖼️   Design: {fitted_w}×{fitted_h} → centered on {lw}×{lh} "
          f"(scale={scale:.0%}, barrel={barrel_amount})")
    return label_path


# ── Step 5: Final composite ───────────────────────────────────────────────
#
#  KEY FIX: The mask is now lw × lh already — no crop needed.
#  We apply it directly to the design's alpha channel using multiply
#  on the alpha channel (not copy_opacity which replaces entirely).
#
def apply_mask_to_design(design_img, mask_img):
    """
    Multiply the design's existing alpha by the mask luminance.
    This preserves transparent areas in the design while adding
    the elliptical feather from the mask.
    """
    # Ensure mask matches design dimensions
    if mask_img.width != design_img.width or mask_img.height != design_img.height:
        mask_img.resize(design_img.width, design_img.height)

    # Extract alpha from design, multiply by mask, put back
    # Using composite with 'multiply' on just the alpha channel
    copy_op = get_op("copy_opacity")

    # Method: composite the grayscale mask into the alpha channel
    # copy_opacity/copy_alpha sets alpha = mask luminance
    # But we want alpha = existing_alpha × mask_luminance
    #
    # Strategy: use 'multiply' blend on the whole image with a
    # version of the mask that is white RGB + mask-as-alpha
    # This multiplies RGB by 1.0 (no change) and alpha by mask value

    with Image(width=design_img.width, height=design_img.height,
               background=Color('white')) as mask_rgba:
        # Set the alpha channel of this white image to the mask values
        mask_rgba.composite_channel('alpha', mask_img, copy_op, left=0, top=0)
        # Now multiply: RGB stays same (white×design = design), alpha gets multiplied
        design_img.composite(mask_rgba, left=0, top=0, operator=get_op('multiply'))


def composite_pillow(product_path, output_path, design_label_path,
                     mask_path, lx, ly, lw, lh, blend_mode='multiply',
                     design_opacity=0.92):
    """
    Two-layer composite:
      Layer 1: multiply blend → design takes on fabric texture
      Layer 2: over blend at low opacity → restores color vibrancy
    """
    multiply_op = get_op(blend_mode)

    with Image(filename=product_path) as product:
        with Image(filename=mask_path) as mask:

            # ── Layer 1: Multiply blend for texture ───────────────────
            with Image(filename=design_label_path) as design1:
                with mask.clone() as m1:
                    apply_mask_to_design(design1, m1)
                product.composite(design1, left=lx, top=ly, operator=multiply_op)

            # ── Layer 2: Over blend for color vibrancy ────────────────
            with Image(filename=design_label_path) as design2:
                with mask.clone() as m2:
                    apply_mask_to_design(design2, m2)
                # Reduce opacity for this layer
                design2.evaluate('multiply', design_opacity * 0.45, channel='alpha')
                product.composite(design2, left=lx, top=ly, operator='over')

        product.save(filename=output_path)


# ── Guide image ───────────────────────────────────────────────────────────
def save_guide(product_path, output_path, lx, ly, lw, lh):
    base, ext = os.path.splitext(output_path)
    guide = base + '_GUIDE' + (ext or '.png')
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            draw.fill_color = Color('rgba(255,0,0,0.15)')
            draw.stroke_color = Color('red')
            draw.stroke_width = 3
            cx = lx + lw // 2
            cy = ly + lh // 2
            draw.ellipse((cx, cy), (lw // 2, lh // 2))

            draw.fill_color = Color('none')
            draw.stroke_color = Color('blue')
            draw.stroke_width = 2
            draw.rectangle(left=lx, top=ly, right=lx + lw, bottom=ly + lh)
            draw(img)
        img.save(filename=guide)
    print(f"  🔴  Guide saved → {guide}")


# ── Debug: save mask preview ──────────────────────────────────────────────
def save_mask_preview(mask_path, output_path, lx, ly, pw, ph):
    """Save a visual preview showing the mask overlaid on a gray background."""
    base, ext = os.path.splitext(output_path)
    preview = base + '_MASK_PREVIEW' + (ext or '.png')

    with Image(width=pw, height=ph, background=Color('gray50')) as canvas:
        with Image(filename=mask_path) as mask:
            canvas.composite(mask, left=lx, top=ly, operator='over')
        canvas.save(filename=preview)
    print(f"  🎭  Mask preview → {preview}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Pillow mockup compositor — 3D warping + fabric blending"
    )
    ap.add_argument("--product", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--output", default="pillow_result.png")

    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)

    ap.add_argument("--scale", type=float, default=0.80)
    ap.add_argument("--barrel", type=float, default=0.06)
    ap.add_argument("--blend", default="multiply",
                    choices=["multiply", "over", "overlay", "multiply"])
    ap.add_argument("--opacity", type=int, default=92)
    ap.add_argument("--fuzz", type=int, default=5)
    ap.add_argument("--inset", type=int, default=20)
    ap.add_argument("--show-grid", action="store_true")
    ap.add_argument("--show-mask", action="store_true",
                    help="Save mask preview image for debugging")
    ap.add_argument("--no-cache", action="store_true")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} not found: {path}")
            sys.exit(1)

    # ── 1. Detect bounds ──────────────────────────────────────────────
    if args.no_cache:
        lx, ly, lw, lh, pw, ph = detect_pillow_bounds(
            args.product, inset=args.inset, fuzz_percent=args.fuzz
        )
    else:
        lx, ly, lw, lh, pw, ph = get_bounds_cached(
            args.product, inset=args.inset, fuzz_percent=args.fuzz
        )

    lx = args.x if args.x is not None else lx
    ly = args.y if args.y is not None else ly
    lw = args.w if args.w is not None else lw
    lh = args.h if args.h is not None else lh

    timer.mark("Bounds detection")

    print(f"\n  Product  : {args.product}")
    print(f"  Design   : {args.design}")
    print(f"  Zone     : x={lx} y={ly}  {lw}×{lh} px")
    print(f"  Scale    : {args.scale:.0%} | Barrel: {args.barrel}")
    print(f"  Blend    : {args.blend} | Opacity: {args.opacity}%")
    print(f"  Cache    : {'disabled' if args.no_cache else CACHE_DIR}\n")

    if args.show_grid:
        save_guide(args.product, args.output, lx, ly, lw, lh)

    # ── 2. Pillow mask (now at label size, no crop needed) ────────────
    mask_path = create_pillow_mask(args.product, lx, ly, lw, lh, pw, ph)
    timer.mark("Pillow mask")

    if args.show_mask:
        save_mask_preview(mask_path, args.output, lx, ly, pw, ph)

    # ── 3. Design prep ────────────────────────────────────────────────
    design_path = prepare_design_for_pillow(
        args.design, lw, lh,
        scale=args.scale,
        barrel_amount=args.barrel,
        opacity=args.opacity
    )
    timer.mark("Design preparation")

    # ── 4. Displacement map ───────────────────────────────────────────
    disp_path = create_displacement_map(args.product, lx, ly, lw, lh)
    timer.mark("Displacement map")

    # ── 5. Composite ──────────────────────────────────────────────────
    composite_pillow(
        args.product, args.output, design_path, mask_path,
        lx, ly, lw, lh,
        blend_mode=args.blend,
        design_opacity=args.opacity / 100.0
    )
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")

    timer.report()


if __name__ == "__main__":
    main()