# Design decisions for the Homayar website (turn 6, particle hero, floor grid and headline sequence)

Short record of what the site uses and where each choice came from. Skill output lives in
`design-system/homayar/MASTER.md` and `design-system/homayar/pages/*.md` (re-generated on
2026-09-30 with `--variance 8 --motion 10 --density 5`). The first two designs (light warm, then
dark warm) were rejected by the owner. Their notes are in git history (`69da721`, `cc9c8c0`).
The CSS tokens in `app/globals.css` must match the hex values listed here.

## Concept: walk into the machine

The site is a flight through the product. Every picture on the site is drawn by code: WebGL for
the home scenes, SVG and CSS for the inner pages, `ImageResponse` for the Open Graph image. There
are no photos, no owner images and no video (Addendum 6, owner decision of 2026-10-01). One
persistent WebGL canvas sits behind the home page. Scrolling moves the camera forward through
three kinds of space:

0. **Particles.** Several thousand glowing points form a slowly turning sphere. Scrolling sends
   them to positions that spell the brand and the slogan (Addendum 7).
1. **Floor grid.** An infinite wireframe floor receding to a dark horizon, with a glowing sound
   wave floating above it (the call). Walls and ceiling were removed on the owner's request
   (Addendum 7b).
2. **Warp.** The tunnel narrows to a bright square, the screen flashes to the theme color, and you
   come out in a different space (from the call into the analysis).
3. **Brain.** A neural network. Inside it, a customer and an agent talk. A glowing core reads each
   line, and the call report grows toward you and fades away.

Every inner page has its own scenario (see "Scenario per page"). The only shared parts are the
header, the footer, the page transition and the theme. Text is always server-rendered and readable
without JavaScript. Motion is a layer on top.

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
| L | `"svg path draw scroll scrub stroke" --domain gsap` (2026-10-01) | Scrub-driven reveal preset: `scrub: 0.5 to 1.5`, never instant jumps, deterministic section height, `ScrollTrigger.refresh()` after fonts. Applied to the snake path, the constellation links and the table build. |
| M | `"3d rotate carousel scroll pin" --domain gsap` and `"3d carousel rotating cards accessibility focus" --domain ux` | Rotating content needs user control and must stop under reduced motion. The solutions ring is driven by scroll only (no timer), every seat is a real link, and reduced motion shows the four seats in a grid. |
| N | `"horizontal timeline scroll reduced motion keyboard" --domain ux` | Horizontal scroll is only allowed inside a pinned section driven by vertical scroll; content never widens the page; reduced motion shows the final readable state. Applied to the day timeline. |
| O | `"stepper progress pinned counter" --domain gsap` | Keep a pinned stepper short (the about page pins 220% only) and pause work when off screen. |

## Overrides of the skill output

- Run A returned a red primary, a blue accent and the Brutalism style. The owner gave six exact
  colors, so the palette below is the owner's. The style is "Parallax Storytelling" from run J.
- Runs A and K list Google Fonts imports. The site makes no third-party request, so fonts are
  self-hosted files under `fonts/`.
- Run D says pin at most one or two sections. The owner asked for a pinned particle hero, a pinned
  grid flight, a pinned brain scene and a pinned horizontal section on the home page. Home has four
  pins on desktop. On phones the horizontal section is a plain vertical stack, so home has three.

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
viewport center is drawn; neighbors cross-fade. Only the home page and the 404 page declare
scene sections. Inner pages draw their scenario with SVG and CSS, so the canvas sleeps there.

| Scene | What you see | Pointer | Phone (under 768px) |
|---|---|---|---|
| `particles` | 6000 points (2600 on phones) on a Fibonacci sphere, one draw for the core and one soft glow draw of the same geometry. The vertex shader mixes each point from its sphere position to a text target along a small outward arc, with a per-point delay so the flight looks organic. Targets are sampled from the two lines drawn on an offscreen 2D canvas with the site font (Vazirmatn shapes Persian correctly, `direction: rtl`), after `document.fonts.ready`. Progress is the pinned section's scroll progress, so the morph is scrubbed and reversible. Colors: `#22bec6` points shaded toward `#125a5e` by depth, `#7fdde2` in the word; light theme `#125a5e` on white. | The sphere turns and tilts toward the pointer; the word barely breathes. | 2600 points, DPR 1. |
| `tunnel` | A procedural wireframe floor: longitudinal lines every 1 unit (2 on phones) and a cross line every 3 units, a brighter double line every 24 units, depth fog to the page color, nothing above the floor. Scroll is distance: the camera position along the corridor is `progress * 90` units, nothing else moves it, so scrolling back reverses the travel exactly. Fast scrolling adds speed streaks (short line segments near the walls whose length and opacity follow the scroll velocity). The voice wave in the center is a live line of points that keeps moving in time. Lines are `#22bec6` on the dark base and `#125a5e` on white. | Camera yaw and pitch follow the pointer, the wave swells near it. | Half the lines, DPR 1. |
| `warp` | The same corridor accelerates (distance grows with the square of progress), rings tighten, a bright square grows ahead, then the screen flashes to the theme color. | Same as tunnel. | Same, lighter. |
| `brain` | A neural network (points and links in a brain-shaped cloud) with a pulsing core and a soft inner glow. The camera flies inside. Node clusters light up on the side of each conversation line. No video. | The network tilts toward the pointer. | A lighter network (520 points). |
| `field` | Sparse floor grid and slow particles, very low cost, for content pages. | Parallax. | Fewer particles. |

