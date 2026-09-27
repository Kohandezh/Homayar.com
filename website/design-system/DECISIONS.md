# Design decisions for the Homayar website (turn 2, dark redesign)

Short record of what the site uses and where each choice came from.
The skill output lives in `design-system/homayar/MASTER.md` and `design-system/homayar/pages/*.md`
(re-generated on 2026-09-28 with `--variance 8 --motion 9`). The first, light design (turn 1) was
rejected by the owner. Its skill runs and palette are in git history (commit `69da721`).
The CSS tokens in `app/globals.css` must match the hex values listed here.

## Concept: voice becomes insight

The site is the inside of the product, not a brochure about it. One visual idea runs through every
page: sound turns into understanding. On the home page a WebGL field of amber particles moves like a
live waveform. As you scroll, the field settles into ordered rows that look like transcript lines, and
the color shifts from amber (voice) to teal (evidence). Below it a real call report builds itself
from sample data: waveform, transcript, then the four agents writing their part. Every other page
reuses the same language: dark stage, warm light, live status chips, app surfaces.

Named inspirations (2025-2026 product sites, inspiration only, nothing copied):

- ElevenLabs: the audio waveform as the brand device.
- Linear: dark restraint, the product surface as the hero, no decoration for its own sake.
- Resend: warm light on a near-black stage, a live demo instead of a screenshot.
- Raycast: the command palette as a marketing surface.
- Vercel: monospace labels, hairline grids, status dots.
- Lusion and Active Theory: WebGL scenes that react to pointer and scroll.

## Skill runs used (ui-ux-pro-max)

| Run | Command (short form) | What we took from it |
|---|---|---|
| K | `"dark immersive AI voice analytics SaaS call center enterprise warm amber trust" --design-system --variance 8 --motion 9 --density 5 --persist --force -p Homayar` | Pattern "Product Demo + Features": demo only when it explains value, captions and transcript, visible play and pause, no autoplay under reduced motion, pause when off screen, keep the final state as static content. Dials: bold and asymmetric layout, complex motion. Motion preset "Page Transition (Complex)". Its palette is red and blue and its style is Brutalism, so the colors and the style were not used (see "Overrides"). |
| L | `--page home`, `--page product`, `--page security` (same query, persisted) | Page pattern reminders. Same palette, not used. |
| M | `"scroll pinned storytelling scrub timeline" --domain gsap` | Pinned scrub timeline preset (`pin: true, scrub: 1, end: '+=150%'`), pin at most 1 or 2 sections per page, `ScrollTrigger.refresh()` after fonts, parallax only on decorative layers with a small delta. |
| N | `"split text heading reveal kinetic typography" --domain gsap` | SplitText preset: `expo.out`, stagger 0.015, revert on cleanup, headline-length text only, `matchMedia` for reduced motion. |
| O | `"page transition magnetic button cursor" --domain gsap` | Magnetic hover with `quickTo` and a clamped pull (`* 0.3`), at most 1 or 2 focal elements per screen. Route transition: exit faster than enter, never block navigation. |
| P | `"hover micro-interaction count up number scroll progress" --domain gsap` | Standard hover lift (`y: -4`, 250ms, matching leave tween), loader pause on `visibilitychange`. |
| Q | `"dark mode immersive AI native cinematic" --domain style` | "Dark Mode (OLED)": minimal glow, `color-scheme: dark`, text contrast 7:1 where possible, no white surfaces. We use a warm near-black instead of pure black. |
| R | `"dark warm amber copper ember charcoal trust enterprise" --domain color` and `"audio podcast voice dark warm" --domain color` | "Podcast Platform: dark audio + warm accent" confirms a dark base with an orange or amber accent for audio products. Its indigo and blue-black base was replaced by a warm base (owner: no blue-black, no purple). Amber `#F59E0B` and `#D97706` come from the "Alarm & World Clock" and "Bookmark" rows. |
| S | `"modern technical AI product sans serif dark" --domain typography` | "Modern Dark Cinema (Inter System)": one precise family with tight tracking for display sizes and a monospace-style label layer. We keep Plus Jakarta Sans (already self-hosted, same geometric class) and add JetBrains Mono for the label layer. |
| T | `"dark mode contrast reduced motion" --domain ux`, `"motion meaning parallax excessive motion" --domain ux` | 4.5:1 on dark surfaces, reduced motion honored, no scroll-jacking under reduced motion, motion must show cause and effect, animate few elements per view. |
| U | `"dynamic import client component lazy canvas performance" --stack nextjs` | `next/dynamic` for heavy client components, client components as leaves, reserve space for async content. |
| V | `"immersive product demo hero storytelling" --domain landing` | "Scroll-Triggered Storytelling" and "Immersive/Interactive Experience": keep the DOM reading order complete, provide skip, keyboard and reduced-motion paths, pause when off screen. |
| W | `"waveform audio microphone insight" --domain icons` | Decorative icons `aria-hidden`; controls get an accessible name and state (`aria-pressed`). |

