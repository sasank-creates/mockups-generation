# Mockup ImageMagick - Product Mockup Generator

A professional Python-based mockup generation tool using ImageMagick to create stunning product mockups for e-commerce, marketing, and design purposes.

## 🎯 Overview

Mockup ImageMagick is a comprehensive solution for generating high-quality product mockups. It supports multiple product types including:
- 🍾 Bottles
- 👕 T-Shirts  
- ☕ Mugs
- ⏰ Clocks
- 🖼️ Frames
- ��️ Totebags
- 🛏️ Pillows

This tool provides both CLI and programmatic interfaces for seamless integration into your workflow.

## 📁 Project Structure

```
mockup-imagemagick/
├── mockups/                          # Mockup generation scripts
│   ├── bottle_mockup.py              # Bottle product mockup generator
│   ├── tshirt_mockup.py              # T-shirt product mockup generator
│   ├── mug_mockup.py                 # Mug product mockup generator
│   ├── clock_mockup.py               # Clock product mockup generator
│   ├── frame_mockup.py               # Frame product mockup generator
│   ├── totebag_mockup.py             # Totebag product mockup generator
│   ├── pillow_mockup.py              # Pillow/cushion mockup generator
│   └── mockup_api.py                 # Unified API module for all generators
│
├── products/                         # Product templates                   # Template product image
│   ├── bottle.png                    # Bottle template
│   ├── tshirt.png                    # T-shirt template
│   ├── mug.png                       # Mug template
│   ├── clock.png                     # Clock template
│   ├── frame.png                     # Frame template
│   ├── totebag.png                   # Totebag template
│   ├── pillow.png                    # Pillow template
│   └── outdoor-pillow.jpg            # Alternative pillow template
│
├── tests/                            # Generated mockup results and test outputs
│   └── *-result*.png                 # All generated mockup outputs
│
├── results/                          # Alternative output directory
├── testImages/                       # Test image resources
├── requirements.txt                  # Python package dependencies
├── README.md                         # This file
├── .gitignore                        # Git ignore rules
└── LICENSE                           # Project license
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.7+** - Ensure Python is installed on your system
- **ImageMagick** - Required for image processing operations
- **pip** - Python package manager

### Installation

#### Step 1: Clone the Repository

```bash
git clone https://github.com/sasank-creates/mockup-imagemagick.git
cd mockup-imagemagick
```

#### Step 2: Install ImageMagick

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install imagemagick
```

**macOS:**
```bash
brew install imagemagick
```

