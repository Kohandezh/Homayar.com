# Design decisions for the Homayar website

Short record of what the site uses and where each choice came from.
The skill output lives in `design-system/homayar/MASTER.md` and `design-system/homayar/pages/*.md`.
The CSS tokens in `app/globals.css` must match the hex values listed here.

## Skill runs used

| Run | Command (short form) | What we took from it |
|---|---|---|
| A | `"B2B SaaS AI voice analytics call center enterprise trust warm" --design-system --persist -p Homayar` | Pattern (hero, value, features, CTA), checklist, anti-patterns, font family (Plus Jakarta Sans), card and muted structure. Its palette is blue, so we did not use its colors (see Addendum 2 of the SPEC). |
| B | `--page home`, `--page product`, `--page security` (same query, persisted) | Page pattern reminders. Same blue palette, not used. |
| C | `"warm trustworthy enterprise saas amber teal" --domain color` | Result 3 "Warm ink + amber accent on cream": background `#FFFBEB`, accent `#D97706`, card `#FFFFFF`. Result 5 "Caring teal + warm orange": teal + orange pairing idea. |
| D | `"call center analytics trust" --domain color` | Result 5 "Trust teal + professional blue": primary `#0F766E`, foreground `#134E4A`. Result 3 "Authority navy + trust gold": accent text `#B45309`. |
| E | `"warm cream ink neutral" --domain color` | Confirms cream `#FFFBEB` and amber `#D97706` for warm products. |
| F | `"professional enterprise clean modern" --domain typography` | Result 4 "Enterprise SaaS Mobile (Plus Jakarta Sans)": one Latin family, weights 800/700/600/400. |
| G | `"enterprise saas warm minimal trust" --domain style` | "Minimalism & Swiss Style": grid, white space, clear hierarchy, subtle 200-250ms hovers, few shadows. |
| H | `"app router fonts images performance" --stack nextjs` | Use App Router, `next/font`, variable fonts, OG metadata. |
| I | `"bottom navigation tab bar mobile"`, `"error summary validation"`, `"reduced motion page transition"` `--domain ux` | Fixed nav must not cover content (padding), error summary with focus and links, inline errors with `aria-describedby`, `prefers-reduced-motion`, at most 1-2 animated elements per view. |
| J | `"decorative icon aria hidden" --domain icons` | Decorative icons next to text get `aria-hidden`; icon-only controls get an accessible name. |

## Palette (final tokens)

All body text pairs below were checked for at least 4.5:1 contrast.

