# Application icon

`source.png` is the approved generated artwork, preserved with transparency.
Run `python scripts/build_icons.py` from the repository root to regenerate the
runtime PNG, Windows ICO, macOS ICNS, Linux launcher SVG and legacy JPEG preview.
Regeneration requires Pillow; the application itself does not.

All GUI modes share `прога/app_icon.py`. Frozen builds bundle the runtime PNG;
DEB/RPM install it alongside the Python module. Existing release binaries are
unchanged until a new version is built and published.