## Overrides of the skill output

- Run K returned a red primary, a blue accent and the Brutalism style. The owner asked for a dark,
  warm, AI-native look with no blue-neon or purple gradients. The palette below is therefore ours,
  built from the amber rows of run R and the dark-audio pattern of run R, on a warm near-black base.
- Run K and S list Google Fonts CSS imports. The site makes no third-party request, so fonts are
  self-hosted files under `fonts/` with their OFL license text.

## Palette (final tokens)

Every text pair below was measured with the WCAG formula (`scratchpad/contrast2.mjs`). Body text
is at least 4.5:1 on every surface it appears on.

| Token | Hex | Role | Source and contrast |
|---|---|---|---|
| `--color-bg` | `#130F0C` | Page background, espresso near-black | Override of run Q `#121212` (neutral) and run R `#0F0F23` (blue-black): warmed toward brown. |
| `--color-bg-2` | `#1A1411` | Alternating bands | Override, one step lighter than `--color-bg`. |
| `--color-surface` | `#211A16` | Cards, panels, app surfaces | Override, warm version of run R card `#1B1B30`. |
| `--color-surface-2` | `#2A211C` | Raised rows, chips, hover | Override, one step lighter. |
| `--color-border` | `#362B25` | Hairlines on `--color-bg` | Override. Non-text, kept subtle (1.4:1). |
| `--color-border-2` | `#4A3C33` | Hairlines on surfaces, input borders | Override. Non-text. |
| `--color-ink` | `#F4ECE1` | Headings and body text | Override of run R foreground `#F8FAFC` (cool): warm cream. On bg 16.3:1, on surface 14.7:1, on surface-2 13.5:1. |
| `--color-ink-2` | `#B9AB9B` | Secondary text | Override of run R muted foreground `#94A3B8` (cool). On bg 8.5:1, on surface 7.7:1, on surface-2 7.0:1. |
| `--color-ink-3` | `#9C8E80` | Small labels, footer text | Override. On bg 6.0:1, on surface 5.4:1, on surface-2 5.0:1. |
| `--color-amber` | `#F59E0B` | Primary button fill, glow, waveform particles | Run R (Alarm, Bookmark rows). As text on bg 8.9:1, on surface 8.0:1. |
| `--color-amber-text` | `#FBBF24` | Eyebrows, labels, links on dark | Override, lighter amber for small text. On bg 11.4:1, on surface 10.3:1, on surface-2 9.4:1. |
| `--color-copper` | `#D97706` | Deep glow gradients, bars | Run R accent. Decorative and large text only (on surface-2 4.95:1). |
| `--color-ember` | `#F97316` | High-severity marks | Override, from run R "Podcast Platform" accent. On bg 6.8:1, on surface 6.1:1. |
| `--color-teal` | `#2DD4BF` | Trust accent: done states, evidence, resolved particles | Override. Teal is the trust color the real console uses (`#147d6f`), lightened for dark surfaces. On bg 10.2:1, on surface 9.2:1. |
| `--color-teal-deep` | `#0F766E` | Teal fills behind cream text | Kept from turn 1. Cream text on it 4.7:1. |
| `--color-danger` | `#F87171` | Form errors | Override of run K destructive `#DC2626`, lightened for dark. On bg 6.9:1, on surface 6.2:1. |
| `--color-on-amber` | `#1A120C` | Text on amber buttons and chips | Override. On amber 8.6:1, on amber-text 11.1:1. |
| `--color-ring` | `#FBBF24` | Focus ring, 3px with a 2px offset | Same as `--color-amber-text`. |

Colors we do not use: purple, violet, indigo, blue-neon, pure black, pure white surfaces.

## Typography

| Use | Font | Source |
|---|---|---|
| Persian (default locale) | Vazirmatn, variable 100-900, `fonts/Vazirmatn-Variable.woff2` | SPEC section 3. OFL license file next to it. |
| English | Plus Jakarta Sans, variable 200-800, latin subset, self-hosted | Kept from turn 1 (run F). Run S suggests Inter; Plus Jakarta Sans is the same geometric class and is already self-hosted. |
| Labels, status chips, timestamps, code | JetBrains Mono, variable 400-700, latin subset, `fonts/JetBrainsMono-Variable-latin.woff2` | Run S: a monospace label layer gives the "inside the product" feel. Persian labels use Vazirmatn (a monospace has no Persian glyphs). OFL license file next to it. |

