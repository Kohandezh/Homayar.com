import { DESKTOP, FINE_POINTER, MOTION_OK, ScrollTrigger, SplitText, gsap } from "@/lib/gsap";
import { localizeDigits, type Locale } from "@/lib/i18n";
import { sceneBus } from "@/components/scene/bus";
import { BRAIN_LINES, ENTER_END, lineAt, REPORT_IN, REPORT_OUT, REPORT_PEAK } from "@/components/scene/timing";
import { setupScenarios } from "./scenarios";

type Cleanup = () => void;
const PHONE = "(max-width: 767px)";

function q<T extends Element>(root: ParentNode, selector: string): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

export function setupPage(root: HTMLElement, locale: Locale): Cleanup {
  const mm = gsap.matchMedia();
  const rtl = locale === "fa";

  mm.add(MOTION_OK, () => {
    gsap.fromTo(
      root,
      { opacity: 0, y: 14, filter: "blur(6px)" },
      { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.52, ease: "expo.out", clearProps: "all", onComplete: () => ScrollTrigger.refresh() },
    );

    const line = document.querySelector<HTMLElement>(".progress-line");
    if (line) {
      gsap.fromTo(line, { scaleX: 0 }, { scaleX: 1, ease: "none", scrollTrigger: { start: 0, end: "max", scrub: 0.3 } });
    }

    for (const section of q<HTMLElement>(root, "[data-particles]")) {
      const hint = section.querySelector<HTMLElement>(".particles-hint");
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: "+=200%",
          pin: true,
          scrub: 0.5,
          anticipatePin: 1,
          onUpdate: (self) => sceneBus.set(section, self.progress),
        },
      });
      tl.to({}, { duration: 1 }, 0);
      if (hint) tl.to(hint, { autoAlpha: 0, y: 10, duration: 0.12, ease: "none" }, 0.04);
    }

    for (const section of q<HTMLElement>(root, "[data-flight]")) {
      const items = q<HTMLElement>(section, "[data-flight-item]");
      const content = section.querySelector<HTMLElement>("[data-flight-content]");
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: "+=260%",
          pin: true,
          scrub: 0.6,
          anticipatePin: 1,
          onUpdate: (self) => sceneBus.set(section, self.progress),
        },
      });
      tl.to({}, { duration: 1 }, 0);
      const heads = q<HTMLElement>(section, "[data-headline]");
      const n = Math.max(1, heads.length);
      const seg = 1 / n;
      heads.forEach((el, i) => {
        const at = i * seg;
        tl.fromTo(el, { autoAlpha: 0, y: 48, filter: "blur(10px)" }, { autoAlpha: 1, y: 0, filter: "blur(0px)", duration: seg * 0.28, ease: "none" }, at);
        if (i < n - 1) tl.to(el, { autoAlpha: 0, y: -44, filter: "blur(10px)", duration: seg * 0.24, ease: "none" }, at + seg * 0.72);
      });
      const base = (n - 1) * seg + seg * 0.3;
      items.forEach((el, i) => {
        const at = Math.min(0.92, base + i * (seg * 0.6) / Math.max(1, items.length));
        tl.fromTo(el, { autoAlpha: 0, y: 36, z: -120 }, { autoAlpha: 1, y: 0, z: 0, duration: 0.07, ease: "none" }, at);
      });
      if (content) tl.fromTo(content, { scale: 1 }, { scale: 1.03, y: -18, duration: 1, ease: "none" }, 0);
    }

    for (const section of q<HTMLElement>(root, "[data-warp]")) {
      const text = section.querySelector<HTMLElement>("[data-warp-text]");
      const flash = section.querySelector<HTMLElement>("[data-warp-flash]");
      const tl = gsap.timeline({ scrollTrigger: { trigger: section, start: "top bottom", end: "bottom top", scrub: 0.4 } });
      tl.to({}, { duration: 1 }, 0);
      if (text) {
        tl.fromTo(text, { autoAlpha: 0, scale: 0.9 }, { autoAlpha: 1, scale: 1, duration: 0.18, ease: "none" }, 0.18);
        tl.to(text, { autoAlpha: 0, scale: 1.12, filter: "blur(6px)", duration: 0.16, ease: "none" }, 0.62);
      }
      if (flash) {
        tl.fromTo(flash, { opacity: 0 }, { opacity: 1, duration: 0.14, ease: "none" }, 0.74);
        tl.to(flash, { opacity: 0, duration: 0.12, ease: "none" }, 0.88);
      }
    }

    for (const section of q<HTMLElement>(root, "[data-brain]")) {
      const lines = q<HTMLElement>(section, "[data-line]").slice(0, BRAIN_LINES);
      const core = section.querySelector<HTMLElement>("[data-brain-core]");
      const intro = section.querySelector<HTMLElement>("[data-brain-intro]");
      const report = section.querySelector<HTMLElement>("[data-brain-report]");
      const statusOn = section.querySelector<HTMLElement>("[data-brain-analyzing]");
      const statusDone = section.querySelector<HTMLElement>("[data-brain-ready]");
      const phone = window.matchMedia(PHONE).matches;
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: phone ? "+=380%" : "+=520%",
          pin: true,
          scrub: 0.5,
          anticipatePin: 1,
          onUpdate: (self) => sceneBus.set(section, self.progress),
        },
      });
      tl.to({}, { duration: 1 }, 0);
      if (intro) tl.to(intro, { autoAlpha: 0, y: -40, duration: ENTER_END * 0.7, ease: "none" }, ENTER_END * 0.3);
      if (statusOn) tl.fromTo(statusOn, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.03, ease: "none" }, ENTER_END);
      lines.forEach((el, i) => {
        const at = lineAt(i);
        const side = el.classList.contains("brain-line-customer") ? -1 : 1;
        const dir = rtl ? -side : side;
        tl.fromTo(el, { autoAlpha: 0, x: dir * 48, scale: 0.94 }, { autoAlpha: 1, x: 0, scale: 1, duration: 0.05, ease: "none" }, at);
        if (core) tl.fromTo(core, { scale: 1 }, { scale: 1.28, duration: 0.025, yoyo: true, repeat: 1, ease: "none" }, at);
      });
      if (lines.length) tl.to(lines, { autoAlpha: 0.15, scale: 0.9, duration: 0.05, ease: "none" }, REPORT_IN - 0.05);
      if (statusOn) tl.to(statusOn, { autoAlpha: 0, duration: 0.02, ease: "none" }, REPORT_IN - 0.02);
      if (statusDone) tl.fromTo(statusDone, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.03, ease: "none" }, REPORT_IN);
      if (core) tl.to(core, { scale: 0.4, autoAlpha: 0, duration: REPORT_PEAK - REPORT_IN, ease: "none" }, REPORT_IN);
      if (report) {
        tl.fromTo(report, { autoAlpha: 0, scale: 0.5, y: 80 }, { autoAlpha: 1, scale: 1, y: 0, duration: REPORT_PEAK - REPORT_IN, ease: "none" }, REPORT_IN);
        tl.to(report, { scale: 1.5, autoAlpha: 0, filter: "blur(10px)", duration: REPORT_OUT - REPORT_PEAK, ease: "none" }, REPORT_PEAK);
      }
      if (statusDone) tl.to(statusDone, { autoAlpha: 0, duration: 0.04, ease: "none" }, REPORT_OUT - 0.04);
    }

    for (const section of q<HTMLElement>(root, "[data-journey]")) {
      const track = section.querySelector<HTMLElement>("[data-journey-track]");
      const bar = section.querySelector<HTMLElement>("[data-journey-progress]");
      if (!track) continue;
      mm.add(DESKTOP, () => {
        const dist = () => Math.max(0, track.scrollWidth - window.innerWidth);
        const dir = rtl ? 1 : -1;
        const tween = gsap.to(track, {
          x: () => dir * dist(),
          ease: "none",
          scrollTrigger: {
            trigger: section,
            start: "top top",
            end: () => `+=${dist()}`,
            pin: true,
            scrub: 1,
            anticipatePin: 1,
            invalidateOnRefresh: true,
            onUpdate: (self) => {
              if (bar) gsap.set(bar, { scaleX: self.progress });
            },
          },
        });
        for (const panel of q<HTMLElement>(track, "[data-journey-panel]")) {
          gsap.fromTo(
            panel,
            { scale: 0.92, opacity: 0.55 },
            {
              scale: 1,
              opacity: 1,
              ease: "none",
              scrollTrigger: {
                containerAnimation: tween,
                trigger: panel,
                start: rtl ? "right 20%" : "left 80%",
                end: rtl ? "right 60%" : "left 40%",
                scrub: true,
              },
            },
          );
        }
      });
    }

    for (const el of q<HTMLElement>(root, "[data-depth]")) {
      el.parentElement?.classList.add("depth-stage");
      const amount = Number(el.dataset.depth || 320);
      gsap.fromTo(
        el,
        { z: -amount, opacity: 0, y: 40 },
        { z: 0, opacity: 1, y: 0, ease: "none", scrollTrigger: { trigger: el, start: "top 100%", end: "top 55%", scrub: 0.6 } },
      );
    }

    for (const section of q<HTMLElement>(root, "[data-hero-scene]")) {
      const content = section.querySelector<HTMLElement>("[data-hero-content]");
      if (!content) continue;
      gsap.to(content, { yPercent: -10, opacity: 0.25, ease: "none", scrollTrigger: { trigger: section, start: "45% top", end: "bottom top", scrub: true } });
    }

    for (const el of q<HTMLElement>(root, "[data-split]")) {
      SplitText.create(el, {
        type: "words",
        aria: "auto",
        onSplit(self) {
          gsap.set(el, { autoAlpha: 1 });
          return gsap.from(self.words, {
            yPercent: 70,
            opacity: 0,
            duration: 0.9,
            stagger: 0.045,
            ease: "expo.out",
            scrollTrigger: { trigger: el, start: "top 90%", once: true },
          });
        },
      });
    }

    for (const el of q<HTMLElement>(root, "[data-reveal]")) {
      const delay = Number(el.dataset.reveal || 0);
      gsap.fromTo(el, { opacity: 0, y: 28 }, { opacity: 1, y: 0, duration: 0.9, delay, ease: "expo.out", scrollTrigger: { trigger: el, start: "top 92%", once: true } });
    }

    for (const group of q<HTMLElement>(root, "[data-reveal-group]")) {
      const children = Array.from(group.children) as HTMLElement[];
      if (children.length === 0) continue;
      gsap.fromTo(children, { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.8, stagger: 0.07, ease: "expo.out", scrollTrigger: { trigger: group, start: "top 88%", once: true } });
    }

    for (const el of q<HTMLElement>(root, "[data-parallax]")) {
      const amount = Number(el.dataset.parallax || -10);
      gsap.fromTo(el, { yPercent: -amount }, { yPercent: amount, ease: "none", scrollTrigger: { trigger: el.parentElement ?? el, start: "top bottom", end: "bottom top", scrub: true } });
    }

    for (const el of q<HTMLElement>(root, "[data-count]")) {
      const target = Number(el.dataset.count || 0);
      const suffix = el.dataset.suffix ?? "";
      const state = { v: 0 };
      gsap.to(state, {
        v: target,
        duration: 1.4,
        ease: "power2.out",
        scrollTrigger: { trigger: el, start: "top 90%", once: true },
        onUpdate: () => {
          el.textContent = localizeDigits(Math.round(state.v).toLocaleString("en-US"), locale) + suffix;
        },
      });
    }

    for (const rail of q<HTMLElement>(root, "[data-rail]")) {
      const fill = rail.querySelector<HTMLElement>(".rail-fill");
      if (fill) gsap.fromTo(fill, { scaleY: 0 }, { scaleY: 1, ease: "none", scrollTrigger: { trigger: rail, start: "top 65%", end: "bottom 65%", scrub: true } });
      for (const step of q<HTMLElement>(rail, "[data-step]")) {
        ScrollTrigger.create({ trigger: step, start: "top 65%", once: true, onEnter: () => step.classList.add("is-done") });
      }
    }

    setupScenarios(root, locale, mm);

    mm.add(FINE_POINTER, () => {
      const handlers: Array<() => void> = [];
      for (const el of q<HTMLElement>(root, "[data-magnetic]")) {
        const xTo = gsap.quickTo(el, "x", { duration: 0.45, ease: "power3" });
        const yTo = gsap.quickTo(el, "y", { duration: 0.45, ease: "power3" });
        const move = (e: PointerEvent) => {
          const r = el.getBoundingClientRect();
          xTo((e.clientX - r.left - r.width / 2) * 0.28);
          yTo((e.clientY - r.top - r.height / 2) * 0.28);
        };
        const leave = () => {
          xTo(0);
          yTo(0);
        };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerleave", leave);
        handlers.push(() => {
          el.removeEventListener("pointermove", move);
          el.removeEventListener("pointerleave", leave);
        });
      }
      return () => handlers.forEach((h) => h());
    });

    const refresh = () => ScrollTrigger.refresh();
    document.fonts?.ready.then(refresh).catch(() => undefined);
    const t = window.setTimeout(refresh, 400);
    return () => window.clearTimeout(t);
  });

  return () => mm.revert();
}
