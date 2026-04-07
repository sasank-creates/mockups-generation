import os, sys, argparse, tempfile, time
from wand.image import Image
from wand.drawing import Drawing
from wand.color import Color

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


# ── Step 1: Detect / Assign Frame Bounds ──────────────────────────────────
def get_frame_bounds(product_path, x, y, w, h):
    """
    Returns exact coordinates.
    """
    with Image(filename=product_path) as img:
        pw, ph = img.width, img.height

    # Defensive check in case the function is imported elsewhere without args
    if any(v is None for v in [x, y, w, h]):
        print("  ⚠️  Coordinates missing. Using hardcoded defaults.")
        x = x if x is not None else 286
        y = y if y is not None else 200
        w = w if w is not None else 628
        h = h if h is not None else 800

    print(f"  📐  Frame bounds set to: x={x} y={y} w={w} h={h}  (product: {pw}×{ph})")
    return x, y, w, h, pw, ph


# ── Step 2: Prepare Design (Strict Cover & Crop) ──────────────────────────
def prepare_design_for_frame(design_path, target_w, target_h):
    """
    Strict mathematical center-crop. This avoids ImageMagick's virtual canvas
    quirks and guarantees absolutely ZERO overflow.
    """
    label_path = os.path.join(TMP_DIR, "_frame_design.png")

    with Image(filename=design_path) as img:
        
        # 1. Calculate exact scaling needed to "cover" the target box
        img_ratio = img.width / img.height
        target_ratio = target_w / target_h

        if img_ratio > target_ratio:
            # Image is too wide: match height, let width overflow (then crop)
            new_h = target_h
            new_w = int(new_h * img_ratio)
        else:
            # Image is too tall: match width, let height overflow (then crop)
            new_w = target_w
            new_h = int(new_w / img_ratio)
            
        # 2. Resize to exact cover dimensions
        img.resize(new_w, new_h)

        # 3. Calculate explicit left/top offsets for a perfect center crop
        left = max(0, (new_w - target_w) // 2)
        top = max(0, (new_h - target_h) // 2)
        
        # 4. Crop and strip virtual canvas page geometry
        img.crop(left=left, top=top, width=target_w, height=target_h)
        img.page = (0, 0, 0, 0)  # STRIP VIRTUAL CANVAS (Fixes overflow bug)

        # 5. Composite onto a solid white canvas (covers the red dot)
        with Image(width=target_w, height=target_h, background=Color('white')) as canvas:
            canvas.composite(img, left=0, top=0, operator='over')
            canvas.save(filename=label_path)

    print(f"  🖼️   Design rigidly cropped to exactly {target_w}×{target_h} px")
    return label_path


# ── Step 3: Final composite ───────────────────────────────────────────────
def composite_frame(product_path, output_path, design_ready_path, lx, ly):
    """
    Drops the perfectly sized design into the frame area.
    """
    with Image(filename=product_path) as product:
        with Image(filename=design_ready_path) as design:
            # 'over' blend mode completely overwrites the white background
            product.composite(design, left=lx, top=ly, operator='over')
        product.save(filename=output_path)


# ── Guide image (for finding coordinates) ─────────────────────────────────
def save_guide(product_path, output_path, lx, ly, lw, lh):
    """Saves a red rectangle over the product to help you dial in exact coordinates"""
    base, ext = os.path.splitext(output_path)
    guide = base + '_GUIDE' + (ext or '.png')
    
    with Image(filename=product_path) as img:
        with Drawing() as draw:
            # Semi-transparent red fill
            draw.fill_color = Color('rgba(255, 0, 0, 0.4)')
            draw.stroke_color = Color('red')
            draw.stroke_width = 2
            
            # Draw exact bounds
            draw.rectangle(left=lx, top=ly, right=lx + lw, bottom=ly + lh)
            draw(img)
            
        img.save(filename=guide)
    print(f"  🔴  Grid guide saved to help align coordinates → {guide}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Strict Rectangle Frame Mockup Compositor")
    ap.add_argument("--product", required=True, help="Path to empty frame image")
    ap.add_argument("--design", required=True, help="Path to artwork/design")
    ap.add_argument("--output", default="frame_result.png")

    # Hardcoded your exact coordinates as the defaults here:
    ap.add_argument("--x", type=int, default=286, help="Left inner edge X coordinate")
    ap.add_argument("--y", type=int, default=200, help="Top inner edge Y coordinate")
    ap.add_argument("--w", type=int, default=628, help="Inner area Width")
    ap.add_argument("--h", type=int, default=800, help="Inner area Height")

    ap.add_argument("--show-grid", action="store_true", help="Generate a visual red-box guide")

    args = ap.parse_args()
    timer = Timer()

    for label, path in [("Product", args.product), ("Design", args.design)]:
        if not os.path.exists(path):
            print(f"  ERROR: {label} not found: {path}")
            sys.exit(1)

    print(f"\n  Product  : {args.product}")
    print(f"  Design   : {args.design}")

    # 1. Setup bounds
    lx, ly, lw, lh, pw, ph = get_frame_bounds(args.product, args.x, args.y, args.w, args.h)
    timer.mark("Bounds detection")

    # Generate visual guide if requested
    if args.show_grid:
        save_guide(args.product, args.output, lx, ly, lw, lh)

    # 2. Design prep (Scale to Cover + Strict center crop)
    design_ready_path = prepare_design_for_frame(args.design, lw, lh)
    timer.mark("Design preparation")

    # 3. Composite
    composite_frame(args.product, args.output, design_ready_path, lx, ly)
    timer.mark("Final composite")

    kb = os.path.getsize(args.output) // 1024
    print(f"\n  ✅  Done → {args.output}  ({kb} KB)")

    timer.report()

if __name__ == "__main__":
    main()