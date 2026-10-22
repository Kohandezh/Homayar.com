# Scene rules and the home page flight

These rules come from the home hero that the owner designed. Every page and section follows them.

## Rules

1. **One world.** Every section happens in the same 3D space: the grid floor, the sky, the stars. No section brings its own background box. Nothing may hide the grid.
2. **One flight.** Scrolling a page is one flight: take-off, flying, landing. The visitor must feel it during the whole scroll.
3. **No frames.** No cards, panels, chips, bordered boxes or glass bubbles around content. Text sits directly on the scene. Thin lines (a hairline, a progress line) are fine.
4. **Data is made of the world's material.** Scores, lists and numbers are drawn with dots, lines and glow, not with UI cards.
5. **Scroll moves the camera.** A section is a place you fly to, not a block that slides up. Nothing pops in. Things come from far away and get closer.
6. **One idea per screen.** One centered headline, and at most one short line under it.
7. **Screens may hang in the world.** A real product screen (console, phone) is allowed as an object in the 3D world, hanging on thin cables, like a screen in a gallery. It is never a card on a page background.

References the owner liked: "The Shape of Intelligence" (kinetic headlines), "Virtually Ever After" (screens hanging in a point-cloud world), "Lusion" (one ribbon that ties connected sections together; for how-it-works and deployment).

## Home page flight

| Section | What the visitor feels |
| --- | --- |
| Hero | Stands on the runway. The sphere floats high ahead. Rolls, lifts the nose, takes off, flies to the sphere. The dots form the word, the word hits the camera and bursts into stars that stay for the whole page. |
| Flight headlines | Climbs with the nose up. The headlines replace each other in the middle of the screen. |
| Climb ("the call ends") | Keeps climbing and levels off high above the grid. The brain shows far away and grows. |
| Brain | Cruises high. Arrives at the brain. Sentences float next to it, with no bubbles. The report forms as plain glowing lines. |
| Four agents | Starts a slow descent. The four agents pass by sideways, each one a big number, a title, one line and its output drawn in dots and lines. |
| Console | Still descending. The console and phone screens hang in the sky on thin cables. The features are plain text, no boxes. |
| Where the data goes | Lower. Three ways to run Homayar, drawn as three naked flows of points and lines. |
| Privacy | Low above the grid. The privacy promises as a plain list with hairlines. |
| Final step | The runway comes back. Flare, touchdown, roll. The final call to action is a headline and plain links on the runway. |

The engine keeps the floor, the runway, the stars and the speed lines on for every section of this flight. The camera height after the brain comes from how far the page is scrolled, so the descent is smooth across sections.

## Every page has its own scenario

The world is shared (grid floor, stars, teal light). The camera story is different on every page. Never reuse one page's camera movement on another page.

Wrap the whole page (including `<CommandCta naked />` at the end) in one element with the page's `data-scene`. Wrap text in `naked` so it stays readable over the grid.

| Page | Scene | Camera movement |
| --- | --- | --- |
| Home | `particles`, `tunnel`, `warp`, `brain`, `field` | Runway, take-off, flight, landing (see above). |
| How it works | `ride` | Rides the ribbon like a track: hills, banked turns, one gate per step. The gate passes when its text is in the middle of the screen. After the last gate the track climbs into the sky. |
| Deployment | `map` | Looks straight down at the grid like a map with three zones (cloud, your server with a fence, hybrid split in two). Comes down to the zone the Homayar block is in, then climbs back up. |
| Security | `vault` | Tilts to look straight down, then drops through the floor into a shaft. One glowing plate with a hole per security layer; a plate passes while its layer text is in front. At the bottom the audio file sits locked in a cage; the camera circles it slowly, and it dims so the long text stays readable. |
| Product | `gallery` | A visit to a gallery. The visitor's eyes are the camera. See "Product: the gallery visit" below. |
| Solutions | `orbit` | The camera comes down from above and walks up to a ring of four role screens around a glowing core (a sphere of points with three orbits). Scrolling turns the ring; it stops at each role so it can be read, and a beam from the core lights the role in front. |
| Pricing | `lift` | Stands in front of an open lift shaft and looks up, then steps in and rides straight up. The lift stops at each floor, where one plan waits; the grid falls away below the higher it goes. |
| About | `dome` | Looks up from the horizon to the night sky. Each principle is a bright star with its text under it. Scrolling pans from star to star and draws the constellation line between them; text far from the centre of the view is dimmed. After the last star the view comes back down to the horizon for the rest of the page. |
| Contact | `beacon` | One bright beacon stands far away on the grid, in the gap between the form and the side column. Light rises along it and rings pulse out from its foot. The camera drifts slowly toward it, a little with time and more with the scroll. The form stays still, so typing is easy. |
| Review time calculator (`/roi`) | `stacks` | Two towers of light stand on the grid at the inline-end side: today's review hours and the hours with Homayar. The dotted part above the shorter tower is the saved time; the towers follow the calculator inputs. The camera walks up to them, rises to the level where they differ, steps sideways around the gap while the formulas are read, then backs off for the call to action. It stays almost still while you type. |

References the owner liked: "Active Theory" (the scroll moves the 3D world, not the page), "Lusion" (one ribbon through connected steps), "Virtually Ever After" (screens hanging in a point-cloud world), "The Shape of Intelligence" (kinetic headlines).

### Review time calculator: two towers of hours

The page text stays in one column at the inline-start side. The towers stand in the open space beside it (on phones, small and far behind the text).

1. **Arrival.** The visitor stands on the grid at eye height. The two towers are far away: "today" on the reading-start side, "with Homayar" beside it.
2. **The gap.** While the fields and the result are on screen, the camera walks closer and rises to the top of the shorter tower, so the dotted gap (the saved hours) is at eye level. The gap has the same footprint as the tower under it, and a bracket beside it marks its size.
3. **Around it.** While the formulas are read, the camera steps sideways around the towers, so the gap shows its depth.
4. **Back.** For the call to action the camera comes down to eye height and backs off.

The calculator writes H1 and H2 on `[data-roi]`; the engine reads them every frame and the towers glide to the new heights. The taller tower is a fixed height once the hours reach the example value (160), so the ratio stays exact and the towers never leave the view. An invalid input lowers both towers to the floor and dims them. Without WebGL or with reduced motion the same comparison is drawn in the page as two rows of dots.

### Product: the gallery visit

The visitor walks down a long hall. Paintings hang on the walls, one after another, on the left and the right. Each painting holds one part of the product.

1. **Entrance.** The hero text stands at the door. Scrolling walks the camera into the hall.
2. **Walk.** The camera walks down the middle of the hall, at eye height. All paintings are visible with their content; the next one comes closer on one wall.
3. **Stop and turn.** At the painting the camera stops and turns to face it, the way a visitor turns their head. It steps closer until the frame fills the screen.
4. **Look.** The painting fills the screen: number, title, text and the product screen for that part. The visitor reads it for a while. The mouse sway stops here, so the content stays still.
5. **Move on.** The camera turns back to the hall and walks to the next painting. The painting stays on the wall with its content.
6. **The masterpiece.** The last painting hangs on the end wall of the hall: the live demo. The camera walks straight up to it.
7. **Exit.** The camera steps back from the end wall and the call to action appears.

Paintings: the seven parts of the product (ingest, transcription, agents, dashboard, reports, copilot, admin), then the live demo. Every painting always shows its content, also from far away. The exhibits are real HTML hung on the walls with CSS 3D transforms that use the scene camera (the method of three.js CSS3DRenderer). When the camera faces a painting from its viewing distance, the painting fills the same part of the screen as the exhibit's CSS size, so it is easy to read.
