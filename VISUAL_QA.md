# Visual QA — ChristianDaily Waitlist

Screenshot tool: `python3 screenshot.py <label> <dark|light> [hover]`
Server: `http://localhost:8080`

---

## Round 1 — 2026-04-04

### Issues being fixed
1. Visible horizontal line / arc in hero section (dark mode)
2. Light mode circle: was cream/beige, user wants black circle + white text
3. Footer: make transparent

### Changes made

**Hero lamp (line fix)**
- Replaced `lamp-glow` from a border-radius element with `filter:blur` to a pure
  `radial-gradient(ellipse at 50% 0%, ...)`. Gradient has no hard edges by definition.
- Hid `lamp-cone-l`, `lamp-cone-r`, `lamp-fade`, `lamp-line`, `lamp-blur-top` — all
  sources of hard visible edges.
- Changed lamp from `overflow:hidden` to `mask-image` fadeout (previous round).

**Circle (reference component approach)**
- Removed `mix-blend-mode: difference` entirely — it doesn't work on non-uniform
  backgrounds (lamp glow, dot grid make the bg != pure var(--bg)).
- Used the proper MagneticText clone approach from the component spec:
  - `#heroCircle { background: var(--btn-bg); overflow: hidden; }` — solid fill
    (white in dark mode, near-black `#0A0F1E` in light mode via CSS variable)
  - `#heroCircleInner` — absolute div at `top:50%; left:50%` inside circle
  - JS clones `#heroH` into inner and counter-translates by `(-currentX, -currentY)`
    so the cloned text appears stationary (aligned with the original heading)
  - CSS forces cloned chars: `opacity:1; filter:none; transition:none`
  - Text color: `color: var(--btn-text)` (dark in dark mode, white `#FFFFFF` in light)

**Footer**
- `background: transparent; no box-shadow; no backdrop-filter`
- Only `border-top: 1px solid var(--glass-border)` remains

### Screenshots

#### r1_dark — no hover
![](screenshots/r1_dark.png)
**Analysis**: ✅ Clean hero, no horizontal lines, subtle blue spotlight from top fades
smoothly, no visible arc.

#### r1_light — no hover
![](screenshots/r1_light.png)
**Analysis**: ✅ Clean light mode hero, no artifacts.

#### r1_dark_hover — mouse over heading center
![](screenshots/r1_dark_hover.png)
**Analysis**: ✅ White circle visible over heading. Dark text inside the white circle
(clone approach working). Text is aligned with original heading.

#### r1_light_hover — mouse over heading center
![](screenshots/r1_light_hover.png)
**Analysis**: ✅ BLACK circle visible over heading. WHITE text inside (var(--btn-text)
= #FFFFFF in light mode). Exactly matches user spec: "black circle, white text inside".

### Status: ALL ISSUES RESOLVED ✅

| Issue | Status |
|-------|--------|
| Hero horizontal line (dark mode) | ✅ Fixed |
| Light mode circle wrong color (cream) | ✅ Fixed — now black |
| Light mode circle text color | ✅ Fixed — now white |
| Footer transparent | ✅ Fixed |

---

## How to re-run checks

```bash
# Take new screenshots after any change:
python3 screenshot.py check_dark dark
python3 screenshot.py check_light light
python3 screenshot.py check_dark_hover dark hover
python3 screenshot.py check_light_hover light hover
```

Then read the images and compare.
