"""Images pasted into a box whose words go to Cursor (a screenshot of what
is meant), on their way to Cursor -- and nowhere else.

The page sends them with the request that starts the run; the server
`save`s them in the job's directory; the run's process `take`s them when it
starts -- read into memory, the files deleted at once -- and they go with
its first message to Cursor (cursor_call.attach). Nothing keeps them: not
the brief, not the logs, not a commit. A request that does not start
removes them (`clear`), and so does the next request of the job.
"""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import time

DIR = "paste"                       # under the job's directory (.workbench/<job>/)
MAX_IMAGES = 4
MAX_EACH = 5 * 1024 * 1024          # bytes, as an image, not as base64
MAX_TOTAL = 10 * 1024 * 1024
MAX_AGE = 600                       # seconds: older ones are a request that never ran
# What a picture is, read from its first bytes -- not from what the page says.
KINDS = (("image/png", "png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
         ("image/jpeg", "jpg", lambda b: b.startswith(b"\xff\xd8\xff")),
         ("image/gif", "gif", lambda b: b[:6] in (b"GIF87a", b"GIF89a")),
         ("image/webp", "webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"))


def _dir(job_dir: str) -> str:
    return os.path.join(job_dir, DIR)


def clear(job_dir: str) -> None:
    shutil.rmtree(_dir(job_dir), ignore_errors=True)


def kind(raw: bytes):
    """(mime type, extension) of an image this accepts, else None."""
    for mime, ext, test in KINDS:
        if test(raw):
            return mime, ext
    return None


def save(job_dir: str, images) -> int:
    """Keep the pasted images for the run about to start; returns how many.
    Each is {"data": base64 or a data: URL}. What was kept before goes
    first, whatever comes now. ValueError says what is wrong."""
    clear(job_dir)
    if not images:
        return 0
    if not isinstance(images, list):
        raise ValueError("images: a list was expected")
    if len(images) > MAX_IMAGES:
        raise ValueError(f"{len(images)} images; at most {MAX_IMAGES} go with one request")
    raws, total = [], 0
    for n, im in enumerate(images, 1):
        data = im.get("data") if isinstance(im, dict) else None
        if not isinstance(data, str) or not data:
            raise ValueError(f"image {n}: no data")
        if data.startswith("data:"):
            data = data.split(",", 1)[-1]
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"image {n}: not base64") from None
        found = kind(raw)
        if not found:
            raise ValueError(f"image {n}: not a PNG, JPEG, GIF or WebP picture")
        if len(raw) > MAX_EACH:
            raise ValueError(f"image {n}: {len(raw) / 1048576:.1f} MB; at most "
                             f"{MAX_EACH // 1048576} MB each")
        total += len(raw)
        raws.append((raw, found[1]))
    if total > MAX_TOTAL:
        raise ValueError(f"{total / 1048576:.1f} MB of images; at most {MAX_TOTAL // 1048576} MB in all")
    os.makedirs(_dir(job_dir), exist_ok=True)
    for n, (raw, ext) in enumerate(raws, 1):
        with open(os.path.join(_dir(job_dir), f"{n}.{ext}"), "wb") as fh:
            fh.write(raw)
    return len(raws)


def take(job_dir: str) -> list:
    """The images kept for this run, as the Cursor SDK takes them
    ({"data": base64, "mimeType": ...}); the files are deleted here, read
    or not. Images older than MAX_AGE belong to a request that never ran,
    and are not taken."""
    folder = _dir(job_dir)
    if not os.path.isdir(folder):
        return []
    out = []
    try:
        if time.time() - os.path.getmtime(folder) <= MAX_AGE:
            for name in sorted(os.listdir(folder))[:MAX_IMAGES]:
                with open(os.path.join(folder, name), "rb") as fh:
                    raw = fh.read(MAX_EACH + 1)
                found = kind(raw)
                if found and len(raw) <= MAX_EACH:
                    out.append({"data": base64.b64encode(raw).decode("ascii"), "mimeType": found[0]})
    except OSError:
        out = []
    finally:
        clear(job_dir)
    return out