Rules:

- The canvas pauses when the tab is hidden and when no scene section is in view.
- Pixel ratio at most 1.5 on desktop and 1 on phones.
- Reduced motion: the engine never loads. Each scene section shows a static frame drawn as
  inline SVG (`SceneBackdrop`, four `<symbol>` definitions from `PosterDefs` reused with
  `<use>`), nothing is pinned, nothing is hidden, and the brain conversation is a plain list with
  the report panel in place. No raster image is requested anywhere on the site.
- Without WebGL the same SVG frames are shown.
- Without JavaScript all text is visible (pre-hidden states exist only under `html.motion`).

## Scenario per page (Addendum 6, approved 2026-10-01)

Each page has one signature scenario. The technique column says how it is built. Every scenario
has a static final state for reduced motion (the `motion` class is missing, so the CSS default is
the finished layout) and a lighter phone version (under 64rem nothing is pinned).

| Page | What the user sees while scrolling | Technique | Phone | Reduced motion |
|---|---|---|---|---|
| Home | Particle sphere that spells the brand and the slogan as you scroll (pinned 200vh) > floor grid flight where the owner's six headline lines take turns (each enters, holds and fades; the full title stays as a visually hidden `h1`), then the description, facts and buttons arrive (pinned 260vh) > warp into a new space (160vh) > brain scene with the conversation and the growing call report (pinned 520vh) > pinned horizontal journey through the 4 agents > product surfaces, deployment spaces and trust list on the calm field > command palette CTA. | WebGL scenes from the table above, GSAP ScrollTrigger timelines per section (`runtime.ts`). The particle words are also in the DOM as visually hidden text; the six lines are `aria-hidden` paragraphs next to the `h1`. | Lighter scenes, journey is a vertical stack, same headline sequence with smaller type. | SVG frames (a dotted disc for the particles), the words and the six lines as a static block under the visible `h1`. |
| How it works | **The snake path.** One curved line, stroke 10 CSS px, `#22bec6`, with a blurred glow copy and a bright head. It starts at the top of the page and is drawn as you scroll. It swings left and right and passes behind each of the 7 step cards (file check > Whisper > speakers > 4 agents > console > partial stages > policy) and comes out below. The card under the head lights up and plays its small demo (bars become text, speakers get colors, 4 scores fill, stage chips switch state, providers are allowed or refused). Persian starts from the right. | `SnakePath` (client) measures the card centers and writes a Catmull-Rom path into one absolute `<svg>` behind the cards (`z-index: 0`, cards `z-index: 1`). The runtime maps the viewport line at 60% height to a path length with a 400-point lookup table and sets `stroke-dashoffset`, the head position and the active card. Demos are CSS transitions keyed on `.is-active` and `.is-done`. | Amplitude of the swing drops to about 12% of the width, cards stack. | Full path, all cards in their finished state. |
| Product | **Assemble the console.** A large app window stays pinned. Each scroll step docks one panel into its slot (upload > transcript > 4 agents > dashboard > call report > copilot > integrations) until the console is complete. The side text explains the panel that just arrived. Below it the live demo plays a sample call. | Pinned timeline (`+=490%`), each panel tweens from small and low to its grid slot, slot outline glows on arrival, side texts cross-fade. Slots are CSS grid areas inside `ConsoleAssembly`. | Window shown complete, the 7 explanations as a numbered list. | Same as phone. |
| Solutions | **Four seats, one call.** One call report sits in the middle of a ring with 4 seats (QA manager, supervisor, operations, IT and security). Scrolling rotates the ring in 3D and brings each seat to the front; the report re-filters to what that role sees. Each seat is a link to its page. | CSS 3D: `perspective` stage, seats at `rotateY(i*90deg) translateZ(R)` with hidden backfaces, the ring's `rotationY` is scrubbed 0 to -270 (mirrored in RTL). The active seat gets `.is-front`; four report views cross-fade. | Seats become a swipe row (`scroll-snap-type: x mandatory`) under the report. | Four seats in a 2x2 grid around the report. |
| Solution pages (4) | **A day in the role.** A pinned horizontal timeline from morning to evening. Each hour shows the task of the role and the Homayar screen that helps. | Pinned section, the track's `x` follows the vertical scroll with `ease: "none"`, an hour axis fills as you travel. Direction follows the reading direction. Content of the five hours comes from the facts already on the page. | Vertical list of the five hours. | Same as phone. |
| Security | **Through the layers.** You pass through four translucent layers one after another: network wall, tenant isolation, personal-data masking, retention and deletion. Then the signature demo: a phone number in a transcript line dissolves into `[PHONE_1]` as you scroll. The detail sections and the "what we do not claim" list stay plain. | Pinned timeline: each glass layer scales from 0.4 to 1 (approach), then to 2.6 and fades (pass through). Mask demo: the digits are `<span>` characters that scatter, blur and fade while the placeholder chip scales in, all scrubbed over the block's travel from 70% to 30% of the viewport. | Layers stack as cards with depth; the mask demo stays scrubbed. | Stack of cards, masked line shown. |
| Deployment | **Three places to run.** One glowing server block travels between three zones: cloud (SaaS), your server (a wall closes behind it: no route), hybrid (it splits in two). A comparison table builds itself at the end. | Pinned timeline; the block's `x` is measured from the zone centers at refresh (RTL safe). The wall is a bar that slides in with the "No route" label. The split moves two halves apart with their labels. The table rows are revealed by a scrubbed stagger. | Three zone cards, each with its own static block state. | Same as phone. |
| About | **Principles as a constellation.** The 6 principles are stars; scrolling draws the links between them. Then the 4-step way of working (discovery > sample > pilot > rollout) as a short pinned stepper. | `Constellation` (client) measures the star of each principle card and writes the link path; `stroke-dashoffset` is scrubbed. Stepper: pinned `+=220%`, one step visible at a time, a 4-dot rail. | Links drawn on scroll, stepper is a vertical list. | Links fully drawn, vertical list. |
| Contact | Calm. The form next to the animated voice-bar mark, which reacts softly while the user types. No 3D. | `input` events on the form drive a short GSAP pulse on the bars; idle is a slow breath that pauses off screen. | Same. | Static mark. |
| Pricing, legal, transparency, 404 | Calm reading pages with light motion only. 404 keeps the warp frame. | Reveals only. | Same. | Static. |

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

