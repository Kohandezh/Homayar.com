# Design decisions for the Homayar website (turn 3, owner palette and 3D scenes)

Short record of what the site uses and where each choice came from. Skill output lives in
`design-system/homayar/MASTER.md` and `design-system/homayar/pages/*.md` (re-generated on
2026-09-30 with `--variance 8 --motion 10 --density 5`). The first two designs (light warm, then
dark warm) were rejected by the owner. Their notes are in git history (`69da721`, `cc9c8c0`).
The CSS tokens in `app/globals.css` must match the hex values listed here.

## Concept: walk into the machine

The site is a flight through the product. One persistent WebGL canvas sits behind every page.
Scrolling moves the camera forward through three kinds of space:

1. **Wire grid.** A wireframe tunnel with a glowing sound wave in the middle (the call).
2. **Warp.** The tunnel narrows to a bright square, the screen flashes to the theme color, and you
   come out in a different space (from the call into the analysis).
3. **Brain.** A neural network. Inside it, a customer and an agent talk. A glowing core reads each
   line, and the call report grows toward you and fades away.

Every page uses the same language: the hero is a short flight through the grid, the body sits on a
calm particle field, and one signature scene per page tells that page's story. Text is always
server-rendered and readable without JavaScript. Motion is a layer on top.

Named inspirations (2025 to 2026, inspiration only, nothing copied):

- Lusion and Active Theory: one WebGL scene that reacts to scroll and pointer across a whole site.
- Linear and Vercel: dark restraint, hairlines, the product surface as the hero.
- ElevenLabs: the voice waveform as the brand mark.
- Apple product pages: pinned scroll chapters where the object moves toward the camera.
- Raycast: the command palette as a marketing surface.
- Igloo Inc and Unseen Studio: page transitions that fade to a solid color with a small loader.

## Skill runs used (ui-ux-pro-max, 2026-09-30)

| Run | Command (short form) | What we took from it |
|---|---|---|
| A | `"dark immersive 3D AI voice analytics SaaS call center cyan teal trust enterprise" --design-system --variance 8 --motion 10 --density 5 --persist --force -p Homayar` | Pattern "Product Demo + Features": demo only when it explains value, visible controls, no autoplay under reduced motion, pause off screen, keep the final state as static content. Motion preset "Hover Micro-interaction (Complex)": clamped magnetic pull `* 0.3`, stable handler, neutral state under reduced motion. Checklist: 4.5:1 in light mode, visible focus, 375 to 1440 px. Its palette (red and blue) and style (Brutalism) were not used, see "Overrides". |
| B | `--page home`, `--page product`, `--page security` (same query) | Page files kept from turn 2 (the script does not overwrite them). |
| C | `"dark cyan teal light mode toggle trust" --domain color` | "Trust teal" row (`#0F766E` primary, `#F0FDFA` background, `#134E4A` foreground): confirms a teal family reads as trust, and that a light theme in this family uses a very light teal-tinted background with a deep teal foreground. Hex values replaced by the owner's colors. |
| D | `"3D scroll scene camera fly through scrub pin" --domain gsap` | Pinned scrub timeline preset (`pin: true, scrub: 1, end: '+=150%'`), deterministic section height, `ScrollTrigger.refresh()` after fonts and images, reduced motion renders the final state at once. Warning: many pins hurt mobile, so mobile pins less. |
| E | `"page transition loader progress" --domain gsap` | Page transition presets: exit faster than enter, cap the exit at about 250 ms, preload the destination before the exit tween ends, never block navigation. |
| F | `"3D scroll storytelling reduced motion vestibular fallback" --domain ux` | Parallax and scroll-jacking cause nausea: honor reduced motion and show the final readable state without pinning. |
| G | `"three.js scroll scene performance dispose lazy load" --stack threejs` | Dispose geometry, materials and textures on teardown. Install GSAP separately from three. |
| H | `"theme toggle client component dynamic import canvas" --stack nextjs` | `next/dynamic` for heavy client components, client components as leaves, mark `use client` only where needed. |
| I | `"immersive 3D scroll storytelling product hero" --domain landing` | "Horizontal Scroll Journey" and "Scroll-Triggered Storytelling": keep a normal vertical reading path, disable scroll-jacking under reduced motion, pause off screen, progress indicator. |
| J | `"dark immersive 3D wireframe neural AI cinematic" --domain style` | "Parallax Storytelling": 3 to 5 layers, full-screen chapters, skip option, mobile alternative, reduced motion fallback. |
| K | `"Persian Arabic sans serif bilingual dark product" --domain typography` | No Persian pairing in the dataset that fits a product site. Vazirmatn stays (SPEC section 3). |

## Overrides of the skill output

