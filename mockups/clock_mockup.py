import os, sys, argparse, tempfile, json, hashlib, time
import numpy as np
from PIL import Image as PILImage
from scipy.ndimage import binary_dilation, generate_binary_structure
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".clock_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_clock_cache.json")
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
        except:
            pass
    return {}

def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except:
        pass

def _cached_file_valid(path):
    return path and os.path.exists(path)

def get_op(name):
    aliases = {
        "copy_opacity": ["copy_alpha", "copy_opacity"],
        "over":         ["over"],
        "multiply":     ["multiply"],
        "screen":       ["screen"],
        "dst_in":       ["dst_in"],
    }
    for candidate in aliases.get(name, [name]):
        if candidate in COMPOSITE_OPERATORS:
            return candidate
    return "over"


# ── Step 1: Detect Clock Inner Face ───────────────────────────────────────
def detect_clock_bounds(product_path, fuzz_percent=5, inset_ratio=0.045):
    """Trim the white background, find the clock frame, return center + inner radius."""
    print("  🔍  Detecting clock face boundaries...")
    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height
        with img.clone() as trimmed:
            # Force flattening to white so transparent PNGs behave exactly the same 
            # as JPGs across Windows and Linux
            trimmed.background_color = Color("white")
            trimmed.alpha_channel = 'remove' 
            
            trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
            out_x, out_y = trimmed.page_x, trimmed.page_y
            out_w, out_h = trimmed.width, trimmed.height

    cx = out_x + out_w // 2
    cy = out_y + out_h // 2
    outer_radius = min(out_w, out_h) / 2
    inner_radius = int(outer_radius - outer_radius * inset_ratio)

    print(f"  📐  Clock center: x={cx} y={cy} | Inner Radius: {inner_radius}px")
    return cx, cy, inner_radius, pw, ph


def get_bounds_cached(product_path, fuzz_percent=5, inset_ratio=0.045):
    cache = _load_cache()
    key = _product_cache_key(product_path)
    entry = cache.get(key)

    if entry and entry.get("inset_ratio") == inset_ratio and entry.get("fuzz") == fuzz_percent:
        b = entry["bounds"]
        print(f"  📐  Clock bounds (CACHED): center x={b['cx']} y={b['cy']} | r={b['radius']}")
        return b["cx"], b["cy"], b["radius"], b["pw"], b["ph"]

    cx, cy, radius, pw, ph = detect_clock_bounds(product_path, fuzz_percent, inset_ratio)
    cache[key] = {
        "product": os.path.abspath(product_path),
        "inset_ratio": inset_ratio, "fuzz": fuzz_percent,
        "bounds": {"cx": cx, "cy": cy, "radius": radius, "pw": pw, "ph": ph},
    }
    _save_cache(cache)
    return cx, cy, radius, pw, ph


# ── Step 2: Circular Mask ─────────────────────────────────────────────────
def create_clock_mask(product_path, cx, cy, radius, use_cache=True):
    """White circle on transparent target-size canvas = printable face area."""
    key = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(f"{cx},{cy},{radius}_transparent_v3_local".encode()).hexdigest()[:8]
    mask_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_mask.png")

    if use_cache and _cached_file_valid(mask_path):
        print("  🎭  Clock circle mask (CACHED)")
        return mask_path

    target_size = radius * 2
    with Image(width=target_size, height=target_size, background=Color("transparent")) as mask:
        with Drawing() as draw:
            draw.fill_color = Color("white")
            draw.circle((radius, radius), (radius, radius + radius))
            draw(mask)
        mask.blur(radius=0, sigma=1.0)
        mask.save(filename=mask_path)

    print(f"  🎭  Circular mask created (r={radius}px)")
    return mask_path


# ── Step 3: Prepare Design Layer ──────────────────────────────────────────
def prepare_design_layer(design_path, mask_path, radius,
                          scale=1.0, shift_x=0, shift_y=0, shrink_w=190, shrink_h=60):
    """Scale and clip design to the clock circle; everything outside = pure white."""
    layer_path = os.path.join(TMP_DIR, "_clock_design_layer.png")
    target_size = radius * 2

    with Image(filename=design_path) as img:
        ratio = max(target_size / img.width, target_size / img.height) * scale
        new_w = max(1, int(img.width * ratio) - shrink_w)
        new_h = max(1, int(img.height * ratio) - shrink_h)
        img.transform(resize=f"{new_w}x{new_h}!")

        off_x = radius - new_w // 2 + shift_x
        off_y = radius - new_h // 2 + shift_y

        with Image(width=target_size, height=target_size, background=Color("transparent")) as canvas:
            canvas.composite(img, left=off_x, top=off_y, operator=get_op("over"))
            with Image(filename=mask_path) as mask:
                canvas.composite(mask, left=0, top=0, operator=get_op("dst_in"))
            with Image(width=target_size, height=target_size, background=Color("white")) as final:
                final.composite(canvas, left=0, top=0, operator=get_op("over"))
                final.save(filename=layer_path)

    print("  🖼️   Design clipped to circle.")
    return layer_path


