"""
Retra Screenshot Capture
Captures the active window (not the whole screen) using macOS screencapture.
The full-resolution capture is read for text first (small text OCRs far
better before downscaling), then compress() shrinks it to 1280px max width.
Images are short-lived: they are deleted after a couple of days once their
text is saved (see purge_old_images).
"""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class ScreenshotCapture:
    def __init__(self, output_dir: str | Path, quality: int = 40, max_width: int = 1280):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.quality = quality
        self.max_width = max_width

    def capture(self, window_id: int) -> Optional[str]:
        """
        Capture one window at full resolution. Returns the filepath or None
        if capture failed. Call compress() once its text has been read.

        Only the given window is captured, so other windows, notifications and
        the menu bar never end up in the image.
        """
        timestamp = datetime.now()
        date_dir = self.output_dir / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(exist_ok=True)
        filepath = date_dir / f"screen_{timestamp.strftime('%H%M%S')}.jpg"

        try:
            # macOS screencapture: -x no sound, -o no window shadow,
            # -l capture only this window id, -t jpg format
            result = subprocess.run(
                ["screencapture", "-x", "-o", f"-l{window_id}", "-t", "jpg", str(filepath)],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0 or not filepath.exists():
                return None
            return str(filepath)

        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"[Retra] Screenshot capture failed: {e}")
            return None

    def compress(self, filepath: str):
        """
        Downscale + recompress a capture and make its thumbnail. Skips images
        that already have a thumbnail, so it's safe to call twice.

        Pipeline: resize to max_width -> compress quality -> thumbnail
        A typical retina screenshot goes from ~2MB to ~80-150KB after resize+compress.
        """
        path = Path(filepath)
        thumb_path = path.parent / f"thumb_{path.name}"
        if not path.exists() or thumb_path.exists():
            return
        try:
            # Downscale to max_width (e.g. 2560px retina -> 1280px)
            # This is the biggest storage win: cuts file size ~75%
            # -Z resizes to fit within a box while preserving aspect ratio
            subprocess.run(
                ["sips", "-Z", str(self.max_width), str(path)],
                capture_output=True, timeout=10,
            )
            # Compress with quality setting
            subprocess.run(
                ["sips", "-s", "formatOptions", str(self.quality), str(path)],
                capture_output=True, timeout=10,
            )
            # Generate thumbnail (320px width from the already-resized image — fast)
            subprocess.run(
                ["sips", "-Z", "320", str(path), "--out", str(thumb_path)],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"[Retra] Screenshot compress failed: {e}")

    @staticmethod
    def delete(filepath: str):
        """Delete an image and its thumbnail, then its date folder if now empty."""
        path = Path(filepath)
        path.unlink(missing_ok=True)
        (path.parent / f"thumb_{path.name}").unlink(missing_ok=True)
        try:
            if not any(p for p in path.parent.iterdir() if p.name != ".DS_Store"):
                (path.parent / ".DS_Store").unlink(missing_ok=True)
                path.parent.rmdir()
        except OSError:
            pass


def purge_old_images(db, screenshots_dir: Path, retention_days: int) -> tuple[int, int]:
    """Delete screenshot images older than retention whose text is saved,
    plus stray image files no row points to. Returns (purged, strays)."""
    cutoff = datetime.now() - timedelta(days=retention_days)
    root = Path(screenshots_dir).resolve()

    purged = []
    for screenshot_id, filepath in db.get_images_to_purge(cutoff):
        path = Path(filepath).resolve()
        if root in path.parents:
            ScreenshotCapture.delete(str(path))
        purged.append(screenshot_id)
    if purged:
        db.mark_images_deleted(purged)

    referenced = {Path(f).resolve() for f in db.get_screenshot_files()}
    strays = 0
    for date_dir in root.iterdir() if root.exists() else []:
        try:
            if datetime.strptime(date_dir.name, "%Y-%m-%d").date() >= cutoff.date():
                continue
        except ValueError:
            continue
        # Anything else in an expired folder is junk, including hidden
        # partial files screencapture leaves behind when it times out.
        for f in date_dir.iterdir():
            image = date_dir / f.name.removeprefix("thumb_")
            if f.is_file() and image.resolve() not in referenced:
                f.unlink(missing_ok=True)
                strays += f.name.endswith(".jpg") or ".jpg-" in f.name
        try:
            date_dir.rmdir()
        except OSError:
            pass  # still holds images waiting for their text
    return len(purged), strays