- Run A returned a red primary, a blue accent and the Brutalism style. The owner gave six exact
  colors, so the palette below is the owner's. The style is "Parallax Storytelling" from run J.
- Runs A and K list Google Fonts imports. The site makes no third-party request, so fonts are
  self-hosted files under `fonts/`.
- Run D says pin at most one or two sections. The owner asked for a pinned grid flight, a pinned
  brain scene and a pinned horizontal section on the home page. Home has three pins on desktop.
  On phones the horizontal section is a plain vertical stack, so home has two pins there.

## Palette (final tokens)

The six owner colors are `#22bec6`, `#ffffff`, `#0a161a`, `#0310119e`, `#125a5e`, `#031011`.
Every other value is a tint or shade of them. No amber, orange, copper or purple remains.
Contrast was measured with the WCAG formula (`scratchpad/contrast.mjs`). Body text is at least
4.5:1 on every surface it appears on, in both themes.

### Dark theme (default when the system prefers dark)

| Token | Hex | Role | Source and contrast |
|---|---|---|---|
| `--color-bg` | `#0a161a` | Page background | Owner. |
| `--color-bg-deep` | `#031011` | Scene void, loader, footer, transition color | Owner. |
| `--color-surface` | `#0f2126` | Cards and panels | Shade between `#0a161a` and `#125a5e`. |
| `--color-surface-2` | `#132a30` | Raised rows, chips, hover | One step lighter. |
| `--color-border` | `#1a3238` | Hairlines on bg | Derived, non-text. |
| `--color-border-2` | `#234349` | Hairlines on surfaces, inputs | Derived, non-text. |
| `--color-ink` | `#ffffff` | Headings and body text | Owner. On bg 18.4:1, on surface-2 15.0:1. |
| `--color-ink-2` | `#b7c9cc` | Secondary text | Tint of `#125a5e` toward white. On bg 10.7:1, on surface-2 8.7:1. |
| `--color-ink-3` | `#9db3b7` | Small labels, footer text | Tint. On bg 8.4:1, on surface-2 6.8:1. |
| `--color-accent` | `#22bec6` | Primary accent: buttons, glow, scene lines, focus ring | Owner. As text on bg 8.1:1, on surface-2 6.6:1. |
| `--color-accent-text` | `#22bec6` | Accent used as text on dark | Same as accent, it passes. |
| `--color-accent-2` | `#7fdde2` | Light tint for highlights inside scenes | Tint. On bg 11.7:1. |
| `--color-teal-deep` | `#125a5e` | Secondary fills, active tab pill | Owner. White text on it 7.9:1. |
| `--color-scrim` | `#0310119e` | Glass panels and overlays | Owner. |
| `--color-on-accent` | `#031011` | Text on accent fills | Owner. On accent 8.5:1. |
| `--color-danger` | `#ff8a8a` | Form errors only | The owner family has no error color. One semantic red, used only next to an error message. On bg 8.1:1. |
| `--color-ring` | `#22bec6` | Focus ring, 3px with 2px offset | Owner accent. |

### Light theme

| Token | Hex | Role | Source and contrast |
|---|---|---|---|
| `--color-bg` | `#ffffff` | Page background | Owner. |
| `--color-bg-deep` | `#e6f2f3` | Footer, loader, transition color, alternate bands | Very light tint of `#22bec6`. |
| `--color-surface` | `#ffffff` | Cards and panels | Owner. |
| `--color-surface-2` | `#f2f8f9` | Raised rows, chips, hover | Tint. |
| `--color-border` | `#d3e4e6` | Hairlines on bg | Tint, non-text. |
| `--color-border-2` | `#b9d3d6` | Hairlines on surfaces, inputs | Tint, non-text. |
| `--color-ink` | `#031011` | Headings and body text | Owner. On white 19.3:1, on bg-deep 16.1:1. |
| `--color-ink-2` | `#3b5a5e` | Secondary text | Shade of `#125a5e`. On white 7.5:1, on bg-deep 6.5:1. |
| `--color-ink-3` | `#4f6d71` | Small labels | Shade. On white 5.6:1, on bg-deep 4.9:1. |
| `--color-accent` | `#22bec6` | Fills, glow, scene lines, large display text only | Owner. On white 2.3:1, so never body text. |
| `--color-accent-text` | `#125a5e` | Accent used as text on light | Owner. On white 7.9:1, on bg-deep 6.9:1. |
| `--color-accent-2` | `#0d4346` | Deep highlight inside scenes | Shade. On white 11.0:1. |
| `--color-teal-deep` | `#125a5e` | Secondary fills | Owner. White text on it 7.9:1. |
| `--color-scrim` | `#ffffffb8` | Glass panels and overlays | White at 72%. |
| `--color-on-accent` | `#031011` | Text on accent fills | Owner. On accent 8.5:1. |
| `--color-danger` | `#b42318` | Form errors only | Semantic red. On white 6.6:1. |
| `--color-ring` | `#125a5e` | Focus ring | Owner. |

