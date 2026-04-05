"""
Retra Screenshot Capture
Captures periodic screenshots using macOS screencapture utility.
Optimized: downscales to 1280px max width to reduce storage by ~75%.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


class ScreenshotCapture:
    def __init__(self, output_dir: str | Path, quality: int = 40, max_width: int = 1280):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.quality = quality
        self.max_width = max_width

    def capture(self) -> Optional[str]:
        """
        Capture a screenshot, downscale, and save as compressed JPEG.
        Returns the filepath or None if capture failed.

        Pipeline: screencapture -> resize to max_width -> compress quality -> thumbnail
        A typical retina screenshot goes from ~2MB to ~80-150KB after resize+compress.
        """
        timestamp = datetime.now()
        date_dir = self.output_dir / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(exist_ok=True)

        filename = f"screen_{timestamp.strftime('%H%M%S')}.jpg"
        filepath = date_dir / filename

        try:
            # macOS screencapture: -x no sound, -t jpg format, -C include cursor
            result = subprocess.run(
                ["screencapture", "-x", "-t", "jpg", str(filepath)],
                capture_output=True, timeout=10,
            )

            if result.returncode != 0 or not filepath.exists():
                return None

            # Downscale to max_width (e.g. 2560px retina -> 1280px)
            # This is the biggest storage win: cuts file size ~75%
            # -Z resizes to fit within a box while preserving aspect ratio
            subprocess.run(
                ["sips", "-Z", str(self.max_width), str(filepath)],
                capture_output=True, timeout=10,
            )

            # Compress with quality setting
            subprocess.run(
                ["sips", "-s", "formatOptions", str(self.quality), str(filepath)],
                capture_output=True, timeout=10,
            )

            # Generate thumbnail (320px width from the already-resized image — fast)
            thumb_path = date_dir / f"thumb_{filename}"
            subprocess.run(
                ["sips", "-Z", "320", str(filepath), "--out", str(thumb_path)],
                capture_output=True, timeout=10,
            )

            return str(filepath)

        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"[Retra] Screenshot capture failed: {e}")
            return None

    def cleanup_old(self, retention_days: int = 30):
        """Remove screenshot directories older than retention period."""
        cutoff = datetime.now().date()
        for date_dir in self.output_dir.iterdir():
            if date_dir.is_dir():
                try:
                    dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                    if (cutoff - dir_date).days > retention_days:
                        import shutil
                        shutil.rmtree(date_dir)
                except ValueError:
                    continue
