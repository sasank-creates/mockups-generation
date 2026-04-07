import os, sys, argparse, tempfile, json, hashlib, time
import numpy as np
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cup_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_cup_cache.json")
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
        "dst_in":       ["dst_in"],
        "screen":       ["screen"],
    }
    for candidate in aliases.get(name, [name]):
        if candidate in COMPOSITE_OPERATORS:
            return candidate
    return "over"


# ── Step 1: Detect Mug Body Bounds ────────────────────────────────────────
def detect_cup_bounds(product_path, fuzz_percent=15, inset=30,
                      top_crop=0.16, bottom_crop=0.03):
    """
    Detects the mug body bounding box using pixel brightness analysis.
    """
    print("  🔍  Detecting mug boundaries (numpy scan)...")

    with Image(filename=product_path) as img:
        arr = np.array(img)
        pw, ph = img.width, img.height

    brightness = arr[:, :, :3].mean(axis=2) / 255.0

    threshold = 0.97
    col_has_mug = (brightness < threshold).any(axis=0)
    row_has_mug = (brightness < threshold).any(axis=1)

    cols = np.where(col_has_mug)[0]
    rows = np.where(row_has_mug)[0]

    if len(cols) == 0 or len(rows) == 0:
        print("  ⚠️  Could not detect mug — falling back to wand trim.")
        with Image(filename=product_path) as img:
            with img.clone() as trimmed:
                trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
                tx, ty = trimmed.page_x, trimmed.page_y
                tw, th = trimmed.width, trimmed.height
        cols = [tx, tx + tw]
        rows = [ty, ty + th]

    mug_x0, mug_x1 = int(cols[0]),  int(cols[-1])
    mug_y0, mug_y1 = int(rows[0]),  int(rows[-1])
    mug_w = mug_x1 - mug_x0
    mug_h = mug_y1 - mug_y0

    print(f"  📐  Mug body: x={mug_x0}-{mug_x1} (w={mug_w}), "
          f"y={mug_y0}-{mug_y1} (h={mug_h})")

    top_margin    = int(mug_h * top_crop)
    bottom_margin = int(mug_h * bottom_crop)

    lx = mug_x0 + inset
    ly = mug_y0 + top_margin 
    lw = mug_w  - inset * 2
    lh = mug_h  - top_margin - bottom_margin

    lx = max(0, lx)
    ly = max(0, ly)
    lw = max(1, min(lw, pw - lx))
    lh = max(1, min(lh, ph - ly))

    print(f"  📐  Printable zone: x={lx} y={ly} w={lw} h={lh}")
    return lx, ly, lw, lh, pw, ph


def get_bounds_cached(product_path, fuzz_percent=15, inset=30,
                      top_crop=0.16, bottom_crop=0.03):
    cache = _load_cache()
    key   = _product_cache_key(product_path)
    entry = cache.get(key)

    if (entry
            and entry.get("inset")       == inset
            and entry.get("fuzz")        == fuzz_percent
            and entry.get("top_crop")   == top_crop
            and entry.get("bottom_crop") == bottom_crop):
        b = entry["bounds"]
        print(f"  📐  Printable zone (CACHED): x={b['lx']} y={b['ly']} "
              f"w={b['lw']} h={b['lh']}")
        return b["lx"], b["ly"], b["lw"], b["lh"], b["pw"], b["ph"]

    lx, ly, lw, lh, pw, ph = detect_cup_bounds(
        product_path, fuzz_percent, inset, top_crop, bottom_crop)

    cache[key] = {
        "product":    os.path.abspath(product_path),
        "inset":      inset,
        "fuzz":       fuzz_percent,
        "top_crop":   top_crop,
        "bottom_crop": bottom_crop,
        "bounds": {"lx": lx, "ly": ly, "lw": lw, "lh": lh,
                   "pw": pw, "ph": ph},
    }
    _save_cache(cache)
    return lx, ly, lw, lh, pw, ph


