import os, sys, argparse, tempfile, json, hashlib, time
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".tshirt_mockup_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "_tshirt_cache.json")
TMP_DIR = tempfile.gettempdir()


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


def get_op(name):
    aliases = {
        "copy_opacity": ["copy_alpha", "copy_opacity"],
        "dst_in": ["dst_in", "in"],
        "over": ["over"],
        "multiply": ["multiply"],
        "screen": ["screen"],
        "overlay": ["overlay"],
        "soft_light": ["soft_light", "soft-light"],
    }
    for candidate in aliases.get(name, [name]):
        if candidate in COMPOSITE_OPERATORS:
            return candidate
            
    if name == "dst_in":
        return "in" if "in" in COMPOSITE_OPERATORS else "over"
    return "over"


# ══════════════════════════════════════════════════════════════════════════
#  STEP 1: DETECT T-SHIRT BOUNDS
# ══════════════════════════════════════════════════════════════════════════
def detect_tshirt_bounds(product_path, inset=10, fuzz_percent=3,
                         chest_top_ratio=0.25, chest_bottom_ratio=0.72,
                         chest_left_ratio=0.22, chest_right_ratio=0.78):
    print("  🔍  Detecting t-shirt print zone...")

    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height

        with img.clone() as trimmed:
            trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
            shirt_x = trimmed.page_x
            shirt_y = trimmed.page_y
            shirt_w = trimmed.width
            shirt_h = trimmed.height

        min_size = min(pw, ph) * 0.3
        if shirt_w < min_size or shirt_h < min_size:
            print("  ⚠️  Trim gave small result — using proportion-based detection")
            shirt_x = int(pw * 0.10)
            shirt_y = int(ph * 0.05)
            shirt_w = int(pw * 0.80)
            shirt_h = int(ph * 0.88)

        if shirt_w > pw * 0.98 and shirt_h > ph * 0.98:
            print("  ⚠️  Trim captured nearly entire image — using proportion-based detection")
            shirt_x = int(pw * 0.10)
            shirt_y = int(ph * 0.05)
            shirt_w = int(pw * 0.80)
            shirt_h = int(ph * 0.88)

    chest_x = int(shirt_x + shirt_w * chest_left_ratio)
    chest_y = int(shirt_y + shirt_h * chest_top_ratio)
    chest_w = int(shirt_w * (chest_right_ratio - chest_left_ratio))
    chest_h = int(shirt_h * (chest_bottom_ratio - chest_top_ratio))

    chest_x += inset
    chest_y += inset
    chest_w -= inset * 2
    chest_h -= inset * 2

    chest_x = max(0, chest_x)
    chest_y = max(0, chest_y)
    chest_w = max(1, min(chest_w, pw - chest_x))
    chest_h = max(1, min(chest_h, ph - chest_y))

    print(f"  👕  Shirt silhouette: x={shirt_x} y={shirt_y} {shirt_w}×{shirt_h}")
    print(f"  📐  Chest print zone: x={chest_x} y={chest_y} {chest_w}×{chest_h}  "
          f"(product: {pw}×{ph})")

    return chest_x, chest_y, chest_w, chest_h, pw, ph, shirt_x, shirt_y, shirt_w, shirt_h


def get_bounds_cached(product_path, inset=10, fuzz_percent=3,
                      chest_top_ratio=0.25, chest_bottom_ratio=0.72,
                      chest_left_ratio=0.22, chest_right_ratio=0.78):
    cache = _load_cache()
    key = _product_cache_key(product_path)
    ratios_key = f"{chest_top_ratio},{chest_bottom_ratio},{chest_left_ratio},{chest_right_ratio}"

    entry = cache.get(key)
    if (entry and entry.get("inset") == inset
            and entry.get("fuzz") == fuzz_percent
            and entry.get("ratios") == ratios_key):
        b = entry["bounds"]
        s = entry["shirt"]
        print(f"  📐  Chest zone (CACHED): x={b['cx']} y={b['cy']} "
              f"{b['cw']}×{b['ch']}  ({b['pw']}×{b['ph']})")
        return (b['cx'], b['cy'], b['cw'], b['ch'], b['pw'], b['ph'],
                s['sx'], s['sy'], s['sw'], s['sh'])

    cx, cy, cw, ch, pw, ph, sx, sy, sw, sh = detect_tshirt_bounds(
        product_path, inset, fuzz_percent,
        chest_top_ratio, chest_bottom_ratio,
        chest_left_ratio, chest_right_ratio
    )

    cache[key] = {
        "product": os.path.abspath(product_path),
        "inset": inset, "fuzz": fuzz_percent, "ratios": ratios_key,
        "bounds": {"cx": cx, "cy": cy, "cw": cw, "ch": ch, "pw": pw, "ph": ph},
        "shirt": {"sx": sx, "sy": sy, "sw": sw, "sh": sh},
    }
    _save_cache(cache)
    return cx, cy, cw, ch, pw, ph, sx, sy, sw, sh


