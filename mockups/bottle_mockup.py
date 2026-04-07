import os, sys, argparse, tempfile, json, hashlib, time
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".bottle_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_bottle_cache.json")
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
            with open(CACHE_FILE, "r") as f: return json.load(f)
        except: pass
    return {}

def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f: json.dump(cache, f, indent=2)
    except: pass

def _cached_file_valid(path):
    return path and os.path.exists(path)

# Safe operator detector for ImageMagick versions
def get_op(name):
    aliases = {
        "copy_opacity": ["copy_alpha", "copy_opacity"], 
        "over": ["over"], 
        "multiply": ["multiply"],
        "dst_in": ["dst_in"]
    }
    for candidate in aliases.get(name, [name]):
        if candidate in COMPOSITE_OPERATORS: return candidate
    return "over"


# ── Step 1: Detect Bottle Body (Expanded Canvas) ──────────────────────────
def detect_bottle_bounds(product_path, fuzz_percent=15, inset=2, top_crop=0.12, bottom_crop=0.04):
    print("  🔍  Detecting bottle boundaries...")

    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height

        with img.clone() as trimmed:
            trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
            tw = trimmed.width
            th = trimmed.height
            ty = trimmed.page_y

        # Force perfect horizontal centering
        cx = pw // 2
        lw = tw - (inset * 2)
        lx = cx - (lw // 2)

        # Extremely tight crops to maximize vertical space
        top_margin = int(th * top_crop)
        bottom_margin = int(th * bottom_crop)

        ly = ty + top_margin
        lh = th - top_margin - bottom_margin

        lx, ly = max(0, lx), max(0, ly)
        lw = max(1, min(lw, pw - lx))
        lh = max(1, min(lh, ph - ly))

        print(f"  📐  Printable area: x={lx} y={ly} w={lw} h={lh}")
        return lx, ly, lw, lh, pw, ph


def get_bounds_cached(product_path, fuzz_percent=15, inset=2, top_crop=0.12, bottom_crop=0.04):
    cache = _load_cache()
    key = _product_cache_key(product_path)
    entry = cache.get(key)
    
    if entry and entry.get("inset") == inset and entry.get("fuzz") == fuzz_percent:
        b = entry["bounds"]
        print(f"  📐  Printable area (CACHED): x={b['lx']} y={b['ly']} w={b['lw']} h={b['lh']}")
        return b['lx'], b['ly'], b['lw'], b['lh'], b['pw'], b['ph']

    lx, ly, lw, lh, pw, ph = detect_bottle_bounds(product_path, fuzz_percent, inset, top_crop, bottom_crop)

    cache[key] = {
        "product": os.path.abspath(product_path),
        "inset": inset, "fuzz": fuzz_percent,
        "bounds": {"lx": lx, "ly": ly, "lw": lw, "lh": lh, "pw": pw, "ph": ph}
    }
    _save_cache(cache)
    return lx, ly, lw, lh, pw, ph


# ── Step 2: Bottle Mask (Seamless Horizontal Cylinder Wrap) ───────────────
def create_bottle_mask(product_path, lx, ly, lw, lh):
    """
    Creates a mask that heavily fades the extreme left and right edges.
    This creates a photorealistic 3D wrap effect and hides harsh square edges.
    """
    key = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(f"{lx},{ly},{lw},{lh}".encode()).hexdigest()[:8]
    mask_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_mask.png")

    if _cached_file_valid(mask_path):
        print("  🎭  Bottle mask (CACHED)")
        return mask_path

    with Image(width=lw, height=lh, background=Color('black')) as mask:
        with Drawing() as draw:
            draw.fill_color = Color('white')
            # Top/bottom are drawn out of bounds so they don't blur.
            # Left/right are inset to allow a smooth fade.
            draw.rectangle(left=4, top=-50, right=lw-4, bottom=lh+50)
            draw(mask)

        # Smooth wrap blur
        mask.blur(radius=0, sigma=8.0)
        mask.save(filename=mask_path)

    print(f"  🎭  Bottle mask created {lw}×{lh}")
    return mask_path


# ── Step 3: Prepare Design (FIT / CONTAIN MODE) ────────────────────────────
# ── Step 3: Prepare Design (FIT / CONTAIN MODE) ────────────────────────────
def prepare_design_for_bottle(design_path, lw, lh, scale=1.0, shift_y=100):
    """
    Fits the image horizontally into the bottle wrapper without forcing it to
    stretch vertically. Maintains aspect ratio.
    """
    label_path = os.path.join(TMP_DIR, "_bottle_design.png")

    with Image(filename=design_path) as img:
        
        ratio = min((lw * scale) / img.width, (lh * scale) / img.height)
        print("  📏  Scaling mode: CONTAIN (Preserving aspect ratio, centering on canvas)")
            
        new_w = max(1, int(img.width * ratio)+110)
        new_h = max(1, int(img.height * ratio)+450)
        
        img.transform(resize=f"{new_w}x{new_h}!")
        
        # Center the resized image exactly in the middle of our transparent canvas
        with Image(width=lw, height=lh, background=Color('transparent')) as canvas:
            offset_x = (lw - new_w) // 2
            
            # 👇 ADDED shift_y HERE TO PUSH THE IMAGE DOWN 👇
            offset_y = ((lh - new_h) // 2) + shift_y 
            
            canvas.composite(img, left=offset_x, top=offset_y, operator=get_op('over'))
            canvas.save(filename=label_path)

    print(f"  🖼️   Design prepared and mapped to {lw}×{lh} (Shifted Y: {shift_y}px)")
    return label_path
# ── Step 4: Final composite (Blend smoothly) ──────────────────────────────
def apply_mask_to_design(design_img, mask_img):
    if mask_img.width != design_img.width or mask_img.height != design_img.height:
        mask_img.resize(design_img.width, design_img.height)

    # Multiply design's transparency with the cylinder mask to fade edges
    with Image(width=design_img.width, height=design_img.height, background=Color('white')) as mask_rgba:
        mask_rgba.composite_channel('alpha', mask_img, get_op('copy_opacity'), 0, 0)
        design_img.composite(mask_rgba, left=0, top=0, operator=get_op('dst_in'))


def composite_bottle(product_path, output_path, design_path, mask_path, lx, ly, lw, lh, design_opacity=0.92):
    with Image(filename=product_path) as product:
        with Image(filename=mask_path) as mask:
            
            # Layer 1: Multiply Blend (Catches the shadows of the bottle)
            with Image(filename=design_path) as design1:
                apply_mask_to_design(design1, mask)
                product.composite(design1, left=lx, top=ly, operator=get_op('multiply'))

            # Layer 2: Over Blend (Brings back vibrancy while respecting the shadows)
            with Image(filename=design_path) as design2:
                apply_mask_to_design(design2, mask)
                design2.evaluate('multiply', design_opacity, channel='alpha')
                product.composite(design2, left=lx, top=ly, operator=get_op('over'))

        product.save(filename=output_path)


# ── Guide image ───────────────────────────────────────────────────────────
def save_guide(product_path, output_path, lx, ly, lw, lh):
    base, ext = os.path.splitext(output_path)
    guide = base + '_GUIDE' + (ext or '.png')
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            draw.fill_color = Color('rgba(0,255,255,0.25)')
            draw.stroke_color = Color('cyan')
            draw.stroke_width = 2
            draw.rectangle(left=lx, top=ly, right=lx + lw, bottom=ly + lh)
            draw(img)
        img.save(filename=guide)
    print(f"  🩵  Guide saved → {guide}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Water Bottle Mockup Compositor")
    ap.add_argument("--product", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--output", default="bottle_result.png")

    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)

    # 1.0 means it matches the canvas perfectly edge-to-edge
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--opacity", type=int, default=95)
    ap.add_argument("--fuzz", type=int, default=15)
    ap.add_argument("--shift-y", type=int, default=50, help="Move image down (positive) or up (negative)")
    # Inset is basically 0 so it reaches the extreme edges
    ap.add_argument("--inset", type=int, default=2)

    ap.add_argument("--show-grid", action="store_true")
    ap.add_argument("--no-cache", action="store_true")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} not found: {path}")
            sys.exit(1)

    # ── 1. Detect bounds
    if args.no_cache:
        lx, ly, lw, lh, pw, ph = detect_bottle_bounds(args.product, args.fuzz, args.inset)
    else:
        lx, ly, lw, lh, pw, ph = get_bounds_cached(args.product, args.fuzz, args.inset)

    lx = args.x if args.x is not None else lx
    ly = args.y if args.y is not None else ly
    lw = args.w if args.w is not None else lw
    lh = args.h if args.h is not None else lh

    timer.mark("Bounds detection")

    print(f"\n  Product  : {args.product}")
    print(f"  Design   : {args.design}")
    print(f"  Zone     : x={lx} y={ly}  {lw}×{lh} px")
    print(f"  Scale    : {args.scale:.0%}")
    print(f"  Cache    : {'disabled' if args.no_cache else CACHE_DIR}\n")

    if args.show_grid:
        save_guide(args.product, args.output, lx, ly, lw, lh)

    # ── 2. Bottle mask
    mask_path = create_bottle_mask(args.product, lx, ly, lw, lh)
    timer.mark("Bottle mask")

    # ── 3. Design prep
    design_path = prepare_design_for_bottle(args.design, lw, lh, scale=args.scale, shift_y=args.shift_y)
    timer.mark("Design preparation")

    # ── 4. Composite
    composite_bottle(args.product, args.output, design_path, mask_path, lx, ly, lw, lh, design_opacity=args.opacity/100.0)
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")

    timer.report()

if __name__ == "__main__":
    main()