**Windows:**
Download and install from [ImageMagick Official Website](https://imagemagick.org/script/download.php#windows)

#### Step 3: Install Python Dependencies

Option A - Direct Installation:
```bash
pip install -r requirements.txt
```

Option B - Using Virtual Environment (Recommended):
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## 💻 Usage Guide

### Command Line Interface

Generate mockups using individual scripts with the following syntax:

```bash
# Generic syntax
.venv/bin/python3 mockups/<product>_mockup.py --product <template.png> --design <design.png> --output <output.png> [OPTIONS]
```

**Required Arguments:**
- `--product`: Path to product template image (e.g., `products/bottle.png`)
- `--design`: Path to design/image file (e.g., `testImages/image1.png`)
- `--output`: Output file path for the mockup result (e.g., `results/bottle-result.png`)

**Optional Arguments:**
- `--scale`: Scale factor for the design (default: 1.0)
- `--opacity`: Opacity percentage (0-100, default: 95)
- `--shift-y`: Vertical shift in pixels (default: 50)
- `--shift-x`: Horizontal shift in pixels (default: 0)
- `--fuzz`: Color fuzz percentage for edge detection (default: 15)
- `--inset`: Inset margin in pixels (default: 2)
- `--no-cache`: Disable caching for this run

**Examples:**

```bash
# Bottle mockup
.venv/bin/python3 mockups/bottle_mockup.py --product products/bottle.png --design testImages/image1.png --output results/bottle-result.png

# Bottle with custom scale and opacity
.venv/bin/python3 mockups/bottle_mockup.py --product products/bottle.png --design testImages/image1.png --output results/bottle-result.png --scale 1.2 --opacity 90

# Clock mockup
.venv/bin/python3 mockups/clock_mockup.py --product products/clock.png --design testImages/image3.png --output results/mugresult.png

# T-shirt mockup
.venv/bin/python3 mockups/tshirt_mockup.py --product products/tshirt.png --design testImages/image1.png --output results/tshirt-result.png

# Mug mockup
.venv/bin/python3 mockups/mug_mockup.py --product products/mug.png --design testImages/image1.png --output results/mug-result.png

# Frame mockup
.venv/bin/python3 mockups/frame_mockup.py --product products/frame.png --design testImages/image1.png --output results/frame-result.png

# Totebag mockup
.venv/bin/python3 mockups/totebag_mockup.py --product products/totebag.png --design testImages/image1.png --output results/totebag-result.png

# Pillow mockup
.venv/bin/python3 mockups/pillow_mockup.py --product products/pillow.png --design testImages/image1.png --output results/pillow-result.png
```

### FastAPI REST Server

Start the API server for programmatic mockup generation via HTTP:

```bash
# Start the server (runs on http://localhost:8000)
.venv/bin/python3 mockups/mockup_api.py

# Server will be available at:
# - API Docs: http://localhost:8000/docs
# - ReDoc: http://localhost:8000/redoc
```

**API Endpoints:**

1. **Single Mockup Generation**
   ```
   POST /generate-mockup
   ```
   Form parameters:
   - `product_type`: Product type (bottle, clock, cup, mug, frame, pillow, totebag, tshirt, sweatshirt)
   - `product_image`: Upload product template image file
   - `target_image`: Upload design/image file
   - `scale`: Scale factor (optional)
   - `opacity`: Opacity percentage (optional)
   - `shift_x`: Horizontal shift (optional)
   - `shift_y`: Vertical shift (optional)
   - `warp_amt`: Warp amount (optional)
   - `fit`: Fit mode (optional)

2. **Generate All Product Mockups**
   ```
   POST /generate-all-mockups
   ```
   Form parameters:
   - `target_image`: Upload design/image file (will generate for all product types)

### Python API

Programmatically generate mockups directly in your Python code:

```python
from mockups.mockup_api import GENERATORS

# Generate a bottle mockup
product_path = 'products/bottle.png'
design_path = 'testImages/image1.png'
output_path = 'results/bottle_output.png'

generator = GENERATORS['bottle']
generator(product_path, design_path, output_path, scale=1.0, opacity=0.95)

# Generate multiple mockups
product_types = ['bottle', 'clock', 'tshirt', 'mug', 'frame', 'totebag', 'pillow']
for product_type in product_types:
    output = f'results/{product_type}_result.png'
    GENERATORS[product_type](product_path, design_path, output)
```

## ⚙️ Environment Configuration

### .env File Setup (Optional)

For API server features like cloud storage integration with Supabase, create a `.env` file in the `mockups/` directory:

```bash
# mockups/.env

# Supabase Configuration (Optional - for cloud storage features)
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_api_key
SUPABASE_BUCKET=mockups
SUPABASE_BASE_FOLDER=mockups

# Products Base Directory (Optional - defaults to mockups directory)
PRODUCTS_BASE_DIR=/path/to/products
```

**Environment Variables Explained:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUPABASE_URL` | No | Empty | Supabase project URL for cloud storage integration |
| `SUPABASE_KEY` | No | Empty | Supabase API key for authentication |
| `SUPABASE_BUCKET` | No | `mockups` | Storage bucket name for generated mockups |
| `SUPABASE_BASE_FOLDER` | No | Empty | Base folder path in Supabase storage |
| `PRODUCTS_BASE_DIR` | No | Mockups dir | Local directory path where product templates are stored |

**Notes:**
- The `.env` file is optional. The application works fine without it.
- If `.env` is not provided, all paths will be relative to the project directory.
- Supabase integration is only used when both `SUPABASE_URL` and `SUPABASE_KEY` are provided.
- Add `.env` to your `.gitignore` to keep credentials private (already included in this project).

## 📦 Dependencies

All required packages are listed in `requirements.txt`:

| Package | Version | Purpose |
|---------|---------|---------|
| Pillow | ≥9.0.0 | Python Imaging Library for image processing |
| numpy | ≥1.21.0 | Numerical computing and array operations |
| scipy | ≥1.7.0 | Scientific computing utilities |
| Wand | ≥0.6.7 | Python bindings for ImageMagick |

Install all dependencies:
```bash
pip install -r requirements.txt
```

## 📚 Module Descriptions

### mockups/bottle_mockup.py
Creates professional bottle product mockups by compositing custom images onto bottle templates with realistic perspective and shadow effects.

### mockups/tshirt_mockup.py
Generates t-shirt mockups with custom prints, supporting various print placements and sizes with authentic fabric rendering.

### mockups/mug_mockup.py
Creates ceramic mug mockups with wrap-around print capabilities, showing the design on multiple angles.

### mockups/clock_mockup.py
Generates clock face mockups with custom image centers, perfect for product visualization.

### mockups/frame_mockup.py
Creates framed artwork mockups with customizable frame styles and mat options.

### mockups/totebag_mockup.py
Generates totebag/shopping bag mockups with large print areas for design preview.

### mockups/pillow_mockup.py
Creates pillow and cushion mockups with texture and shadow effects for realistic presentation.

### mockups/mockup_api.py
Unified API module providing a consistent interface for all mockup generators. Recommended for programmatic use.

## 🎨 Features

- ✨ High-quality, production-ready mockups
- 🔄 Support for multiple product types
- 💡 Simple and intuitive API
- ⚡ Fast batch processing capabilities
- 🎯 Precise image positioning and scaling
- 🌈 Realistic shadow and lighting effects
- 📐 Customizable output dimensions
- 🔧 Extensible architecture for adding new products

## 🐛 Troubleshooting

### ImageMagick Not Found
Ensure ImageMagick is installed and the `magick` or `convert` command is available in your PATH.

### Wand Import Error
Install Wand properly: `pip install --upgrade Wand`

### Image Quality Issues
Ensure input images have adequate resolution (minimum 300x300px recommended).

## 🤝 Contributing

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Support

For issues, questions, or feature requests, please open an issue on the [GitHub repository](https://github.com/sasank-creates/mockup-imagemagick/issues).

## 🔗 Resources

- [ImageMagick Official Documentation](https://imagemagick.org/)
- [Wand Documentation](https://docs.wand-py.org/)
- [Pillow Documentation](https://pillow.readthedocs.io/)

---

**Version:** 1.0.0  
**Last Updated:** April 2026  
**Author:** Sasank Creates  
**Repository:** [mockup-imagemagick](https://github.com/sasank-creates/mockup-imagemagick)
