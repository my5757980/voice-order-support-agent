"""Render the submission images from their HTML sources.

    python assets/src/render.py

cover.html -> assets/cover.png   (1920x1080, 16:9 — a hard format requirement)
slides.html -> assets/slides.pdf (one 1920x1080 page per slide, text kept as text)
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path(__file__).resolve().parent
OUT = SRC.parent

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    page.goto((SRC / "cover.html").as_uri(), wait_until="load")
    page.screenshot(path=str(OUT / "cover.png"), full_page=False)

    page.goto((SRC / "slides.html").as_uri(), wait_until="load")
    page.pdf(path=str(OUT / "slides.pdf"), width="1920px", height="1080px",
             print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
    browser.close()

print("wrote", OUT / "cover.png", "and", OUT / "slides.pdf")