Rules: base 16px. Display headings use `clamp()` from 40px to 84px, weight 700 and 800, tracking
-0.03em in English and 0 in Persian. Body 16 to 18px, line-height 1.6 English and 1.8 Persian.
Numbers in Persian prose use Persian digits. Latin technical names inside Persian prose are wrapped
in `<bdi dir="ltr" lang="en">` by `lib/bidi.tsx` so they never reorder.

## Style

Dark stage, warm light. Surfaces are flat panels with 1px hairlines and 16px radius. Light comes
from a few radial amber glows and from the WebGL field, never from blurred blobs. App details
everywhere: monospace status chips with a live dot, segmented controls, panel headers with window
dots, kbd hints in the command-palette CTA. No icon-in-a-colored-circle cards. No banner CTA. Each
page has its own composition (see "Page compositions").

## Motion system (GSAP 3.15, ScrollTrigger, SplitText)

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 160ms | Hover and focus color changes |
| `--dur-base` | 260ms | Chips, sheet, tab pill |
| `--dur-page` | 520ms | Route enter (fade, 16px rise, blur 6px to 0) |
| Ease in | `expo.out` | Arrivals (run N) |
| Ease scrub | `none` | Everything tied to scroll (run M) |
| Stagger | 0.06s children, 0.04s words | Reveals and SplitText |

Rules:

- Motion means something: the field resolves into text (voice to transcript), scores count up
  (analysis), the pipeline rail fills (progress), the mask demo replaces a phone number (redaction).
- Declarative scroll motion: server components mark elements with `data-reveal`, `data-reveal-group`,
  `data-split` and `data-parallax`. One client module (`components/motion/runtime.ts`) creates the
  ScrollTriggers. It is imported dynamically after first paint, so animation code never blocks
  rendering.
- Every page has at least three scroll-driven changes of transform or opacity (reveals, parallax,
  the header progress line).
- Pinned scrub sections: at most one per page (run M): home agents story, product demo stage.
- Reduced motion: `gsap.matchMedia("(prefers-reduced-motion: no-preference)")` wraps every tween.
  The `<html class="motion">` flag is set by a tiny inline script only when JavaScript runs and the
  user has no reduced-motion preference. Pre-hidden states exist only under that class, so with
  JavaScript off or reduced motion on, everything is visible at load. The WebGL field draws one
  static frame. The live demo shows its final state and does not autoplay.
- Nothing animates width, height or layout. No layout shift from motion.

## WebGL scene

`components/scene/VoiceField.tsx`, raw WebGL (WebGL2 with a WebGL1 fallback), no library. About
6 KB. A grid of particles drawn as points with additive blending. The vertex shader adds a moving
waveform, a pointer ripple and a `uResolve` value that scroll drives from 0 (chaotic voice) to 1
(ordered rows). Desktop: 180 by 44 points at device pixel ratio up to 1.5. Under 768px: 84 by 22
points at ratio 1. Pauses when off screen (IntersectionObserver) or when the tab is hidden. Loaded
with `next/dynamic` after the first paint. Without WebGL a CSS aurora gradient is shown.

## Navigation

Desktop (min-width 64rem): sticky dark glass header, text links, language switcher, "Log in" ghost
and "Start" amber button. A 2px amber progress line at the top of the header scales with scroll.
Mobile: compact header with a menu button that opens a native `<dialog>` sheet (focus trap and top
layer for free), plus a fixed bottom tab bar with 5 destinations. Tabs are at least 44 by 44 CSS px,
the current page carries `aria-current="page"`, the active tab has an amber pill.

## Page compositions

- Home: full-height stage with the WebGL field and a kinetic headline; live call report demo;
  pinned "four agents" story; product surfaces grid with parallax; deployment flow; trust list;
  command-palette CTA.
- Product: console window with the live demo; a sticky segmented control that follows the section
  in view; capability sections with app surfaces alternating sides.
- How it works: job timeline with a rail that fills on scroll and state chips that flip; the state
  glossary as a status grid; policy block.
- Solutions: role switcher cards with mini surfaces; role pages with a role-specific surface.
- Security: settings-panel index, policy cards, live masking demo.
- Deployment: three environment panels with animated flow lines, comparison table, runtime list.
- About: editorial hero, numbered principles, working steps timeline, status note.
- Contact: form panel with the "what happens next" rail. Pricing, legal, transparency: status
  placeholder layout. 404: stage with the field.

## Icons

`lucide-react`, stroke 1.75. Decorative icons carry `aria-hidden="true"`, icon-only controls carry
`aria-label` (run W). Same icon family as the real console.
