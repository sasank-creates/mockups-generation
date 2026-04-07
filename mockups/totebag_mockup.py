import os, sys, argparse, tempfile, json, hashlib, time
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".tote_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_tote_cache.json")
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

# ── Step 1: Detect Tote Bag Bounds (Exact Size) ───────────────────────────
def detect_totebag_bounds(product_path, inset=2, fuzz_percent=5):
    print("  🔍  Detecting tote bag boundaries...")

    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height

        with img.clone() as trimmed:
            trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
            trim_x = trimmed.page_x
            trim_y = trimmed.page_y
            trim_w = trimmed.width
            trim_h = trimmed.height

        # TOTE BAG HEURISTIC: 
        # The trim includes the handles at the top. 
        # The body of the bag is almost perfectly square. By making the height
        # equal to the width and pinning it to the bottom, we get the exact bag body.
        bag_body_w = trim_w
        bag_body_h = min(trim_w, trim_h * 0.85) # Ensures we don't go too high into handles
        
        # Calculate X and Y to target the bottom square part of the image
        bag_x = trim_x
        bag_y = (trim_y + trim_h) - bag_body_h

        lx = bag_x + inset
        ly = bag_y + inset
        lw = bag_body_w - (inset * 2)
        lh = bag_body_h - (inset * 2)

        lx = max(0, lx)
        ly = max(0, int(ly))
        lw = max(1, min(int(lw), pw - lx))
        lh = max(1, min(int(lh), ph - ly))

        print(f"  📐  Tote bounds: x={lx} y={ly} w={lw} h={lh}  (product: {pw}×{ph})")
        return lx, ly, lw, lh, pw, ph

def get_bounds_cached(product_path, inset=2, fuzz_percent=5):
    cache = _load_cache()
    key = _product_cache_key(product_path)

    entry = cache.get(key)
    if entry and entry.get("inset") == inset and entry.get("fuzz") == fuzz_percent:
        b = entry["bounds"]
        lx, ly, lw, lh, pw, ph = b["lx"], b["ly"], b["lw"], b["lh"], b["pw"], b["ph"]
        print(f"  📐  Tote bounds (CACHED): x={lx} y={ly} w={lw} h={lh} (product: {pw}×{ph})")
        return lx, ly, lw, lh, pw, ph

    lx, ly, lw, lh, pw, ph = detect_totebag_bounds(product_path, inset, fuzz_percent)

    cache[key] = {
        "product": os.path.abspath(product_path),
        "inset": inset,
        "fuzz": fuzz_percent,
        "bounds": {"lx": lx, "ly": ly, "lw": lw, "lh": lh, "pw": pw, "ph": ph},
    }
    _save_cache(cache)
    return lx, ly, lw, lh, pw, ph

# ── Step 2: Tote Bag Mask (Exact Edges) ───────────────────────────────────
def create_totebag_mask(product_path, lx, ly, lw, lh, pw, ph):
    """
    Create a soft rounded rectangle mask for a flat tote bag.
    """
    key = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(f"{lx},{ly},{lw},{lh}".encode()).hexdigest()[:8]
    mask_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_mask.png")

    if _cached_file_valid(mask_path):
        print("  🎭  Tote mask (CACHED)")
        return mask_path

    with Image(width=lw, height=lh, background=Color('white')) as mask:
        with Drawing() as draw:
            draw.fill_color = Color('white')
            # 2% rounded corners to match the sharp exact corners of the bag
            corner_radius = min(lw, lh) * 0.02 
            draw.rectangle(left=0, top=0, right=lw, bottom=lh, radius=corner_radius)
            draw(mask)

        # Very tight feathering so the design doesn't bleed outside the exact bounds
        blur_sigma = max(1, min(lw, lh) * 0.005) 
        mask.blur(radius=0, sigma=blur_sigma)

        mask.save(filename=mask_path)

    print(f"  🎭  Tote mask created {lw}×{lh} (blur σ={blur_sigma:.1f}, cached)")
    return mask_path

# ── Step 3: Prepare design ────────────────────────────────────────────────
def prepare_design_for_tote(design_path, lw, lh, scale=0.90, opacity=100):
    label_path = os.path.join(TMP_DIR, "_tote_design.png")
    padding_ratio = 0.05 # Reduced padding since scale is higher

    target_w = max(1, int(lw * (scale + padding_ratio)))
    target_h = max(1, int(lh * (scale + padding_ratio)))

    with Image(filename=design_path) as img:
        img.transform(resize=f"{target_w}x{target_h}")
        fitted_w, fitted_h = img.width, img.height

        offset_x = (lw - img.width) // 2
        offset_y = (lh - img.height) // 2

        with Image(width=lw, height=lh, background=Color('transparent')) as canvas:
            canvas.composite(img, left=offset_x, top=offset_y, operator='over')

            if opacity < 100:
                canvas.evaluate('multiply', opacity / 100.0, channel='alpha')

            canvas.save(filename=label_path)

    print(f"  🖼️   Design: {fitted_w}×{fitted_h} → centered on {lw}×{lh} (scale={scale:.0%})")
    return label_path