# ══════════════════════════════════════════════════════════════════════════
#  STEP 2: SOFT OVAL MASK FOR CHEST ZONE
# ══════════════════════════════════════════════════════════════════════════
def create_chest_mask(cw, ch, feather=12):
    mask_hash = hashlib.md5(f"chest_oval_{cw}_{ch}_{feather}".encode()).hexdigest()[:10]
    mask_path = os.path.join(CACHE_DIR, f"{mask_hash}_chest_mask.png")

    if _cached_file_valid(mask_path):
        print("  🎭  Chest oval mask (CACHED)")
        return mask_path

    with Image(width=cw, height=ch, background=Color('black')) as mask:
        with Drawing() as draw:
            margin = feather + 2
            draw.fill_color = Color('white')
            
            center_x = cw / 2.0
            center_y = ch / 2.0
            radius_x = max(1, (cw / 2.0)*1.5 )
            radius_y = max(1, (ch / 2.0)*1.5 )
            
            draw.ellipse((center_x, center_y), (radius_x, radius_y))
            draw(mask)

        mask.blur(radius=0, sigma=feather)
        mask.save(filename=mask_path)

    print(f"  🎭  Chest oval mask created {cw}×{ch} (feather={feather}px)")
    return mask_path


# ══════════════════════════════════════════════════════════════════════════
#  STEP 3: EXTRACT FABRIC TEXTURE
# ══════════════════════════════════════════════════════════════════════════
def extract_fabric_texture(product_path, cx, cy, cw, ch):
    key = _product_cache_key(product_path)
    bh = hashlib.md5(f"{cx},{cy},{cw},{ch}".encode()).hexdigest()[:8]
    tex_path = os.path.join(CACHE_DIR, f"{key}_{bh}_fabric_tex.png")

    if _cached_file_valid(tex_path):
        print("  🧵  Fabric texture (CACHED)")
        return tex_path

    with Image(filename=product_path) as img:
        img.crop(left=cx, top=cy, width=cw, height=ch)
        img.type = 'grayscale'
        img.blur(radius=0, sigma=1.5)
        img.save(filename=tex_path)

    print("  🧵  Fabric texture extracted")
    return tex_path


# ══════════════════════════════════════════════════════════════════════════
#  STEP 4: PREPARE DESIGN
# ══════════════════════════════════════════════════════════════════════════
def prepare_design_for_tshirt(design_path, cw, ch, scale=0.85,
                                warp_amount=0.02, opacity=100,
                                placement='center', relative_padding=0.24):
    label_path = os.path.join(TMP_DIR, "_tshirt_design.png")

    # Base targets determined strictly by the scale of the chest zone
    base_w = cw * scale
    base_h = ch * scale

    # Apply relative padding (e.g., 0.08 adds an 8% buffer regardless of resolution)
    target_w = max(1, int(base_w * (1 + relative_padding)))
    target_h = max(1, int(base_h * (1 + relative_padding)))

    with Image(filename=design_path) as img:
        orig_w, orig_h = img.width, img.height

        ratio_w = target_w / orig_w
        ratio_h = target_h / orig_h
        fit_ratio = min(ratio_w, ratio_h)

        new_w = max(1, int(orig_w * fit_ratio))
        new_h = max(1, int(orig_h * fit_ratio))
        img.resize(new_w, new_h)

        fitted_w, fitted_h = img.width, img.height

        if warp_amount > 0:
            img.virtual_pixel = 'transparent'
            img.distort('barrel', (warp_amount, 0, 0, 1.0 - warp_amount))

        offset_x = (cw - img.width) // 2

        if placement == 'top':
            offset_y = max(0, int(ch * 0.05))
        elif placement == 'upper':
            offset_y = (ch - img.height) // 4
        else:  
            offset_y = (ch - img.height) // 2

        offset_x = max(0, offset_x)
        offset_y = max(0, offset_y)

        with Image(width=cw, height=ch, background=Color('transparent')) as canvas:
            canvas.composite(img, left=offset_x, top=offset_y, operator='over')

            if opacity < 100:
                canvas.evaluate('multiply', opacity / 100.0, channel='alpha')

            canvas.save(filename=label_path)

    print(f"  🖼️   Design: {orig_w}×{orig_h} → fit {fitted_w}×{fitted_h} "
          f"→ canvas {cw}×{ch} (placement={placement})")
    return label_path


