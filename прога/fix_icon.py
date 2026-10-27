"""Compatibility entry point for regenerating icons from the approved PNG."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_icons.py"), run_name="__main__")