| Token | Hex | Role | Source |
|---|---|---|---|
| `--color-bg` | `#FBF7EF` | Page background, warm white | Override of run C/E cream `#FFFBEB`. The skill cream is too yellow on large areas. Softened toward sand, still warm. |
| `--color-surface` | `#FFFFFF` | Cards, sheets, inputs | Run C card `#FFFFFF`. |
| `--color-sand` | `#F3EBDC` | Second surface, muted bands, chips | Override. Warm version of the skill "Muted" role (`#FCF6F0` in run C) darkened so it reads on the warm white. |
| `--color-border` | `#E5DAC6` | Card borders, dividers | Override. Warm version of run C border `#FAEEE1`, darkened so borders stay visible. |
| `--color-ink` | `#1C1917` | Body text and headings | Override of skill foreground `#0F172A` (cool slate). Warm near-black. Contrast on `--color-bg` 16.4:1. |
| `--color-ink-2` | `#57534E` | Secondary text | Override of skill muted foreground `#475569` (cool). Warm gray. Contrast on `--color-bg` 7.1:1, on white 7.6:1. |
| `--color-primary` | `#0F766E` | Deep teal anchor: primary buttons, links, active nav | Run D result 5 primary. Contrast of white text on it 5.5:1. |
| `--color-primary-strong` | `#115E59` | Hover and pressed state of primary | Override: darker step of `--color-primary`. |
| `--color-primary-soft` | `#DDF0EC` | Tinted backgrounds behind teal icons and chips | Override: light tint of `--color-primary`. Decorative only. |
| `--color-ink-teal` | `#123A36` | Dark sections (CTA band, footer, app window chrome) | Override. Taken from the real product's dark panel colors (`#12342f`, `#17332f` in `Homayar/frontend/src/index.css`) so the site and the app share one brand anchor. Contrast of `#F6F1E7` text on it 11.1:1. |
| `--color-accent` | `#D97706` | Amber highlight: badges, marks, decorative bars | Run C/E accent. Used with ink text on top (`#1C1917` on amber 5.5:1). Not used as text on light backgrounds. |
| `--color-accent-text` | `#B45309` | Amber as text (eyebrows, labels) | Run D result 3 accent `#B45309`. Contrast on `--color-bg` 4.7:1. |
| `--color-terracotta` | `#C2410C` | Second warm accent: severity marks, chart bars | Override. Run C result 5 orange `#EA580C` darkened to reach 4.5:1 on cream (4.9:1). |
| `--color-success` | `#15803D` | Positive status text and marks | Override. Skill green `#16A34A` darkened for 4.5:1 text contrast (4.7:1 on cream). |
| `--color-danger` | `#B91C1C` | Form errors, error status | Override. Skill destructive `#DC2626` darkened for 4.5:1 on cream (6.1:1). |
| `--color-ring` | `#0F766E` | Focus ring | Same as primary. Ring is 3px with a 2px offset so it also works on dark sections. |

Colors we deliberately do not use: purple or violet gradients, neon glows, pure black, cool slate grays.

## Typography

| Use | Font | Source |
|---|---|---|
| Persian (default locale) | Vazirmatn, variable weight 100-900, self-hosted from `fonts/Vazirmatn-Variable.woff2` | SPEC section 3 names Vazirmatn. OFL license file next to it. |
| English | Plus Jakarta Sans, variable weight 200-800, latin subset, self-hosted | Run A and run F result 4. OFL license file next to it. |

Rules: base 16px, body line-height 1.7 for Persian and 1.6 for English. Scale 14, 16, 18, 20, 24, 30, 38, 48, 60 px with `clamp()` on the two largest steps. Headings 700, hero 800, labels 600, body 400. No letter-spacing changes on Persian text. Numbers inside Persian prose are written with Persian digits.

## Style

Minimal and Swiss (run G) with app-like surfaces: 12px radius on cards, 16px on sheets and mockup frames, 999px on chips and tab pills. Shadows only on floating elements (sticky header, bottom tab bar, sheet). The sticky header uses a light backdrop blur, the one idea we kept from the glassmorphism suggestion in run A, because it keeps the header readable over any section while feeling like a native app bar.

## Motion

| Token | Value | Use |
|---|---|---|
| `--dur-fast` | 150ms | Hover and focus color changes |
| `--dur-base` | 220ms | Sheet open, tab underline, button press |
| `--dur-page` | 320ms | Page enter (fade plus 8px rise, transform and opacity only) |
| Easing | `cubic-bezier(0.2, 0, 0, 1)` entering, `ease-in` leaving | Run I: decelerate on arrival |

Under `prefers-reduced-motion: reduce` every transition and animation is set to near zero and the page enter animation is removed. Nothing animates width, height or layout properties, so there is no layout shift.

## Navigation

Desktop (min-width 64rem): sticky header with text links and the language switcher. Mobile: compact header with menu sheet, plus a fixed bottom tab bar with 5 destinations (Home, Product, Solutions, Security, Contact). Each tab is at least 44 by 44 CSS px, the current page carries `aria-current="page"`, and the page reserves bottom padding so content is never hidden under the bar (run I).

## Icons

`lucide-react`, stroke 1.75, one style everywhere. Decorative icons next to text carry `aria-hidden="true"`; icon-only buttons carry `aria-label` (run J). This is the same icon family the real product console uses.