# ── Step 4: Build Hand Overlay ────────────────────────────────────────────
def build_hand_overlay(product_path, cx, cy, radius,
                        hand_threshold=0.90, hand_darkness=30,
                        fringe_darkness=160, dilation_px=3,
                        inner_ratio=0.80):
    """
    Builds the hand overlay mapping based on brightness exclusively targeting the local inner circle.
    """
    overlay_path = os.path.join(TMP_DIR, "_clock_hand_overlay.png")

    pil_img = PILImage.open(product_path).convert("RGB")
    arr = np.array(pil_img)
    H_full, W_full = arr.shape[:2]
    
    top = max(0, cy - radius)
    bottom = min(H_full, cy + radius)
    left = max(0, cx - radius)
    right = min(W_full, cx + radius)
    
    local_arr = arr[top:bottom, left:right]
    
    brightness = local_arr.mean(axis=2) / 255.0
    H, W = brightness.shape

    Y, X = np.mgrid[0:H, 0:W]
    local_cx = cx - left
    local_cy = cy - top
    
    inner_r = int(radius * inner_ratio)
    inner_circle = (X - local_cx) ** 2 + (Y - local_cy) ** 2 < inner_r ** 2

    # Detect hand shadow pixels
    hand_shadow = inner_circle & (brightness < hand_threshold)
    n_hand = int(hand_shadow.sum())
    print(f"  🕐  Hand shadow pixels detected: {n_hand}")

    if n_hand == 0:
        print("  ⚠️   No hand shadows detected — skipping hand overlay.")
        return None

    # Dilate to thicken thin hand lines
    struct = generate_binary_structure(2, 1)
    dilated = binary_dilation(hand_shadow, structure=struct, iterations=dilation_px)

    # Build multiply layer (white = no-op, dark = darkens)
    overlay = np.ones((H, W, 3), dtype=np.uint8) * 255

    core_y, core_x = np.where(hand_shadow)
    for y_c, x_c in zip(core_y, core_x):
        overlay[y_c, x_c] = [hand_darkness, hand_darkness, hand_darkness]

    fringe_y, fringe_x = np.where(dilated & ~hand_shadow)
    for y_c, x_c in zip(fringe_y, fringe_x):
        overlay[y_c, x_c] = [fringe_darkness, fringe_darkness, fringe_darkness]

    pil_overlay = PILImage.fromarray(overlay)
    pil_overlay.save(overlay_path)
    print(f"  🕐  Hand overlay built ({n_hand} core + {len(fringe_y)} fringe pixels).")
    return overlay_path


# ── Step 5: Final Composite ───────────────────────────────────────────────
def composite_clock(product_path, output_path, design_layer_path, cx, cy, radius,
                    hand_overlay_path=None, opacity=100):
    """
    Three-pass compositing:
      Pass 1 — Multiply design layer onto blank clock  → design appears on face
      Pass 2 — Multiply original clock on top          → black frame restored
      Pass 3 — Multiply hand overlay on top            → hands made visible
    """
    with Image(filename=product_path) as result:
        
        left = cx - radius
        top = cy - radius

        # Pass 1: map design onto face
        with Image(filename=design_layer_path) as dl:
            if opacity < 100:
                with Image(width=dl.width, height=dl.height,
                           background=Color("white")) as fade:
                    dl.evaluate("multiply", opacity / 100.0, channel="alpha")
                    fade.composite(dl, left=0, top=0, operator="over")
                    result.composite(fade, left=left, top=top, operator=get_op("multiply"))
            else:
                result.composite(dl, left=left, top=top, operator=get_op("multiply"))

        # Pass 2: restore frame and overall clock structure
        with Image(filename=product_path) as orig:
            result.composite(orig, left=0, top=0, operator=get_op("multiply"))

        # Pass 3: darken hand silhouettes so they're visible over the design
        if hand_overlay_path and os.path.exists(hand_overlay_path):
            with Image(filename=hand_overlay_path) as ho:
                result.composite(ho, left=left, top=top, operator=get_op("multiply"))
            print("  ✅  Hand overlay applied.")
        else:
            print("  ℹ️   No hand overlay (clock may have naturally dark hands).")

        result.save(filename=output_path)


