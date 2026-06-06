import os
import math
import subprocess
import numpy as np
from PIL import Image, ImageDraw

def generate_logo():
    print("Generating 1024x1024 base logo image...")
    # 1. Create a diagonal gradient from Emerald Green (#00C896) to Electric Blue (#00A2FF)
    xx, yy = np.meshgrid(np.arange(1024), np.arange(1024))
    ratio = (xx + yy) / 2048.0
    r = np.zeros_like(ratio, dtype=np.uint8)
    g = (200 * (1 - ratio) + 162 * ratio).astype(np.uint8)
    b = (150 * (1 - ratio) + 255 * ratio).astype(np.uint8)
    a = np.full_like(ratio, 255, dtype=np.uint8)
    grad_arr = np.stack([r, g, b, a], axis=-1)
    gradient = Image.fromarray(grad_arr, "RGBA")

    # 2. Create base image (transparent background)
    img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    base_draw = ImageDraw.Draw(img)

    # 3. Draw solid dark background rounded rectangle (#0D1117)
    base_draw.rounded_rectangle([64, 64, 960, 960], radius=200, fill=(13, 17, 23, 255))

    # 4. Draw border gradient
    border_mask = Image.new("L", (1024, 1024), 0)
    border_draw = ImageDraw.Draw(border_mask)
    border_draw.rounded_rectangle([64, 64, 960, 960], radius=200, fill=255)
    border_draw.rounded_rectangle([76, 76, 948, 948], radius=188, fill=0)
    
    # 80% opacity for the border gradient
    border_mask_arr = np.array(border_mask)
    border_mask_arr = (border_mask_arr * 0.8).astype(np.uint8)
    border_mask = Image.fromarray(border_mask_arr, "L")
    img.paste(gradient, mask=border_mask)

    # 5. Create mask for the central symbol
    symbol_mask = Image.new("L", (1024, 1024), 0)
    symbol_draw = ImageDraw.Draw(symbol_mask)

    # A. Circular Arrow (Radius = 240, Bounding Box = [272, 272, 752, 752])
    # Sweep from -60 deg to 240 deg clockwise
    symbol_draw.arc([272, 272, 752, 752], start=-60, end=240, fill=255, width=24)

    # Arrowhead at end (240 deg, which is at x=392, y=304) pointing at 330 deg (clockwise)
    cx, cy = 392, 304
    angle_rad = math.radians(330)
    size = 50
    tip_x = cx + size * math.cos(angle_rad)
    tip_y = cy + size * math.sin(angle_rad)
    
    left_x = cx + (size / 1.5) * math.cos(angle_rad + math.radians(135))
    left_y = cy + (size / 1.5) * math.sin(angle_rad + math.radians(135))
    right_x = cx + (size / 1.5) * math.cos(angle_rad - math.radians(135))
    right_y = cy + (size / 1.5) * math.sin(angle_rad - math.radians(135))
    symbol_draw.polygon([(tip_x, tip_y), (left_x, left_y), (right_x, right_y)], fill=255)

    # B. Microchip Body (Box [392, 392, 632, 632], width 240, height 240, rx 30)
    symbol_draw.rounded_rectangle([392, 392, 632, 632], radius=30, outline=255, width=20)

    # C. Pins
    pin_length = 52
    pin_width = 20
    for x in [452, 512, 572]:
        symbol_draw.line([(x, 392), (x, 392 - pin_length)], fill=255, width=pin_width, joint="round")
        symbol_draw.line([(x, 632), (x, 632 + pin_length)], fill=255, width=pin_width, joint="round")
    for y in [452, 512, 572]:
        symbol_draw.line([(392, y), (392 - pin_length, y)], fill=255, width=pin_width, joint="round")
        symbol_draw.line([(632, y), (632 + pin_length, y)], fill=255, width=pin_width, joint="round")

    # D. Internal Circuitry
    core_radius = 16
    cores = [(472, 472), (552, 472), (472, 552), (552, 552)]
    for cx, cy in cores:
        symbol_draw.ellipse([cx - core_radius, cy - core_radius, cx + core_radius, cy + core_radius], fill=255)
    
    # Internal connections
    symbol_draw.line([(472, 472), (552, 552)], fill=255, width=12)
    symbol_draw.line([(552, 472), (472, 552)], fill=255, width=12)
    
    # Outward paths
    symbol_draw.line([(472, 472), (430, 472)], fill=255, width=12)
    symbol_draw.line([(552, 472), (594, 472)], fill=255, width=12)
    symbol_draw.line([(472, 552), (430, 552)], fill=255, width=12)
    symbol_draw.line([(552, 552), (594, 552)], fill=255, width=12)

    # Paste the symbol gradient on the base image
    img.paste(gradient, mask=symbol_mask)
    return img