# ── Step 2: Build Cylinder Wrap Mask ──────────────────────────────────────
def create_cup_mask(product_path, lx, ly, lw, lh,
                    edge_fade=0.12, mode="full"):
    key         = _product_cache_key(product_path)
    bounds_hash = hashlib.md5(
        f"{lx},{ly},{lw},{lh},{mode},{edge_fade}".encode()).hexdigest()[:8]
    mask_path   = os.path.join(
        CACHE_DIR, f"{key}_{bounds_hash}_mask_{mode}.png")

    if _cached_file_valid(mask_path):
        print(f"  🎭  Cup mask ({mode}, CACHED)")
        return mask_path

    mask_arr = np.ones((lh, lw), dtype=np.float32)

    if mode == "portrait":
        Y, X = np.mgrid[0:lh, 0:lw]
        cx_m = lw / 2.0
        cy_m = lh / 2.0

        # We use proportional ellipse values to ensure the edges fade naturally into the mug curve.
        # This prevents the image from looking like a flat sticker at the boundaries.
        rx = lw * 0.60   
        ry = lh * 0.60   
        dist = np.sqrt(((X - cx_m) / rx) ** 2 + ((Y - cy_m) / ry) ** 2)

        # Start the fade sooner (0.75) to get a gradual, realistic blend at the edges
        fade_start = 0.75  
        fade_end   = 1.00  
        t = np.clip((dist - fade_start) / (fade_end - fade_start), 0.0, 1.0)
        mask_arr = (1.0 - t).astype(np.float32)
    else:
        fade_px = int(lw * edge_fade)
        if fade_px > 0:
            ramp = np.linspace(0.0, 1.0, fade_px, dtype=np.float32)
            mask_arr[:, :fade_px]       *= ramp[np.newaxis, :]        
            mask_arr[:, lw - fade_px:]  *= ramp[::-1][np.newaxis, :]  

    mask_uint8 = (mask_arr * 255).clip(0, 255).astype(np.uint8)
    from PIL import Image as PILImage
    pil_mask = PILImage.fromarray(mask_uint8, mode="L")
    pil_mask.save(mask_path)

    print(f"  🎭  Cup mask created ({mode}, {lw}×{lh})")
    return mask_path


# ── Step 3: Prepare Design ────────────────────────────────────────────────
def prepare_design_for_cup(design_path, lw, lh, scale=1.0,
                           shift_x=0, shift_y=0, fit="cover", vertical_stretch=1.25,
                           warp_type="perfect", warp_amount=30.0):
    label_path = os.path.join(TMP_DIR, "_cup_design.png")

    with Image(filename=design_path) as img:
        img.virtual_pixel = 'transparent'
        img.background_color = Color("transparent")
        
        ratio_w = lw / img.width
        ratio_h = lh / img.height

        if fit == "cover":
            ratio = max(ratio_w, ratio_h) * scale
            print(f"  📏  Fit: COVER  scale={ratio:.3f}")
        else:
            ratio = min(ratio_w, ratio_h) * scale
            print(f"  📏  Fit: CONTAIN  scale={ratio:.3f}")

        new_w = max(1, int(img.width  * ratio))
        # Apply vertical stretch
        new_h = max(1, int(img.height * ratio * vertical_stretch))
        img.transform(resize=f"{new_w}x{new_h}!")

        # Save offset BEFORE warp to prevent the image from "moving away" 
        # when bounding boxes change size during distortion.
        base_off_x = (lw - new_w) // 2 + shift_x
        base_off_y = (lh - new_h) // 2 + shift_y

        # Auto-scale barrel warp amounts if the user passed a large number like "30"
        bar_amt = warp_amount if warp_amount < 5.0 else warp_amount / 200.0

        # Apply warp to wrap the design
        if warp_type == "perfect":
            # PERFECT: Fixes the 'shrinking top' by ditching arc.
            # 1. Safely squeeze left/right edges for 3D depth.
            img.distort('barrel', (0.0, 0.0, 0.12, 0.88, 0.0, 0.0, 0.0, 1.0))
            # 2. Perfect downward U curve using a wave shift!
            # Because it shifts pixels straight down, the top NEVER shrinks!
            try:
                img.wave(amplitude=warp_amount, wave_length=img.width * 2)
            except:
                pass
        elif warp_type == "cylinder":
            img.distort('barrel', (0.0, 0.0, bar_amt, 1.0 - bar_amt,
                                   0.0, 0.0, 0.0, 1.0))
        elif warp_type == "arc-up":
            img.distort('arc', (warp_amount,))
        elif warp_type == "arc-down":
            img.rotate(180)
            img.distort('arc', (warp_amount,))
            img.rotate(180)
        else:
            img.distort('barrel', (0.0, 0.0, bar_amt, 1.0 - bar_amt))

        with Image(width=lw, height=lh,
                   background=Color("transparent")) as canvas:
            # We composite using the pre-warp offsets so it securely stays in the mask zone!
            canvas.composite(img, left=base_off_x, top=base_off_y,
                              operator=get_op("over"))
            canvas.save(filename=label_path)

    print(f"  🖼️   Design prepared: {new_w}×{new_h} → canvas {lw}×{lh} "
          f"(shift {shift_x},{shift_y})")
    return label_path