# ── Guide image ───────────────────────────────────────────────────────────
def save_guide(product_path, output_path, cx, cy, radius):
    base, ext = os.path.splitext(output_path)
    guide = base + "_GUIDE" + (ext or ".png")
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            draw.fill_color = Color("rgba(0,255,0,0.15)")
            draw.stroke_color = Color("lime")
            draw.stroke_width = 2
            draw.circle((cx, cy), (cx, cy + radius))
            draw.stroke_color = Color("red")
            draw.stroke_width = 1
            draw.line((cx - 20, cy), (cx + 20, cy))
            draw.line((cx, cy - 20), (cx, cy + 20))
            draw(img)
        img.save(filename=guide)
    print(f"  🩵  Guide saved → {guide}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Wall Clock Mockup Compositor — with automatic hand restoration"
    )
    ap.add_argument("--product", required=True, help="Blank clock image")
    ap.add_argument("--design",  required=True, help="Design to place on the face")
    ap.add_argument("--output",  default="clock_result.png")

    # Placement
    ap.add_argument("--scale",   type=float, default=1.0,  help="Design scale inside clock face")
    ap.add_argument("--shift-x", type=int,   default=0,    help="Horizontal shift (px, +right)")
    ap.add_argument("--shift-y", type=int,   default=0,    help="Vertical shift (px, +down)")
    
    # Size tuning
    ap.add_argument("--shrink-w", type=int,  default=190,  help="Horizontal width edge shrink per ratio (px)")
    ap.add_argument("--shrink-h", type=int,  default=60,   help="Vertical height edge shrink per ratio (px)")

    # Opacity
    ap.add_argument("--opacity", type=int,   default=100,  help="Design opacity 0-100")

    # Detection tuning
    ap.add_argument("--fuzz",    type=int,   default=5,    help="Background trim sensitivity")
    ap.add_argument("--inset",   type=float, default=0.045,help="Frame thickness ratio")

    # Hand overlay tuning
    ap.add_argument("--hand-threshold",  type=float, default=0.90,
                    help="Brightness threshold for hand detection (default 0.90). "
                         "Lower = only detect darker shadows; raise if hands are missed.")
    ap.add_argument("--hand-darkness",   type=int,   default=30,
                    help="Core hand pixel darkness 0-255 in multiply layer (default 30). "
                         "Lower = darker hands.")
    ap.add_argument("--hand-fringe",     type=int,   default=160,
                    help="Edge fringe darkness 0-255 (default 160, soft edge).")
    ap.add_argument("--hand-dilation",   type=int,   default=3,
                    help="Pixels to dilate hand edges (default 3, thickens thin hands).")
    ap.add_argument("--no-hand-overlay", action="store_true",
                    help="Skip hand overlay (use if clock already has dark/coloured hands).")

    ap.add_argument("--show-grid", action="store_true")
    ap.add_argument("--no-cache",  action="store_true")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} file not found: {path}")
            sys.exit(1)

    # 1. Detect bounds
    if args.no_cache:
        cx, cy, radius, pw, ph = detect_clock_bounds(args.product, args.fuzz, args.inset)
    else:
        cx, cy, radius, pw, ph = get_bounds_cached(args.product, args.fuzz, args.inset)
    timer.mark("Bounds detection")

    print(f"\n  Product   : {args.product}")
    print(f"  Design    : {args.design}")
    print(f"  Output    : {args.output}")
    print(f"  Scale     : {args.scale:.0%}")
    print(f"  Cache     : {'disabled' if args.no_cache else CACHE_DIR}\n")

    if args.show_grid:
        save_guide(args.product, args.output, cx, cy, radius)

    # 2. Circular mask
    mask_path = create_clock_mask(args.product, cx, cy, radius, use_cache=not args.no_cache)
    timer.mark("Clock mask")

    # 3. Design layer
    layer_path = prepare_design_layer(
        args.design, mask_path, radius,
        args.scale, args.shift_x, args.shift_y,
        args.shrink_w, args.shrink_h
    )
    timer.mark("Design preparation")

    # 4. Hand overlay
    hand_overlay_path = None
    if not args.no_hand_overlay:
        hand_overlay_path = build_hand_overlay(
            args.product, cx, cy, radius,
            hand_threshold=args.hand_threshold,
            hand_darkness=args.hand_darkness,
            fringe_darkness=args.hand_fringe,
            dilation_px=args.hand_dilation,
        )
    timer.mark("Hand overlay")

    # 5. Final composite
    composite_clock(
        args.product, args.output, layer_path, cx, cy, radius,
        hand_overlay_path=hand_overlay_path,
        opacity=args.opacity
    )
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")
    timer.report()


if __name__ == "__main__":
    main()