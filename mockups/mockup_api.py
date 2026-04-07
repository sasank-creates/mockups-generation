import os, sys, tempfile, json, hashlib, time, re
import numpy as np
from wand.image import Image, COMPOSITE_OPERATORS
from wand.drawing import Drawing
from wand.color import Color
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from supabase import create_client, Client
import uvicorn
import shutil
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mockup_api_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
TMP_DIR = tempfile.gettempdir()

# Supabase Initialization
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                k, v = line.strip().split('=', 1)
                os.environ[k] = v.strip()

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
bucket_name: str = os.environ.get("SUPABASE_BUCKET", "mockups")
base_folder: str = os.environ.get("SUPABASE_BASE_FOLDER", "")
products_base_dir: str = os.environ.get("PRODUCTS_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))

if url and key:
    supabase: Client = create_client(url, key)
else:
    supabase = None

from fastapi.middleware.cors import CORSMiddleware
app = FastAPI(title="Unified Mockup API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import RedirectResponse
@app.get("/")
def read_root():
    return RedirectResponse(url="/docs")

def _init_bottle():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        lx, ly, lw, lh, pw, ph = detect_bottle_bounds(product_path, fuzz_percent=15, inset=2, top_crop=0.12, bottom_crop=0.04)
        mask_path = create_bottle_mask(product_path, lx, ly, lw, lh)
        design_prep = prepare_design_for_bottle(design_path, lw, lh, scale=kwargs.get('scale', 1.0), shift_y=kwargs.get('shift_y', 50))
        composite_bottle(product_path, output_path, design_prep, mask_path, lx, ly, lw, lh, design_opacity=kwargs.get('opacity', 95)/100.0)
        return output_path
    return run_pipeline

generator_bottle = _init_bottle()

def _init_clock():

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
                with open(CACHE_FILE, "r") as f: return json.load(f)
            except: pass
        return {}

    def _save_cache(cache):
        try:
            with open(CACHE_FILE, "w") as f: json.dump(cache, f, indent=2)
        except: pass

    def _cached_file_valid(path):
        return path and os.path.exists(path)

    def get_op(name):
        aliases = {
            "copy_opacity": ["copy_alpha", "copy_opacity"], 
            "over": ["over"], 
            "multiply": ["multiply"],
            "dst_in": ["dst_in"],
        }
        for candidate in aliases.get(name, [name]):
            if candidate in COMPOSITE_OPERATORS: return candidate
        return "over"


    # ── Step 1: Detect Clock Inner Face ───────────────────────────────────────
    def detect_clock_bounds(product_path, fuzz_percent=5, inset_ratio=0.045):
        """
        Finds the clock frame, calculates the center, and determines the 
        radius of the inner white printable face.
        """
        print("  🔍  Detecting clock face boundaries...")

        with Image(filename=product_path) as img:
            pw, ph = img.width, img.height

            with img.clone() as trimmed:
                trimmed.background_color = Color("white")
                trimmed.alpha_channel = 'remove' 
                # Trim white background to find the outer black clock frame
                trimmed.trim(fuzz=fuzz_percent * 65535 / 100)
                out_x = trimmed.page_x
                out_y = trimmed.page_y
                out_w = trimmed.width
                out_h = trimmed.height

            # Calculate exact center of the clock
            cx = out_x + (out_w // 2)
            cy = out_y + (out_h // 2)

            # Calculate inner radius (outer radius minus the black frame thickness)
            outer_radius = min(out_w, out_h) / 2
            inset_px = outer_radius * inset_ratio
            inner_radius = int(outer_radius - inset_px)

            print(f"  📐  Clock center: x={cx} y={cy} | Inner Radius: {inner_radius}px")
            return cx, cy, inner_radius, pw, ph


    def get_bounds_cached(product_path, fuzz_percent=5, inset_ratio=0.045):
        cache = _load_cache()
        key = _product_cache_key(product_path)
        entry = cache.get(key)

        if entry and entry.get("inset_ratio") == inset_ratio and entry.get("fuzz") == fuzz_percent:
            b = entry["bounds"]
            print(f"  📐  Clock bounds (CACHED): center x={b['cx']} y={b['cy']} | r={b['radius']}")
            return b['cx'], b['cy'], b['radius'], b['pw'], b['ph']

        cx, cy, radius, pw, ph = detect_clock_bounds(product_path, fuzz_percent, inset_ratio)

        cache[key] = {
            "product": os.path.abspath(product_path),
            "inset_ratio": inset_ratio, "fuzz": fuzz_percent,
            "bounds": {"cx": cx, "cy": cy, "radius": radius, "pw": pw, "ph": ph}
        }
        _save_cache(cache)
        return cx, cy, radius, pw, ph


    # ── Step 2: Perfect Circular Mask ─────────────────────────────────────────
    def create_clock_mask(product_path, cx, cy, radius):
        """
        Creates a full-size mask: Transparent background with a White circle 
        representing the printable clock face area.
        """
        key = _product_cache_key(product_path)
        bounds_hash = hashlib.md5(f"{cx},{cy},{radius}_transparent_v3_local".encode()).hexdigest()[:8]
        mask_path = os.path.join(CACHE_DIR, f"{key}_{bounds_hash}_mask.png")

        if _cached_file_valid(mask_path):
            print("  🎭  Clock circle mask (CACHED)")
            return mask_path

        target_size = radius * 2
        with Image(width=target_size, height=target_size, background=Color('transparent')) as mask:
            with Drawing() as draw:
                draw.fill_color = Color('white')
                # Draw circle: center coordinate, then a point on the perimeter
                draw.circle((radius, radius), (radius, radius + radius))
                draw(mask)

            # Very slight blur for anti-aliasing the sharp circle edge
            mask.blur(radius=0, sigma=1.0)
            mask.save(filename=mask_path)

        print(f"  🎭  Circular mask created (r={radius}px)")
        return mask_path


    # ── Step 3: Prepare Multiply Layer ────────────────────────────────────────
    def prepare_multiply_layer(design_path, mask_path, radius, scale=1.0, shift_x=0, shift_y=0, shrink_w=190, shrink_h=60):
        """
        Creates a strictly fitted image where the design is perfectly clipped inside 
        the clock circle, and EVERYTHING outside the circle is pure white.
        """
        layer_path = os.path.join(TMP_DIR, "_clock_multiply_layer.png")
        target_size = radius * 2

        with Image(filename=design_path) as img:
            # Scale to completely fill the circular face
            ratio = max(target_size / img.width, target_size / img.height) * scale
            new_w = max(1, int(img.width * ratio) - shrink_w)
            new_h = max(1, int(img.height * ratio) - shrink_h)
            img.transform(resize=f"{new_w}x{new_h}!")

            # Calculate offset to center the design on the face
            off_x = radius - (new_w // 2) + shift_x
            off_y = radius - (new_h // 2) + shift_y

            # Step A: Place design on a transparent target-size canvas
            with Image(width=target_size, height=target_size, background=Color('transparent')) as trans_canvas:
                trans_canvas.composite(img, left=off_x, top=off_y, operator=get_op('over'))

                # Step B: Clip the design strictly to the circle using the mask
                with Image(filename=mask_path) as mask:
                    trans_canvas.composite(mask, left=0, top=0, operator=get_op('dst_in'))

                # Step C: Place the clipped circle onto a PURE WHITE canvas.
                # (Because multiplying pure white = no change, preserving the clock frame).
                with Image(width=target_size, height=target_size, background=Color('white')) as final_layer:
                    final_layer.composite(trans_canvas, left=0, top=0, operator=get_op('over'))
                    final_layer.save(filename=layer_path)

        print(f"  🖼️   Design clipped to circle and prepared for Multiply Blend.")
        return layer_path


    # ── Step 4: Final composite ───────────────────────────────────────────────
    def composite_clock(product_path, output_path, multiply_layer_path, cx, cy, radius, opacity=100):
        """
        Multiplies the prepared white-backed layer over the blank clock.
        This seamlessly maps the design to the face while perfectly keeping 
        the shadows cast by the clock hands and frame.
        """
        with Image(filename=product_path) as product:
            with Image(filename=multiply_layer_path) as layer:
                left = cx - radius
                top = cy - radius

                if opacity < 100:
                    # If opacity is reduced, fade the layer towards white (not transparent)
                    # so it doesn't darken the clock frame. 
                    # (Simple workaround: blend layer over pure white before multiplying)
                    with Image(width=layer.width, height=layer.height, background=Color('white')) as fade_bg:
                        layer.evaluate('multiply', opacity / 100.0, channel='alpha')
                        fade_bg.composite(layer, left=0, top=0, operator='over')
                        product.composite(fade_bg, left=left, top=top, operator=get_op('multiply'))
                else:
                    product.composite(layer, left=left, top=top, operator=get_op('multiply'))

            product.save(filename=output_path)


    # ── Guide image ───────────────────────────────────────────────────────────
    def save_guide(product_path, output_path, cx, cy, radius):
        base, ext = os.path.splitext(output_path)
        guide = base + '_GUIDE' + (ext or '.png')
        with Image(filename=product_path) as img:
            with Drawing() as draw:
                # Draw printable area (green circle)
                draw.fill_color = Color('rgba(0,255,0,0.15)')
                draw.stroke_color = Color('lime')
                draw.stroke_width = 2
                draw.circle((cx, cy), (cx, cy + radius))

                # Draw center crosshair
                draw.stroke_color = Color('red')
                draw.stroke_width = 1
                draw.line((cx - 20, cy), (cx + 20, cy))
                draw.line((cx, cy - 20), (cx, cy + 20))

                draw(img)
            img.save(filename=guide)
        print(f"  🩵  Guide saved → {guide}")


    # ── Main ──────────────────────────────────────────────────────────────────


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        cx, cy, radius, pw, ph = detect_clock_bounds(product_path, fuzz_percent=5, inset_ratio=0.045)
        mask_path = create_clock_mask(product_path, cx, cy, radius)
        design_prep = prepare_multiply_layer(design_path, mask_path, radius, scale=kwargs.get('scale', 1.0), shift_x=kwargs.get('shift_x', 0), shift_y=kwargs.get('shift_y', 0), shrink_w=kwargs.get('shrink_w', 190), shrink_h=kwargs.get('shrink_h', 60))
        composite_clock(product_path, output_path, design_prep, cx, cy, radius, opacity=kwargs.get('opacity', 100))
        return output_path
    return run_pipeline

generator_clock = _init_clock()

def _init_cup():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        lx, ly, lw, lh, pw, ph = detect_cup_bounds(product_path, fuzz_percent=15, inset=30)
        mask_path = create_cup_mask(product_path, lx, ly, lw, lh)
        design_prep = prepare_design_for_cup(design_path, lw, lh, scale=kwargs.get('scale', 1.0), shift_x=kwargs.get('shift_x', -15), shift_y=kwargs.get('shift_y', -90), fit=kwargs.get('fit', 'contain'), warp_type='perfect', warp_amount=kwargs.get('warp_amt', 30.0))
        composite_cup(product_path, output_path, design_prep, mask_path, lx, ly, lw, lh, design_opacity=kwargs.get('opacity', 92)/100.0)
        return output_path
    return run_pipeline

generator_cup = _init_cup()

def _init_frame():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        x = kwargs.get('x', 286)
        y = kwargs.get('y', 200)
        w = kwargs.get('w', 628)
        h = kwargs.get('h', 800)
        lx, ly, lw, lh, pw, ph = get_frame_bounds(product, x, y, w, h)
        design_prep = prepare_design_for_frame(design, lw, lh)
        composite_frame(product, output, design_prep, lx, ly)
        return output_path
    return run_pipeline

generator_frame = _init_frame()

def _init_pillow():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        lx, ly, lw, lh, pw, ph = detect_pillow_bounds(product_path, inset=20, fuzz_percent=5)
        mask_path = create_pillow_mask(product_path, lx, ly, lw, lh, pw, ph)
        design_prep = prepare_design_for_pillow(design_path, lw, lh, scale=kwargs.get('scale', 0.80), barrel_amount=0.06, opacity=kwargs.get('opacity', 92))
        disp_path = create_displacement_map(product_path, lx, ly, lw, lh)
        composite_pillow(product_path, output_path, design_prep, mask_path, lx, ly, lw, lh, blend_mode='multiply', design_opacity=kwargs.get('opacity', 92)/100.0)
        return output_path
    return run_pipeline

generator_pillow = _init_pillow()

def _init_totebag():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        lx, ly, lw, lh, pw, ph = detect_totebag_bounds(product_path, inset=2, fuzz_percent=5)
        mask_path = create_totebag_mask(product_path, lx, ly, lw, lh, pw, ph)
        design_prep = prepare_design_for_tote(design_path, lw, lh, scale=kwargs.get('scale', 0.90), opacity=kwargs.get('opacity', 95))
        composite_totebag(product_path, output_path, design_prep, mask_path, lx, ly, lw, lh, blend_mode='multiply', design_opacity=kwargs.get('opacity', 95)/100.0)
        return output_path
    return run_pipeline

generator_totebag = _init_totebag()

def _init_tshirt():

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


    def run_pipeline(product, design, output, **kwargs):
        # Disable cache for safe generation via API
        product_path = product
        design_path = design
        output_path = output
        
        cx, cy, cw, ch, pw, ph, sx, sy, sw, sh = detect_tshirt_bounds(product_path, inset=10, fuzz_percent=3)
        mask_path = create_chest_mask(cw, ch, feather=12)
        design_prep = prepare_design_for_tshirt(design_path, cw, ch, scale=kwargs.get('scale', 0.85), warp_amount=0.02, opacity=kwargs.get('opacity', 95), placement="center")
        texture_path = extract_fabric_texture(product_path, cx, cy, cw, ch)
        composite_tshirt(product_path, output_path, design_prep, mask_path, texture_path, cx, cy, cw, ch, design_opacity=kwargs.get('opacity', 95)/100.0, texture_strength=0.3)
        return output_path
    return run_pipeline

generator_tshirt = _init_tshirt()

GENERATORS = {
    'bottle': generator_bottle,
    'clock': generator_clock,
    'cup': generator_cup,
    'mug': generator_cup,
    'frame': generator_frame,
    'pillow': generator_pillow,
    'totebag': generator_totebag,
    'tshirt': generator_tshirt,
    'sweatshirt': generator_tshirt
}

@app.post("/generate-mockup")
async def generate_mockup(
    product_type: str = Form(...),
    product_image: UploadFile = File(...),
    target_image: UploadFile = File(...),
    scale: str = Form(None),
    opacity: str = Form(None),
    shift_x: str = Form(None),
    shift_y: str = Form(None),
    warp_amt: str = Form(None),
    fit: str = Form(None)
):
    product_type = product_type.lower()
    logger.info(f"Received /generate-mockup request for type: {product_type}")
    if product_type not in GENERATORS:
        raise HTTPException(status_code=400, detail=f"Invalid product_type. Allowed: {list(GENERATORS.keys())}")
        
    os.makedirs(TMP_DIR, exist_ok=True)
    safe_product_fname = re.sub(r'[^\w\-.]', '_', os.path.basename(product_image.filename or "product"))
    safe_target_fname = re.sub(r'[^\w\-.]', '_', os.path.basename(target_image.filename or "target"))
    temp_product = os.path.join(TMP_DIR, f"prod_{os.urandom(4).hex()}_{safe_product_fname}")
    temp_target = os.path.join(TMP_DIR, f"targ_{os.urandom(4).hex()}_{safe_target_fname}")
    
    # Secure filename for output
    safe_target_name = safe_target_fname.split('.')[0]
    out_name = f"{safe_target_name}_mockup.png"
    temp_output = os.path.join(TMP_DIR, out_name)

    # Save uploads
    with open(temp_product, "wb") as f:
        shutil.copyfileobj(product_image.file, f)
    with open(temp_target, "wb") as f:
        shutil.copyfileobj(target_image.file, f)
        
    try:
        # Run Mockup logic
        gen_func = GENERATORS[product_type]
        kwargs = {}
        if scale is not None: kwargs['scale'] = float(scale)
        if opacity is not None: kwargs['opacity'] = int(opacity)
        if shift_x is not None: kwargs['shift_x'] = int(shift_x)
        if shift_y is not None: kwargs['shift_y'] = int(shift_y)
        if warp_amt is not None: kwargs['warp_amt'] = float(warp_amt)
        if fit is not None: kwargs['fit'] = fit
        
        gen_result_path = gen_func(
            temp_product, temp_target, temp_output, **kwargs
        )
        
        # Supabase Upload
        public_url = None
        if supabase:
            upload_path = f"{base_folder}/{out_name}" if base_folder else out_name
            with open(gen_result_path, "rb") as f:
                res = supabase.storage.from_(bucket_name).upload(upload_path, f, file_options={"content-type": "image/png", "upsert": "true"})
            
            public_url = supabase.storage.from_(bucket_name).get_public_url(upload_path)
            
        else:
            print("Supabase connection not initialized. Skipping upload.")

        return [public_url] if public_url else []
        
    except Exception as e:
        logger.error(f"Error during mockup generation for {product_type}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error occurred.")
        
    finally:
        # Cleanup
        for path in [temp_product, temp_target, temp_output]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

@app.post("/generate-all-mockups")
async def generate_all_mockups(
    target_image: UploadFile = File(...)
):
    logger.info("Received /generate-all-mockups request")
    os.makedirs(TMP_DIR, exist_ok=True)
    safe_target_fname = re.sub(r'[^\w\-.]', '_', os.path.basename(target_image.filename or "target"))
    temp_target = os.path.join(TMP_DIR, f"all_targ_{os.urandom(4).hex()}_{safe_target_fname}")
    
    with open(temp_target, "wb") as f:
        shutil.copyfileobj(target_image.file, f)
        
    # Get base name without extension
    safe_target_name = safe_target_fname.rsplit('.', 1)[0] if '.' in safe_target_fname else safe_target_fname
    folder_name = safe_target_name
    
    base_dir = products_base_dir
    
    DEFAULT_BACKGROUNDS = {
        'bottle': 'bottle.png',
        'clock': 'clock.png',
        'mug': 'mug.png',
        'frame': 'frame.png',
        'pillow': 'outdoor-pillow.jpg',
        'totebag': 'totebag.png',
        'tshirt': 'tshirt.png',
        'sweatshirt': 'sweatShirt.png'
    }

    semaphore = asyncio.Semaphore(3)

    async def generate_and_upload(ptype, bg_file):
        async with semaphore:
            try:
                logger.info(f"Generating mockup for {ptype} in all_mockups loop")
                product_path = os.path.join(base_dir, bg_file)
                if not os.path.exists(product_path):
                    return {"product_type": ptype, "error": f"Base image {bg_file} not found"}
                    
                gen_func = GENERATORS.get(ptype)
                if not gen_func:
                    return {"product_type": ptype, "error": f"Generator for {ptype} not found"}
                    
                out_name = f"{safe_target_name}_{ptype}_mockup.png"
                temp_output = os.path.join(TMP_DIR, f"{os.urandom(4).hex()}_{out_name}")
                
                # Run heavy ImageMagick generator in thread
                gen_result_path = await asyncio.to_thread(
                    gen_func,
                    product_path, temp_target, temp_output
                )
                
                public_url = None
                if supabase:
                    supabase_path = f"{base_folder}/{folder_name}/{out_name}" if base_folder else f"{folder_name}/{out_name}"
                    def upload_to_supabase():
                        for attempt in range(3):
                            try:
                                with open(gen_result_path, "rb") as file_obj:
                                    supabase.storage.from_(bucket_name).upload(supabase_path, file_obj, file_options={"content-type": "image/png", "upsert": "true"})
                                return supabase.storage.from_(bucket_name).get_public_url(supabase_path)
                            except Exception as e:
                                logger.warning(f"Upload failed for {out_name} (attempt {attempt+1}): {e}")
                                if attempt == 2:
                                    raise
                                time.sleep(1 + attempt)
                    
                    logger.info(f"Uploading {out_name} to Supabase path: {supabase_path}")
                    public_url = await asyncio.to_thread(upload_to_supabase)
                
                # Cleanup temp output
                if os.path.exists(gen_result_path):
                    try: os.remove(gen_result_path)
                    except: pass
                    
                return {
                    "product_type": ptype,
                    "filename": out_name,
                    "url": public_url
                }
            except Exception as e:
                logger.error(f"Error generating mockup for {ptype} in all_mockups loop", exc_info=True)
                return {
                    "product_type": ptype,
                    "error": "An error occurred during generation."
                }

    try:
        tasks = [generate_and_upload(ptype, bg_file) for ptype, bg_file in DEFAULT_BACKGROUNDS.items()]
        results = await asyncio.gather(*tasks)
        
        urls = [res.get("url") for res in results if res and res.get("url")]
        return urls
        
    except Exception as e:
        logger.error("Error in /generate-all-mockups", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error occurred.")
        
    finally:
        if os.path.exists(temp_target):
            try: os.remove(temp_target)
            except: pass

if __name__ == "__main__":
    uvicorn.run("mockup_api:app", host="0.0.0.0", port=8000, reload=True)