# ── Step 4: Apply Mask to Design ──────────────────────────────────────────
def apply_mask_to_design(design_img, mask_path):
    from PIL import Image as PILImage
    import numpy as np

    design_arr = np.array(design_img)          
    H, W       = design_arr.shape[:2]

    pil_mask = PILImage.open(mask_path).convert("L").resize((W, H))
    mask_arr = np.array(pil_mask).astype(np.float32) / 255.0  

    if design_arr.shape[2] == 4:
        alpha = design_arr[:, :, 3].astype(np.float32) / 255.0
    else:
        alpha = np.ones((H, W), dtype=np.float32)

    new_alpha = (alpha * mask_arr * 255).clip(0, 255).astype(np.uint8)

    rgba = np.dstack([design_arr[:, :, :3], new_alpha])
    pil_rgba = PILImage.fromarray(rgba, mode="RGBA")
    tmp = os.path.join(TMP_DIR, "_cup_masked_design.png")
    pil_rgba.save(tmp)

    with Image(filename=tmp) as masked:
        design_img.sequence.clear()
        
    return tmp  


# ── Step 5: Final Composite ───────────────────────────────────────────────
def composite_cup(product_path, output_path, design_path, mask_path,
                  lx, ly, lw, lh, design_opacity=0.92,
                  shadow_strength=1.0):
    from PIL import Image as PILImage
    import numpy as np

    with Image(filename=product_path) as product:
        with Image(filename=design_path) as d1:
            masked_path = apply_mask_to_design(d1, mask_path)

        with Image(filename=masked_path) as d1_masked:
            product.composite(d1_masked, left=lx, top=ly,
                               operator=get_op("multiply"))

        with Image(filename=masked_path) as d2:
            d2.evaluate("multiply", design_opacity, channel="alpha")
            product.composite(d2, left=lx, top=ly,
                               operator=get_op("over"))

        product.save(filename=output_path)

    print(f"  ✅  Composite done (multiply + over@{design_opacity:.0%})")


