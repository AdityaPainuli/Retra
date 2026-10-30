"""
On-device text extraction for screenshots.

Uses Apple's Vision framework (runs locally, no network) to read the text in
a screenshot, then masks things that should never be stored in plain text:
emails, card numbers, API keys/tokens, private keys. The text is what Retra
keeps long term; the image itself is deleted after a short window.
"""

import logging
import re
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("retra")

MAX_TEXT_CHARS = 20000


def recognize_text(image_path: str | Path) -> Optional[str]:
    """Return the text Vision finds in the image, one line per observation
    (top to bottom), or None if the image can't be read."""
    import objc
    import Vision
    from Foundation import NSURL

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(image_path))
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        ok, _err = handler.performRequests_error_([request], None)
        if not ok:
            return None
        lines = []
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if candidates:
                lines.append(str(candidates[0].string()))
        return "\n".join(lines)


# ── Redaction ─────────────────────────────────────────────────────

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_TOKEN_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}"                 # OpenAI / Anthropic style
    r"|gh[pousr]_[A-Za-z0-9]{20,}"           # GitHub
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"         # Slack
    r"|AKIA[0-9A-Z]{16}"                     # AWS access key id
    r"|AIza[0-9A-Za-z_-]{30,}"               # Google API key
    r"|(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"  # Stripe
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"  # JWT
    r")"
)
# Long unbroken strings mixing letters and digits read like secrets/hashes.
_LONG_SECRET_RE = re.compile(r"\b(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{32,}\b")
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# "password: hunter2", "api_key = abc", "pin 1234". With only a space before
# the value, it must contain a digit so prose like "password reset" survives.
_SECRET_FIELD_RE = re.compile(
    r"(?i)\b(password|passwd|passcode|pin|secret|api[_ ]?key|access[_ ]?token|token|otp)\b"
    r"(\s*[:=]\s*(?=\S{4,})|\s+(?=\S*\d)(?=\S{4,}))\S+"
)
# "Your verification code is 482913", "OTP for login: 1234"
_OTP_RE = re.compile(r"(?i)\b(otp|one[- ]time|verification|security code|login code|code is)\b([^\n\d]{0,25})\d{4,8}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return "[card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)


def redact(text: str) -> str:
    """Mask secrets and personal identifiers in OCR text."""
    text = _PRIVATE_KEY_RE.sub("[private key]", text)
    text = _TOKEN_RE.sub("[token]", text)
    text = _SECRET_FIELD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = _OTP_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[code]", text)
    text = _LONG_SECRET_RE.sub("[token]", text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _CARD_RE.sub(_mask_card, text)
    return text


def extract_screen_text(image_path: str | Path) -> Optional[str]:
    """OCR + redact. Returns '' when the image has no text, None on failure."""
    raw = recognize_text(image_path)
    if raw is None:
        return None
    return redact(raw)[:MAX_TEXT_CHARS]


# ── Processing ────────────────────────────────────────────────────

def ocr_screenshot(db, screenshot_id: int, filepath: str) -> bool:
    """Extract and save one screenshot's text. Returns False if Vision errored;
    the row then stays pending (and its image is kept) for a later retry."""
    if not filepath or not Path(filepath).exists():
        db.set_screen_text(screenshot_id, "")  # image gone, nothing to read
        return True
    try:
        text = extract_screen_text(filepath)
    except Exception as e:
        log.warning(f"Screen text extraction failed for {filepath}: {e}")
        return False
    # None means the file isn't a readable image; retrying won't help.
    db.set_screen_text(screenshot_id, text or "")
    return True


def process_backlog(db, should_stop: Callable[[], bool] = lambda: False) -> tuple[int, int]:
    """OCR every pending screenshot, oldest first. Returns (done, failed)."""
    done = failed = 0
    last_id = 0
    while not should_stop():
        batch = db.get_screenshots_pending_text(after_id=last_id, limit=50)
        if not batch:
            break
        for screenshot_id, filepath in batch:
            if should_stop():
                break
            last_id = screenshot_id
            if ocr_screenshot(db, screenshot_id, filepath):
                done += 1
            else:
                failed += 1
    return done, failed