# ══════════════════════════════════════════════════════════════════════════
#  STEP 5: COMPOSITE
# ══════════════════════════════════════════════════════════════════════════
def composite_tshirt(product_path, output_path, design_label_path,
                     mask_path, texture_path, cx, cy, cw, ch,
                     design_opacity=0.92, texture_strength=0.3):
                     
    copy_op = get_op('copy_opacity')
    clip_op = get_op('dst_in')  
    mult_op = get_op('multiply')

    with Image(filename=product_path) as product:

        # ── Layer 1: Mask and Place Design ──
        with Image(filename=design_label_path) as design:
            with Image(filename=mask_path) as mask:
                if mask.width != design.width or mask.height != design.height:
                    mask.resize(design.width, design.height)

                with Image(width=design.width, height=design.height, background=Color('white')) as alpha_mask:
                    alpha_mask.composite_channel('alpha', mask, copy_op, left=0, top=0)
                    design.composite(alpha_mask, left=0, top=0, operator=clip_op)

            if design_opacity < 1.0:
                design.evaluate('multiply', design_opacity, channel='alpha')

            # Place design on shirt
            product.composite(design, left=cx, top=cy, operator='over')

            # ── Layer 2: Fabric texture show-through ──
            if texture_strength > 0 and os.path.exists(texture_path):
                with Image(filename=texture_path) as texture:
                    if texture.width != cw or texture.height != ch:
                        texture.resize(cw, ch)

                    texture.level(black=0.4, white=0.6)
                    texture.alpha_channel = 'activate'

                    # Clip texture strictly to design edges to prevent dark rectangles 
                    texture.composite(design, left=0, top=0, operator=clip_op)

                    texture.evaluate('multiply', texture_strength, channel='alpha')
                    product.composite(texture, left=cx, top=cy, operator=mult_op)

        product.save(filename=output_path)


# ══════════════════════════════════════════════════════════════════════════
#  GUIDE & DEBUG
# ══════════════════════════════════════════════════════════════════════════
def save_guide(product_path, output_path, cx, cy, cw, ch, sx, sy, sw, sh):
    base, ext = os.path.splitext(output_path)
    guide = base + '_GUIDE' + (ext or '.png')

    with Image(filename=product_path) as img:
        with Drawing() as draw:
            # Shirt bounds (blue)
            draw.fill_color = Color('none')
            draw.stroke_color = Color('rgba(0,100,255,0.6)')
            draw.stroke_width = 2
            draw.rectangle(left=sx, top=sy, right=sx + sw, bottom=sy + sh)

            # Chest zone (green)
            draw.fill_color = Color('rgba(0,255,0,0.1)')
            draw.stroke_color = Color('rgba(0,200,0,0.8)')
            draw.stroke_width = 3
            draw.rectangle(left=cx, top=cy, right=cx + cw, bottom=cy + ch)

            # Center crosshair (red)
            center_x = cx + cw // 2
            center_y = cy + ch // 2
            draw.stroke_color = Color('rgba(255,0,0,0.6)')
            draw.stroke_width = 1
            draw.line((center_x - 30, center_y), (center_x + 30, center_y))
            draw.line((center_x, center_y - 30), (center_x, center_y + 30))

            draw(img)
        img.save(filename=guide)
    print(f"  📋  Guide saved → {guide}")