# ── Step 4: Final composite ───────────────────────────────────────────────
def apply_mask_to_design(design_img, mask_img):
    if mask_img.width != design_img.width or mask_img.height != design_img.height:
        mask_img.resize(design_img.width, design_img.height)

    copy_op = get_op("copy_opacity")
    with Image(width=design_img.width, height=design_img.height, background=Color('white')) as mask_rgba:
        mask_rgba.composite_channel('alpha', mask_img, copy_op, left=0, top=0)
        design_img.composite(mask_rgba, left=0, top=0, operator=get_op('multiply'))

def composite_totebag(product_path, output_path, design_label_path, mask_path, lx, ly, lw, lh, blend_mode='multiply', design_opacity=0.92):
    multiply_op = get_op(blend_mode)

    with Image(filename=product_path) as product:
        with Image(filename=mask_path) as mask:

            # ── Layer 1: Multiply blend for shadows & creases ──────────
            with Image(filename=design_label_path) as design1:
                with mask.clone() as m1:
                    apply_mask_to_design(design1, m1)
                product.composite(design1, left=lx, top=ly, operator=multiply_op)

            # ── Layer 2: Over blend for color vibrancy ─────────────────
            with Image(filename=design_label_path) as design2:
                with mask.clone() as m2:
                    apply_mask_to_design(design2, m2)
                # Lower opacity for the "over" layer to let shadows show through
                design2.evaluate('multiply', design_opacity * 0.35, channel='alpha')
                product.composite(design2, left=lx, top=ly, operator='over')

        product.save(filename=output_path)

# ── Guide image ───────────────────────────────────────────────────────────
def save_guide(product_path, output_path, lx, ly, lw, lh):
    base, ext = os.path.splitext(output_path)
    guide = base + '_GUIDE' + (ext or '.png')
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            draw.fill_color = Color('rgba(0,255,0,0.15)')
            draw.stroke_color = Color('green')
            draw.stroke_width = 2
            
            radius = min(lw, lh) * 0.02
            draw.rectangle(left=lx, top=ly, right=lx+lw, bottom=ly+lh, radius=radius)
            draw(img)
        img.save(filename=guide)
    print(f"  🟢  Guide saved → {guide}")

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Tote Bag mockup compositor")
    ap.add_argument("--product", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--output", default="tote_result.png")

    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)

    # Scale increased to 90% so your design can stretch across the exact bounds
    ap.add_argument("--scale", type=float, default=0.90) 
    ap.add_argument("--blend", default="multiply", choices=["multiply", "over", "overlay"])
    ap.add_argument("--opacity", type=int, default=95)
    ap.add_argument("--fuzz", type=int, default=5)
    
    # Inset reduced to 2 pixels to perfectly align with the edges of the bag
    ap.add_argument("--inset", type=int, default=2) 
    
    ap.add_argument("--show-grid", action="store_true")
    ap.add_argument("--no-cache", action="store_true")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} not found: {path}")
            sys.exit(1)

    # ── 1. Detect bounds ──────────────────────────────────────────────
    if args.no_cache:
        lx, ly, lw, lh, pw, ph = detect_totebag_bounds(args.product, inset=args.inset, fuzz_percent=args.fuzz)
    else:
        lx, ly, lw, lh, pw, ph = get_bounds_cached(args.product, inset=args.inset, fuzz_percent=args.fuzz)

    lx = args.x if args.x is not None else lx
    ly = args.y if args.y is not None else ly
    lw = args.w if args.w is not None else lw
    lh = args.h if args.h is not None else lh

    timer.mark("Bounds detection")

    print(f"\n  Product  : {args.product}")
    print(f"  Design   : {args.design}")
    print(f"  Zone     : x={lx} y={ly}  {lw}×{lh} px")
    print(f"  Scale    : {args.scale:.0%}")
    print(f"  Blend    : {args.blend} | Opacity: {args.opacity}%")
    print(f"  Cache    : {'disabled' if args.no_cache else CACHE_DIR}\n")

    if args.show_grid:
        save_guide(args.product, args.output, lx, ly, lw, lh)

    # ── 2. Tote mask ──────────────────────────────────────────────────
    mask_path = create_totebag_mask(args.product, lx, ly, lw, lh, pw, ph)
    timer.mark("Tote mask")

    # ── 3. Design prep ────────────────────────────────────────────────
    design_path = prepare_design_for_tote(
        args.design, lw, lh,
        scale=args.scale,
        opacity=args.opacity
    )
    timer.mark("Design preparation")

    # ── 4. Composite ──────────────────────────────────────────────────
    composite_totebag(
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