def main():
    assets_dir = "/Users/user/Desktop/Final_year/src/assets"
    
    # Generate base 1024x1024 image
    base_img = generate_logo()
    
    # Define iconset output mappings
    iconset_dir = os.path.join(assets_dir, "icon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)
    
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_64x64.png": 64,
        "icon_64x64@2x.png": 128,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024
    }
    
    print("Writing PNGs to icon.iconset folder...")
    for filename, size in sizes.items():
        resized = base_img.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(os.path.join(iconset_dir, filename), "PNG")
        
    # Overwrite main icon.png (512x512) and icon_proper.png (1024x1024)
    print("Overwriting main PNG icons...")
    base_img.resize((512, 512), Image.Resampling.LANCZOS).save(os.path.join(assets_dir, "icon.png"), "PNG")
    base_img.save(os.path.join(assets_dir, "icon_proper.png"), "PNG")

    # Save as ICO for Windows notifications
    print("Generating icon.ico for Windows...")
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base_img.save(os.path.join(assets_dir, "icon.ico"), format="ICO", sizes=ico_sizes)
    
    # Generate .icns file using iconutil on macOS
    icns_path = os.path.join(assets_dir, "icon.icns")
    print(f"Compiling icon.iconset into {icns_path} using iconutil...")
    try:
        subprocess.run(["iconutil", "-c", "icns", iconset_dir], check=True)
        print("Successfully compiled icon.icns!")
    except Exception as e:
        print(f"Error compiling icon.icns: {e}")
        print("Note: iconutil is only available on macOS.")

    # 6. Generate SVG file representation
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <!-- Background Gradient for subtle depth -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A" />
      <stop offset="100%" stop-color="#050814" />
    </linearGradient>
    <!-- Main Symbol Gradient (Emerald Green to Electric Blue) -->
    <linearGradient id="symbolGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00C896" />
      <stop offset="100%" stop-color="#00A2FF" />
    </linearGradient>
    <!-- Border Gradient -->
    <linearGradient id="borderGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00C896" stop-opacity="0.8" />
      <stop offset="100%" stop-color="#00A2FF" stop-opacity="0.3" />
    </linearGradient>
  </defs>

  <!-- App Icon Rounded Background -->
  <rect x="32" y="32" width="448" height="448" rx="100" fill="url(#bgGrad)" stroke="url(#borderGrad)" stroke-width="6" />

  <!-- Optimization Circular Arrow -->
  <path d="M 316 152 A 120 120 0 1 1 196 152" fill="none" stroke="url(#symbolGrad)" stroke-width="12" stroke-linecap="round" />
  
  <!-- Arrowhead at (196, 152) pointing at 330 deg -->
  <polygon points="217.6 139.5, 192.7 164.0, 183.9 148.7" fill="url(#symbolGrad)" stroke="url(#symbolGrad)" stroke-width="2" stroke-linejoin="round" />

  <!-- Central Microchip -->
  <rect x="196" y="196" width="120" height="120" rx="15" fill="none" stroke="url(#symbolGrad)" stroke-width="10" stroke-linejoin="round" />
  
  <!-- Microchip Pins -->
  <!-- Top Pins -->
  <line x1="226" y1="196" x2="226" y2="170" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="256" y1="196" x2="256" y2="170" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="286" y1="196" x2="286" y2="170" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  
  <!-- Bottom Pins -->
  <line x1="226" y1="316" x2="226" y2="342" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="256" y1="316" x2="256" y2="342" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="286" y1="316" x2="286" y2="342" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />

  <!-- Left Pins -->
  <line x1="196" y1="226" x2="170" y2="226" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="196" y1="256" x2="170" y2="256" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="196" y1="286" x2="170" y2="286" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />

  <!-- Right Pins -->
  <line x1="316" y1="226" x2="342" y2="226" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="316" y1="256" x2="342" y2="256" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />
  <line x1="316" y1="286" x2="342" y2="286" stroke="url(#symbolGrad)" stroke-width="10" stroke-linecap="round" />

  <!-- Internal Circuitry (Nodes & Connections) -->
  <circle cx="236" cy="236" r="8" fill="url(#symbolGrad)" />
  <circle cx="276" cy="236" r="8" fill="url(#symbolGrad)" />
  <circle cx="236" cy="276" r="8" fill="url(#symbolGrad)" />
  <circle cx="276" cy="276" r="8" fill="url(#symbolGrad)" />

  <line x1="236" y1="236" x2="276" y2="276" stroke="url(#symbolGrad)" stroke-width="6" />
  <line x1="276" y1="236" x2="236" y2="276" stroke="url(#symbolGrad)" stroke-width="6" />
  
  <line x1="236" y1="236" x2="216" y2="236" stroke="url(#symbolGrad)" stroke-width="6" />
  <line x1="276" y1="236" x2="296" y2="236" stroke="url(#symbolGrad)" stroke-width="6" />
  <line x1="236" y1="276" x2="216" y2="276" stroke="url(#symbolGrad)" stroke-width="6" />
  <line x1="276" y1="276" x2="296" y2="276" stroke="url(#symbolGrad)" stroke-width="6" />
</svg>
"""
    svg_path = os.path.join(assets_dir, "icon.svg")
    with open(svg_path, "w") as f:
        f.write(svg_content)
    print(f"Successfully wrote SVG icon to {svg_path}!")

if __name__ == "__main__":
    main()
