"""Convert the approved generated PNG into platform icon formats.

Requires Pillow only when regenerating assets, not at application runtime.
No recoloring, background removal or redesign is performed here.
"""
import base64
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    with Image.open(ROOT / "packaging/icons/source.png") as source:
        icon = source.convert("RGBA")
        if icon.width != icon.height:
            raise ValueError("The approved icon must be square")
        icon.resize((256, 256), Image.Resampling.LANCZOS).save(ROOT / "прога/app_icon.png")
        icon.save(ROOT / "прога/app_icon.ico", format="ICO",
                  sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
        icon.resize((1024, 1024), Image.Resampling.LANCZOS).save(
            ROOT / "packaging/icons/app_icon.icns", format="ICNS")
        # Legacy source distributions refer to a JPEG preview. Do not leave the
        # previous artwork there; JPEG alone cannot retain transparency.
        preview = Image.new("RGB", icon.size, "white")
        preview.paste(icon, mask=icon.getchannel("A"))
        preview.save(ROOT / "прога/app_icon.jpg", quality=95)
    # Preserve the generated artwork exactly inside the existing Linux SVG
    # launcher slot, without external file references or SVG renderer dependencies.
    encoded = base64.b64encode((ROOT / "прога/app_icon.png").read_bytes()).decode("ascii")
    (ROOT / "packaging/flash-usb.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="256" height="256" viewBox="0 0 256 256">\n'
        f'  <image width="256" height="256" xlink:href="data:image/png;base64,{encoded}"/>\n'
        '</svg>\n', encoding="utf-8")


if __name__ == "__main__":
    main()