## Media (Addendum 6: none)

`public/media/` is gone. The owner video and the three owner images are removed from scenes,
posters and Open Graph. What replaced them:

| Former use | Now |
|---|---|
| Scene posters (reduced motion, no WebGL) | Four inline SVG frames drawn by code (`components/scene/PosterDefs.tsx`), reused through `<use>`. |
| Inside-the-brain video layer | Nothing. The WebGL brain has a soft inner glow instead. |
| `og.jpg` | `app/[locale]/opengraph-image.tsx` renders the floor grid, the wave and the voice-bar mark with `ImageResponse` at build time (1200x630 PNG). The text in the image is the Latin brand name, because `ImageResponse` needs a TTF, OTF or WOFF font and the site only ships WOFF2 files. The Persian page title travels in `og:title`; the image alt is localized. |

## Motion system (GSAP 3.15, ScrollTrigger, SplitText)

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 160ms | Hover and focus color changes |
| `--dur-base` | 260ms | Chips, sheet, tab pill, theme switch |
| `--dur-cover` | 240ms | Page transition cover in |
| `--dur-reveal` | 420ms | Page transition cover out |
| Ease in | `expo.out` | Arrivals |
| Ease scrub | `none` | Everything tied to scroll |

Rules: motion means something (points become words, flight is progress, the core reads the
line, the report comes to you, the snake travels with you). Declarative reveals stay (`data-reveal`, `data-reveal-group`,
`data-split`, `data-parallax`, `data-depth`). Scenario hooks: `data-snake`, `data-console`,
`data-ring`, `data-day`, `data-layers`, `data-mask`, `data-zones`, `data-table-build`,
`data-constellation`, `data-stepper`, `data-voice-react`. Every main page has at least three
scroll-driven changes of transform or opacity. Pins per page: home 4 on desktop (owner request),
product 1, solutions 1, solution pages 1, security 1, deployment 1, about 1, others 0.
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
