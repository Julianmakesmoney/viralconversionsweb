"""Screenshot utility for iterative visual QA — supports hover state."""
import sys, os
from playwright.sync_api import sync_playwright

URL = "http://localhost:8080/"
OUT = "/Users/verboom/christaindaily waitlist/screenshots"
os.makedirs(OUT, exist_ok=True)

def take(label, theme="dark", hover=False, wait_ms=2500):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1280, "height": 800})
        page.goto(URL, wait_until="networkidle")
        page.evaluate(f"document.documentElement.setAttribute('data-theme','{theme}')")
        page.wait_for_timeout(wait_ms)

        if hover:
            # Find heroHWrap and hover over the center of the heading
            wrap = page.locator("#heroHWrap")
            box = wrap.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.wait_for_timeout(800)  # let circle expand

        path = f"{OUT}/{label}.png"
        page.screenshot(path=path, full_page=False)
        b.close()
        print(f"Saved: {path}")

if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "shot"
    theme = sys.argv[2] if len(sys.argv) > 2 else "dark"
    hover = len(sys.argv) > 3 and sys.argv[3] == "hover"
    take(label, theme, hover)