def save_mask_preview(mask_path, output_path):
    base, ext = os.path.splitext(output_path)
    preview = base + '_MASK' + (ext or '.png')
    with Image(filename=mask_path) as mask:
        mask.save(filename=preview)
    print(f"  🎭  Mask preview → {preview}")


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Unisex T-Shirt mockup compositor")
    ap.add_argument("--product", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--output", default="tshirt_result.png")

    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)

    ap.add_argument("--chest-top", type=float, default=0.25)
    ap.add_argument("--chest-bottom", type=float, default=0.72)
    ap.add_argument("--chest-left", type=float, default=0.22)
    ap.add_argument("--chest-right", type=float, default=0.78)

    ap.add_argument("--scale", type=float, default=0.85)
    ap.add_argument("--warp", type=float, default=0.02)
    ap.add_argument("--placement", default="center", choices=["center", "top", "upper"])
    ap.add_argument("--opacity", type=int, default=95)
    ap.add_argument("--texture", type=float, default=0.3)
    ap.add_argument("--feather", type=int, default=12)

    ap.add_argument("--fuzz", type=int, default=3)
    ap.add_argument("--inset", type=int, default=10)

    ap.add_argument("--show-grid", action="store_true")
    ap.add_argument("--show-mask", action="store_true")
    ap.add_argument("--no-cache", action="store_true")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} not found: {path}")
            sys.exit(1)

    if args.no_cache:
        cx, cy, cw, ch, pw, ph, sx, sy, sw, sh = detect_tshirt_bounds(
            args.product, inset=args.inset, fuzz_percent=args.fuzz,
            chest_top_ratio=args.chest_top, chest_bottom_ratio=args.chest_bottom,
            chest_left_ratio=args.chest_left, chest_right_ratio=args.chest_right)
    else:
        cx, cy, cw, ch, pw, ph, sx, sy, sw, sh = get_bounds_cached(
            args.product, inset=args.inset, fuzz_percent=args.fuzz,
            chest_top_ratio=args.chest_top, chest_bottom_ratio=args.chest_bottom,
            chest_left_ratio=args.chest_left, chest_right_ratio=args.chest_right)

    cx = args.x if args.x is not None else cx
    cy = args.y if args.y is not None else cy
    cw = args.w if args.w is not None else cw
    ch = args.h if args.h is not None else ch
    timer.mark("Bounds detection")

    print(f"\n  Product    : {args.product}")
    print(f"  Design     : {args.design}")
    print(f"  Shirt      : x={sx} y={sy} {sw}×{sh}")
    print(f"  Chest zone : x={cx} y={cy} {cw}×{ch}")
    print(f"  Scale      : {args.scale:.0%} | Warp: {args.warp} | Placement: {args.placement}")
    print(f"  Opacity    : {args.opacity}% | Texture: {args.texture:.0%} | Feather: {args.feather}px")
    print(f"  Cache      : {'disabled' if args.no_cache else CACHE_DIR}\n")

    mask_path = create_chest_mask(cw, ch, feather=args.feather)
    timer.mark("Chest mask")

    if args.show_grid:
        save_guide(args.product, args.output, cx, cy, cw, ch, sx, sy, sw, sh)
    if args.show_mask:
        save_mask_preview(mask_path, args.output)

    design_path = prepare_design_for_tshirt(
        args.design, cw, ch,
        scale=args.scale, warp_amount=args.warp,
        opacity=args.opacity, placement=args.placement)
    timer.mark("Design preparation")

    texture_path = extract_fabric_texture(args.product, cx, cy, cw, ch)
    timer.mark("Fabric texture")

    composite_tshirt(
        args.product, args.output, design_path,
        mask_path, texture_path, cx, cy, cw, ch,
        design_opacity=args.opacity / 100.0,
        texture_strength=args.texture)
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")
    timer.report()


if __name__ == "__main__":
    main()