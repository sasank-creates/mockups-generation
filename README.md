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

Generate mockups using individual scripts:

```bash
# Bottle mockup
python mockups/bottle_mockup.py -i products/image1.png -o tests/bottle-result.png

# T-shirt mockup
python mockups/tshirt_mockup.py -i products/image1.png -o tests/tshirt-result.png

# Mug mockup
python mockups/mug_mockup.py -i products/image1.png -o tests/mug-result.png

# Clock mockup
python mockups/clock_mockup.py -i products/image1.png -o tests/clock-result.png

# Frame mockup
python mockups/frame_mockup.py -i products/image1.png -o tests/frame-result.png

# Totebag mockup
python mockups/totebag_mockup.py -i products/image1.png -o tests/totebag-result.png

# Pillow mockup
python mockups/pillow_mockup.py -i products/image1.png -o tests/pillow-result.png
```

### Python API

Programmatically generate mockups in your Python code:

```python
from mockups.mockup_api import generate_mockup

# Generate a bottle mockup
result = generate_mockup(
    product_type='bottle',
    input_image='products/image1.png',
    output_path='tests/bottle_output.png'
)

# Generate multiple mockups
products = ['bottle', 'tshirt', 'mug', 'clock', 'frame', 'totebag', 'pillow']
for product in products:
    generate_mockup(
        product_type=product,
        input_image='products/image1.png',
        output_path=f'tests/{product}_result.png'
    )
```

## �� Dependencies

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