# ── Guide image ───────────────────────────────────────────────────────────
def save_guide(product_path, output_path, lx, ly, lw, lh):
    base, ext = os.path.splitext(output_path)
    guide = base + "_GUIDE" + (ext or ".png")
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            draw.fill_color   = Color("rgba(0,255,0,0.20)")
            draw.stroke_color = Color("lime")
            draw.stroke_width = 3
            draw.rectangle(left=lx, top=ly,
                            right=lx + lw, bottom=ly + lh)
            
            draw.stroke_color = Color("red")
            draw.stroke_width = 1
            cx_g = lx + lw // 2
            cy_g = ly + lh // 2
            draw.line((cx_g - 30, cy_g), (cx_g + 30, cy_g))
            draw.line((cx_g, cy_g - 30), (cx_g, cy_g + 30))
            draw(img)
        img.save(filename=guide)
    print(f"  🩵  Guide saved → {guide}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Cup / Mug Mockup Compositor  v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
────────
Full-wrap pattern (covers entire mug surface):
  python cup_mockup_v2.py --product blank.png --design pattern.png --mode full

Portrait / logo design (soft-feathered centre placement):
  python cup_mockup_v2.py --product blank.png --design portrait.png --mode portrait

Manual zone override:
  python cup_mockup_v2.py --product blank.png --design art.png --x 340 --y 300 --w 480 --h 650
        """,
    )

    ap.add_argument("--product",  required=True,  help="Blank mug/cup image")
    ap.add_argument("--design",   required=True,  help="Design image to apply")
    ap.add_argument("--output",   default="cup_result.png")

    ap.add_argument("--x",  type=int, default=None, help="Printable zone left edge (px)")
    ap.add_argument("--y",  type=int, default=None, help="Printable zone top edge (px)")
    ap.add_argument("--w",  type=int, default=None, help="Printable zone width (px)")
    ap.add_argument("--h",  type=int, default=None, help="Printable zone height (px)")

    ap.add_argument("--scale",   type=float, default=1.0,
                    help="Design scale: 1.0=fill zone, 0.8=80%% of zone")
    ap.add_argument("--shift-x", type=int,   default=-15,  
                    help="Horizontal shift within zone (+right, -left)")

    # ⬇️ CHANGED DEFAULT HERE FROM -50 TO -90
    ap.add_argument("--shift-y", type=int,   default=-90,
                    help="Vertical shift within zone (+down, -up)")
    
    ap.add_argument("--fit",     choices=["cover", "contain"], default="contain",
                    help="'cover'=fill & crop, 'contain'=fit & letterbox (default: contain)")

    ap.add_argument("--warp-type", choices=["perfect", "cylinder", "barrel", "arc-up", "arc-down"], default="perfect",
                    help="Distortion type. 'perfect' beautifully curves without shrinking the top. Default: perfect")
    
    # ⬇️ DEFAULT WAS ALREADY 30.0 HERE, LEAVING AS IS
    ap.add_argument("--warp-amt", type=float, default=30.0,
                    help="Amount of wrap (default 30.0 pixel sag for perfect, or 0.15 for pure cylinder/barrel).")

    ap.add_argument("--opacity", type=int,   default=92,
                    help="Design vibrancy 0-100 (default 92). "
                         "Lower=more mug shading shows through.")

    ap.add_argument("--mode", choices=["full", "portrait"], default="portrait",
                    help="Mask mode: 'portrait'=soft elliptical fade (default), "
                         "'full'=hard wrap with edge-only fade")
    ap.add_argument("--edge-fade", type=float, default=0.12,
                    help="Left/right fade fraction in 'full' mode (default 0.12)")

    ap.add_argument("--fuzz",         type=int,   default=15)
    ap.add_argument("--inset",        type=int,   default=30,
                    help="Left/right inset from mug edge (px, default 30)")
    ap.add_argument("--top-crop",     type=float, default=0.16,
                    help="Fraction of mug height to skip at top (default 0.16)")
    ap.add_argument("--bottom-crop",  type=float, default=0.03,
                    help="Fraction of mug height to skip at bottom (default 0.03)")

    ap.add_argument("--show-grid", action="store_true",
                    help="Save a guide image showing the printable zone")
    ap.add_argument("--no-cache",  action="store_true",
                    help="Bypass cached bounds detection")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} file not found: {path}")
            sys.exit(1)

    if args.no_cache:
        lx, ly, lw, lh, pw, ph = detect_cup_bounds(
            args.product, args.fuzz, args.inset,
            args.top_crop, args.bottom_crop)
    else:
        lx, ly, lw, lh, pw, ph = get_bounds_cached(
            args.product, args.fuzz, args.inset,
            args.top_crop, args.bottom_crop)

    lx = args.x if args.x is not None else lx
    ly = args.y if args.y is not None else ly
    lw = args.w if args.w is not None else lw
    lh = args.h if args.h is not None else lh
    timer.mark("Bounds detection")

    print(f"\n  Product  : {args.product}")
    print(f"  Design   : {args.design}")
    print(f"  Mode     : {args.mode}")
    print(f"  Fit      : {args.fit}")
    print(f"  Zone     : x={lx} y={ly}  {lw}×{lh} px")
    print(f"  Scale    : {args.scale:.0%}")
    print(f"  Opacity  : {args.opacity}%")
    print(f"  Cache    : {'disabled' if args.no_cache else CACHE_DIR}\n")

    if args.show_grid:
        save_guide(args.product, args.output, lx, ly, lw, lh)

    mask_path = create_cup_mask(
        args.product, lx, ly, lw, lh,
        edge_fade=args.edge_fade, mode=args.mode)
    timer.mark("Mask creation")

    design_path = prepare_design_for_cup(
        args.design, lw, lh,
        scale=args.scale, shift_x=args.shift_x, shift_y=args.shift_y,
        fit=args.fit, warp_type=args.warp_type, 
        warp_amount=args.warp_amt)
    timer.mark("Design preparation")

    composite_cup(
        args.product, args.output,
        design_path, mask_path,
        lx, ly, lw, lh,
        design_opacity=args.opacity / 100.0)
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")
    timer.report()

if __name__ == "__main__":
    main()