### Theme switch

- The theme is stored in `localStorage("theme")` as `dark` or `light`. Without a stored value the
  system preference wins.
- A tiny inline script in `<head>` sets `data-theme` on `<html>` before the first paint, so there
  is no flash. `color-scheme` follows the theme.
- The toggle is a 44px button in the header on every page (icon plus visible state, `aria-pressed`).

## Typography

| Use | Font | Source |
|---|---|---|
| Persian (default locale) | Vazirmatn, variable 100-900, `fonts/Vazirmatn-Variable.woff2` | SPEC section 3. |
| English | Plus Jakarta Sans, variable 200-800, latin subset, self-hosted | Kept from turn 1. |

No monospace anywhere (owner decision 3). The former mono label layer is now `.label`: the sans
font, 12 to 13px, weight 600, tabular numbers, and uppercase tracking in English only.
Display headings use `clamp()` from 40px to 84px. Body 16 to 18px, line-height 1.6 English and
1.8 Persian. Persian prose uses Persian digits. Latin names inside Persian prose are wrapped by
`lib/bidi.tsx`.

## Scene system

One `<canvas>` (`components/scene/Stage.tsx`), fixed behind the page, drawn with three.js
(imperative, no react-three-fiber: the scene is driven by scroll, not by React state, and the
dependency list stays short). The engine (`components/scene/engine.ts`) loads after first paint,
on the first scroll, pointer move or key press, or 1.5 s after `load`, whichever comes first.
Sections declare what the canvas shows with `data-scene="tunnel|warp|brain|field"`. The engine
creates one ScrollTrigger per section and hands its progress to that scene. The scene under the
viewport center is drawn; neighbors cross-fade.

| Scene | What you see | Pointer | Phone (under 768px) |
|---|---|---|---|
| `tunnel` | Wireframe grid corridor, accent lines, fog to the page color, a sound wave of points in the middle. Progress moves the camera forward. | Camera yaw and pitch follow the pointer, the wave swells near it. | Half the lines, DPR 1. |
| `warp` | The same corridor speeds up, rings tighten, a bright square grows ahead, then the screen flashes to the theme color. | Same as tunnel. | Same, lighter. |
| `brain` | A neural network (points and links in a brain-shaped cloud) with a pulsing core. The camera flies inside. Node clusters light up on the side of each conversation line. The owner video plays at low opacity as the inside-the-brain layer. | The network tilts toward the pointer. | A lighter network (520 points) plus the 720 video at higher opacity. |
| `field` | Sparse floor grid and slow particles, very low cost, for content pages. | Parallax. | Fewer particles. |

Rules:

- The canvas pauses when the tab is hidden and when no scene section is in view.
- Pixel ratio at most 1.5 on desktop and 1 on phones.
- Reduced motion: the engine never loads. Each scene section shows its poster picture instead
  (`SceneBackdrop`), nothing is pinned, nothing is hidden, and the brain conversation is a plain
  list with the report panel in place.
- Without WebGL the same posters are shown.
- Without JavaScript all text is visible (pre-hidden states exist only under `html.motion`).

## Scenes per page, in scroll order

Home:

1. `tunnel`, pinned 260vh: the h1 is visible at once, then the description, the facts and the
   buttons appear one by one as the camera flies forward.
2. `warp`, 160vh: one transitional sentence, the flash to the theme color, and we are inside.
3. `brain`, pinned 520vh: five conversation lines alternate sides (customer and agent, RTL
   correct), the core pulses and links to each line, then the "گزارش تماس" panel grows toward the
   camera and fades. Sample data, labeled as a demo.
4. Horizontal journey, pinned 300vh (vertical stack on phones): four panels, one per agent, travel
   follows the reading direction.
5. `field`: product surfaces (desktop window and phone), deployment spaces, trust list.
6. Command palette call to action, then the footer on `bg-deep`.

Product: `tunnel` hero (short, 140vh, not pinned) > live demo window with the sticky section
tabs > capability sections with depth (each panel comes toward the camera as it enters, CSS
perspective plus GSAP z) > `field` > CTA.

How it works: `tunnel` hero > pipeline timeline where each step is a gate the camera passes
(`tunnel` with progress tied to the rail) > state glossary > policy block > CTA.

Solutions: `tunnel` hero > role switcher over `field` > CTA. Role pages: `tunnel` hero > role
surface with depth > checklist > CTA.

