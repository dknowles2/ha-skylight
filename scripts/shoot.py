#!/usr/bin/env python3
"""Regenerate the card screenshots in docs/images/.

    python scripts/shoot.py

Screenshots rot. These exist so the ones in the documentation can be remade from
the card as it is now rather than recaptured by hand and quietly left behind, so
everything that decides what they look like is fixed here: the data, the date,
the window size and the theme colours in `shot.html`. Running this against an
unchanged card produces byte-identical files and no diff.

The cards are loaded from `custom_components/` over a local server rather than
copied anywhere, so a screenshot is always of the card that ships.

Needs Google Chrome and Pillow. Neither is a dependency of the integration, and
this is not run by CI — it is a tool for whoever is changing a card.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import pathlib
import socketserver
import subprocess
import threading
from collections.abc import Iterator
from typing import NamedTuple

from PIL import Image, ImageChops

ROOT = pathlib.Path(__file__).parent.parent
OUT = ROOT / "docs" / "images"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: Retina, because these are read on laptop screens.
SCALE = 2
#: Background left around a trimmed card, in CSS pixels.
MARGIN = 16 * SCALE


class Shot(NamedTuple):
    """One screenshot, in both themes."""

    #: What the files are called: `<name>-light.png` and `<name>-dark.png`.
    name: str
    #: The layout to ask `shot.html` for.
    card: str
    #: The card's width in CSS pixels, or 0 to let the layout decide.
    width: int
    #: The browser window, which only has to be big enough to hold the card.
    window: tuple[int, int]
    #: Whether to trim the leftover background off afterwards.
    trim: bool = True


SHOTS = [
    Shot("chores", "chores", 440, (480, 620)),
    Shot("rewards", "rewards", 440, (480, 700)),
    # Already exactly the size of the panel it depicts, so it is rendered edge
    # to edge and kept that way.
    Shot("echo-show", "echo", 0, (960, 480), trim=False),
]
THEMES = ("light", "dark")


@contextlib.contextmanager
def serving() -> Iterator[int]:
    """Serve the repository on a spare port.

    A server rather than `file://` because the page loads the cards as ES
    modules, which the file scheme refuses.
    """
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """`SimpleHTTPRequestHandler` without a line of log per request."""

    def log_message(self, format: str, *args: object) -> None:
        """Say nothing."""


def shoot(port: int, shot: Shot, theme: str) -> pathlib.Path:
    """Photograph one card and return where it landed."""
    path = OUT / f"{shot.name}-{theme}.png"
    url = (
        f"http://127.0.0.1:{port}/scripts/shot.html"
        f"?card={shot.card}&theme={theme}&width={shot.width}"
    )
    subprocess.run(
        [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={SCALE}",
            # Long enough for the progress bars to finish their transition;
            # without it they are caught mid-animation at a random width.
            "--virtual-time-budget=3000",
            f"--window-size={shot.window[0]},{shot.window[1]}",
            f"--screenshot={path}",
            url,
        ],
        check=True,
        capture_output=True,
    )
    return path


def finish(path: pathlib.Path, *, trim: bool) -> tuple[int, int]:
    """Trim the background to an even margin, and shrink the file.

    Chrome photographs the whole window, so each page is given more height than
    its card needs and the leftover is measured off here rather than guessed —
    a card that grows a row still comes out with the same margin around it.

    These are flat-coloured interfaces, so a 256-colour palette is visually
    identical to the truecolour original at about a third of the size. Worth
    doing: they are committed, and every clone pays for them.
    """
    image = Image.open(path).convert("RGB")
    if trim:
        background = Image.new("RGB", image.size, image.getpixel((0, 0)))
        box = ImageChops.difference(image, background).getbbox()
        if box is None:
            raise SystemExit(f"{path.name} came out blank — the card did not render")
        left, top, right, bottom = box
        image = image.crop(
            (
                max(left - MARGIN, 0),
                max(top - MARGIN, 0),
                min(right + MARGIN, image.width),
                min(bottom + MARGIN, image.height),
            )
        )
    image.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE).save(path, optimize=True)
    return image.size


def main() -> None:
    """Render every screenshot the documentation uses."""
    if not pathlib.Path(CHROME).exists():
        raise SystemExit(f"Google Chrome is needed to take the screenshots, and is not at {CHROME}")
    OUT.mkdir(parents=True, exist_ok=True)

    with serving() as port:
        for shot in SHOTS:
            for theme in THEMES:
                path = shoot(port, shot, theme)
                width, height = finish(path, trim=shot.trim)
                size = path.stat().st_size // 1024
                print(f"{path.relative_to(ROOT)}  {width}x{height}  {size}kB")


if __name__ == "__main__":
    main()
