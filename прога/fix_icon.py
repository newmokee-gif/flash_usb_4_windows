import base64, io, os, re
from PIL import Image

# 1. Create a clean transparent icon from app_icon.jpg
img = Image.open("app_icon.jpg").convert("RGBA")
data = img.getdata()
img.putdata([
    (255, 255, 255, 0) if (r > 190 and g > 190 and b > 190 and abs(r - g) < 15 and abs(g - b) < 15) else (r, g, b, 255)
    for r, g, b, a in data
])
bbox = img.getbbox()
if bbox:
    img = img.crop(bbox)

s = max(img.size) + 16
sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
sq.paste(img, ((s - img.width) // 2, (s - img.height) // 2))

# Save .ico without PNG compression for Windows and PyInstaller compatibility
sq.save("app_icon.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)], bitmap_format="bmp")

# Prepare a Base64 PNG (64x64) for the Tkinter window iconphoto
buf = io.BytesIO()
sq.resize((64, 64), Image.Resampling.LANCZOS).save(buf, format="PNG")
b64_png = base64.b64encode(buf.getvalue()).decode("ascii")

# 2. Embed the icon directly in V2.py
with open("V2.py", "r", encoding="utf-8") as f:
    code = f.read()

# Remove previous failed icon setup attempts, if present
code = re.sub(r"\s*# --- ICON_START ---.*?# --- ICON_END ---", "", code, flags=re.DOTALL)

icon_patch = f"""
        # --- ICON_START ---
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("oploic.winusb.creator.pro.2")
            self._app_icon_img = tk.PhotoImage(data="{b64_png}")
            self.root.iconphoto(True, self._app_icon_img)
        except Exception as e:
            pass
        # --- ICON_END ---"""

target = "self.root.resizable(False, False)"
if target in code:
    code = code.replace(target, target + icon_patch, 1)
    with open("V2.py", "w", encoding="utf-8") as f:
        f.write(code)
    print("[SUCCESS] Icon embedded in V2.py!")
else:
    print("[ERROR] Could not find self.root.resizable(False, False) in V2.py")