Security: `warp` hero (you go into the vault, the page rests on `bg-deep`) > settings index >
policy cards with depth > live masking demo > CTA.

Deployment: `tunnel` hero > three environment spaces with a `warp` between each pair (SaaS,
self-hosted, hybrid) > comparison table > runtime list > CTA.

About: `tunnel` hero > principles over `field` > working steps rail > status note.
Contact: `tunnel` hero > form panel with the "what happens next" rail. Pricing, legal,
transparency: `tunnel` hero > status placeholder. 404: `warp` hero.

## Page transitions and loader

- `components/site/TransitionLayer.tsx` (client, in the layout) listens for clicks on internal
  links. It covers the page with the theme base color (`--color-bg-deep`) in 240 ms, shows the
  loader, and calls `router.push` inside `startTransition`.
- The loader shows the Homayar mark as five voice bars that move like a waveform, a progress bar
  and a percentage. Progress is real, in three steps: 12% when the request starts, up to 72% while
  the route is pending (an eased ramp), 100% when the new route has committed and painted.
- Then the cover fades out in 420 ms and the new page plays its enter animation.
- Browser back and forward are not intercepted, so history works as usual.
- Reduced motion: no interception, no loader, instant swap.
- `aria-busy` on the loader, `role="status"` with the localized "loading" text.

## Media plan (owner assets, Addendum 5)

All files live in `public/media/`. Widths are pixels, sizes are bytes on disk.

| File | Size | Use |
|---|---|---|
| `tunnel-wave-1920.avif` / `.webp` | 38,199 / 51,614 | Poster of the `tunnel` scene (reduced motion, no WebGL, no JS). |
| `tunnel-wave-960.avif` / `.webp` | 6,051 / 5,862 | Same, phones. |
| `tunnel-warp-1920.avif` / `.webp` | 37,813 / 51,262 | Poster of the `warp` scene and the loader backdrop. |
| `tunnel-warp-960.avif` / `.webp` | 8,452 / 8,016 | Same, phones. |
| `brain-1920.avif` / `.webp` | 86,404 / 98,724 | Poster of the `brain` scene, and the look reference for the 3D network. |
| `brain-960.avif` / `.webp` | 14,380 / 13,216 | Same, phones. |
| `og.jpg` (1200x630) | 93,365 | Open Graph image, cropped from the tunnel-wave source. |
| `neurons-1280.mp4` / `.webm` | 1,850,077 / 1,848,979 | Audio stripped. Inside-the-brain layer behind the conversation on desktop (30% opacity in dark, 10% in light, under the WebGL network). `preload="none"`, `muted playsinline`, loads when the brain section nears the viewport. |
| `neurons-720.mp4` / `.webm` | 1,059,932 / 1,503,127 | Same, under 768px, behind the lighter WebGL network (55% opacity in dark, 12% in light). |
| `neurons-poster.avif` / `.webp` | 45,744 / 54,458 | Poster frame of the video. Under reduced motion only the poster shows and the video never plays. |

Posters are `<picture>` elements with `loading="lazy"`, `decoding="async"` and a fixed aspect
box, so nothing shifts. They are `display: none` under `html.motion`, so a browser with the
WebGL engine never downloads them. First-load transfer of the home page stays under 1.5 MB:
the video is not requested before scroll.

## Motion system (GSAP 3.15, ScrollTrigger, SplitText)

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 160ms | Hover and focus color changes |
| `--dur-base` | 260ms | Chips, sheet, tab pill, theme switch |
| `--dur-cover` | 240ms | Page transition cover in |
| `--dur-reveal` | 420ms | Page transition cover out |
| Ease in | `expo.out` | Arrivals |
| Ease scrub | `none` | Everything tied to scroll |

Rules: motion means something (flight is progress, the core reads the line, the report comes to
you). Declarative reveals stay (`data-reveal`, `data-reveal-group`, `data-split`, `data-parallax`,
`data-depth`). Every main page has at least three scroll-driven changes of transform or opacity.
Reduced motion: `gsap.matchMedia` wraps every tween, the `motion` class is set only when the user
has no reduced-motion preference, so nothing is ever hidden for them.

## Navigation

Desktop: sticky glass header (scrim token), text links, theme toggle, language switcher, "Log in"
ghost and "Start" accent button, 2px accent progress line. Mobile: compact header with theme
toggle, language switcher and a menu sheet (native `<dialog>`), plus a fixed bottom tab bar with
five destinations, 44px targets, `aria-current="page"` and a teal-deep pill.

## Icons

`lucide-react`, stroke 1.75. Decorative icons carry `aria-hidden="true"`, icon-only controls carry
`aria-label`. Same family as the real console.
