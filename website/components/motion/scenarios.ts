import { DESKTOP, ScrollTrigger, gsap } from "@/lib/gsap";
import type { Locale } from "@/lib/i18n";
import { sceneBus } from "@/components/scene/bus";

type MM = ReturnType<typeof gsap.matchMedia>;

function q<T extends Element>(root: ParentNode, selector: string): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

export function setupScenarios(root: HTMLElement, locale: Locale, mm: MM) {
  const rtl = locale === "fa";
  const dir = rtl ? -1 : 1;

  // Product gallery: the section is a scroll track. Each painting gets an equal part of it.
  // The engine reads this progress to walk the camera and hang the exhibits in the hall.
  for (const section of q<HTMLElement>(root, "[data-exhibits]")) {
    const fit = section.querySelector<HTMLElement>("[data-exhibit-fit]");
    const inner = section.querySelector<HTMLElement>("[data-exhibit-fit-inner]");
    // The live demo is larger than the painting, so it is scaled down to fit inside it.
    const scaleDemo = () => {
      if (!fit || !inner || !fit.clientWidth) return;
      const k = Math.min(1, fit.clientWidth / inner.offsetWidth, fit.clientHeight / Math.max(1, inner.offsetHeight));
      inner.style.transform = `translateX(-50%) scale(${k})`;
    };
    ScrollTrigger.create({ trigger: section, start: "top top", end: "bottom bottom", onUpdate: (self) => sceneBus.set(section, self.progress), onRefresh: scaleDemo });
    const ro = new ResizeObserver(scaleDemo);
    if (fit) ro.observe(fit);
    if (inner) ro.observe(inner);
    mm.add("all", () => () => ro.disconnect());
  }

  // Solutions ring and pricing lift: the section is a scroll track; the engine reads its progress.
  for (const section of q<HTMLElement>(root, "[data-roles], [data-floors], [data-stars]")) {
    ScrollTrigger.create({ trigger: section, start: "top top", end: "bottom bottom", onUpdate: (self) => sceneBus.set(section, self.progress) });
  }

  for (const section of q<HTMLElement>(root, "[data-snake]")) {
    const path = section.querySelector<SVGPathElement>("[data-snake-line]");
    const glow = section.querySelector<SVGPathElement>(".snake-glow");
    const head = section.querySelector<SVGCircleElement>("[data-snake-head]");
    const headGlow = section.querySelector<SVGCircleElement>(".snake-head-glow");
    const cards = q<HTMLElement>(section, "[data-snake-card]");
    if (!path || !head) continue;
    let total = 0;
    let table: Array<{ len: number; y: number }> = [];
    const build = () => {
      total = path.getTotalLength();
      if (!total) return;
      table = [];
      const n = 400;
      for (let i = 0; i <= n; i++) {
        const len = (total * i) / n;
        table.push({ len, y: path.getPointAtLength(len).y });
      }
      path.style.strokeDasharray = `${total}`;
      if (glow) glow.style.strokeDasharray = `${total}`;
      draw();
    };
    let lastLen = -1;
    const draw = () => {
      if (!total) return;
      const rect = section.getBoundingClientRect();
      const yLine = window.innerHeight * 0.6 - rect.top;
      let len = 0;
      if (yLine <= 0) len = 0;
      else if (yLine >= rect.height) len = total;
      else {
        let i = 0;
        while (i < table.length - 1 && table[i + 1].y < yLine) i++;
        const a = table[i];
        const b = table[Math.min(table.length - 1, i + 1)];
        const t = b.y === a.y ? 0 : Math.min(1, Math.max(0, (yLine - a.y) / (b.y - a.y)));
        len = a.len + (b.len - a.len) * t;
      }
      if (Math.abs(len - lastLen) < 0.25) return;
      lastLen = len;
      const off = total - len;
      path.style.strokeDashoffset = `${off}`;
      if (glow) glow.style.strokeDashoffset = `${off}`;
      const pt = path.getPointAtLength(len);
      head.setAttribute("cx", pt.x.toFixed(1));
      head.setAttribute("cy", pt.y.toFixed(1));
      headGlow?.setAttribute("cx", pt.x.toFixed(1));
      headGlow?.setAttribute("cy", pt.y.toFixed(1));
      const headY = pt.y + rect.top;
      cards.forEach((card) => {
        const r = card.getBoundingClientRect();
        const active = headY >= r.top - 24 && headY <= r.bottom + 24;
        const done = headY > r.bottom + 24;
        card.classList.toggle("is-active", active);
        card.classList.toggle("is-done", done);
      });
    };
    section.addEventListener("snake:update", build);
    build();
    ScrollTrigger.create({ trigger: section, start: "top bottom", end: "bottom top", onUpdate: draw, onRefresh: build });
    mm.add("all", () => () => section.removeEventListener("snake:update", build));
  }

  for (const section of q<HTMLElement>(root, "[data-console]")) {
    mm.add(DESKTOP, () => {
      const pin = section.querySelector<HTMLElement>(".console-pin") ?? section;
      const texts = q<HTMLElement>(section, "[data-dock-text]");
      const docks = q<HTMLElement>(section, "[data-dock]");
      const slots = q<HTMLElement>(section, ".console-slot");
      const win = section.querySelector<HTMLElement>("[data-console-window]");
      const status = section.querySelector<HTMLElement>("[data-console-status]");
      const complete = status?.textContent ?? "";
      const n = docks.length;
      if (!n) return;
      const tl = gsap.timeline({
        scrollTrigger: { trigger: section, start: "top top", end: `+=${n * 70}%`, pin, scrub: 0.6, anticipatePin: 1 },
      });
      tl.to({}, { duration: 1 }, 0);
      if (win) tl.fromTo(win, { scale: 0.94, y: 24 }, { scale: 1, y: 0, duration: 1, ease: "none" }, 0);
      const seg = 1 / n;
      docks.forEach((dock, i) => {
        const at = i * seg;
        tl.fromTo(dock, { autoAlpha: 0, scale: 0.55, y: 160, x: dir * 60 }, { autoAlpha: 1, scale: 1, y: 0, x: 0, duration: seg * 0.55, ease: "none" }, at);
        tl.call(() => slots[i]?.classList.add("is-filled"), [], at + seg * 0.5);
        tl.call(() => slots[i]?.classList.remove("is-filled"), [], at + seg * 0.5 - 0.0001);
        if (texts[i]) {
          tl.fromTo(texts[i], { autoAlpha: 0, y: 24 }, { autoAlpha: 1, y: 0, duration: seg * 0.3, ease: "none" }, at);
          if (i < n - 1) tl.to(texts[i], { autoAlpha: 0, y: -20, duration: seg * 0.2, ease: "none" }, at + seg * 0.8);
        }
        if (status) {
          tl.call(() => (status.textContent = i === n - 1 ? complete : `${i + 1} / ${n}`), [], at + seg * 0.5);
        }
      });
      const fixOrder = () => {
        slots.forEach((s, i) => s.classList.toggle("is-filled", tl.progress() >= (i + 0.5) * seg));
      };
      tl.eventCallback("onUpdate", fixOrder);
      return () => slots.forEach((s) => s.classList.remove("is-filled"));
    });
  }

  for (const section of q<HTMLElement>(root, "[data-ring]")) {
    mm.add(DESKTOP, () => {
      const pin = section.querySelector<HTMLElement>(".ring-pin") ?? section;
      const wheel = section.querySelector<HTMLElement>("[data-ring-wheel]");
      const seats = q<HTMLElement>(section, "[data-ring-seat]");
      const views = q<HTMLElement>(section, "[data-ring-view]");
      if (!wheel || seats.length === 0) return;
      const n = seats.length;
      gsap.set(views, { autoAlpha: (i: number) => (i === 0 ? 1 : 0) });
      let active = 0;
      const setActive = (k: number) => {
        if (k === active) return;
        active = k;
        seats.forEach((s, i) => s.classList.toggle("is-front", i === k));
        views.forEach((v, i) => gsap.to(v, { autoAlpha: i === k ? 1 : 0, y: i === k ? 0 : 8, duration: 0.35, overwrite: true }));
      };
      gsap.to(wheel, {
        rotationY: -dir * 90 * (n - 1),
        ease: "none",
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: `+=${(n - 1) * 90}%`,
          pin,
          scrub: 0.7,
          anticipatePin: 1,
          onUpdate: (self) => setActive(Math.min(n - 1, Math.round(self.progress * (n - 1)))),
        },
      });
      return () => {
        seats.forEach((s, i) => s.classList.toggle("is-front", i === 0));
        gsap.set(views, { clearProps: "all" });
      };
    });
  }

  for (const section of q<HTMLElement>(root, "[data-layers]")) {
    mm.add(DESKTOP, () => {
      const pin = section.querySelector<HTMLElement>(".layers-pin") ?? section;
      const layers = q<HTMLElement>(section, "[data-layer]");
      const head = section.querySelector<HTMLElement>(".layers-head");
      const n = layers.length;
      if (!n) return;
      const tl = gsap.timeline({ scrollTrigger: { trigger: section, start: "top top", end: `+=${n * 95}%`, pin, scrub: 0.6, anticipatePin: 1 } });
      tl.to({}, { duration: 1 }, 0);
      const seg = 1 / n;
      gsap.set(layers, { xPercent: -50, yPercent: -50 });
      if (head) tl.to(head, { autoAlpha: 0.35, y: -20, duration: seg * 0.6, ease: "none" }, seg * 0.4);
      layers.forEach((layer, i) => {
        const at = i * seg;
        tl.fromTo(layer, { autoAlpha: 0, scale: 0.35, z: -600 }, { autoAlpha: 1, scale: 1, z: 0, duration: seg * 0.5, ease: "none" }, at);
        if (i < n - 1) tl.to(layer, { scale: 2.6, autoAlpha: 0, z: 400, duration: seg * 0.42, ease: "none" }, at + seg * 0.58);
      });
    });
  }

  for (const box of q<HTMLElement>(root, "[data-mask]")) {
    const chars = q<HTMLElement>(box, ".mask-ch");
    const placeholder = box.querySelector<HTMLElement>(".mask-placeholder");
    const badge = box.querySelector<HTMLElement>(".mask-badge");
    if (!placeholder) continue;
    const tl = gsap.timeline({ scrollTrigger: { trigger: box, start: "top 72%", end: "top 28%", scrub: 0.5 } });
    tl.to({}, { duration: 1 }, 0);
    if (chars.length) {
      tl.to(
        chars,
        {
          y: (i: number) => (i % 2 ? -1 : 1) * (10 + ((i * 7) % 12)),
          x: (i: number) => (i % 3 === 0 ? -6 : 6),
          opacity: 0,
          filter: "blur(4px)",
          duration: 0.55,
          ease: "none",
          stagger: { each: 0.03, from: "random" },
        },
        0,
      );
    }
    tl.fromTo(placeholder, { autoAlpha: 0, scale: 0.6 }, { autoAlpha: 1, scale: 1, duration: 0.35, ease: "none" }, 0.4);
    if (badge) tl.fromTo(badge, { autoAlpha: 0, scale: 0.8 }, { autoAlpha: 1, scale: 1, duration: 0.25, ease: "none" }, 0.65);
  }

  for (const section of q<HTMLElement>(root, "[data-zones]")) {
    mm.add(DESKTOP, () => {
      const pin = section.querySelector<HTMLElement>(".zones-pin") ?? section;
      const block = section.querySelector<HTMLElement>("[data-block]");
      const slots = q<HTMLElement>(section, "[data-zone-slot]");
      const zones = q<HTMLElement>(section, "[data-zone]");
      const wall = section.querySelector<HTMLElement>("[data-wall]");
      const halves = block ? q<HTMLElement>(block, "[data-half]") : [];
      const tags = block ? q<HTMLElement>(block, ".zblock-tag") : [];
      if (!block || slots.length < 3) return;
      const xTo = (i: number) => {
        const s = slots[i].getBoundingClientRect();
        const b = block.getBoundingClientRect();
        const cur = Number(gsap.getProperty(block, "x")) || 0;
        return s.left + s.width / 2 - (b.left + b.width / 2 - cur);
      };
      const yTo = () => {
        const s = slots[0].getBoundingClientRect();
        const b = block.getBoundingClientRect();
        const cur = Number(gsap.getProperty(block, "y")) || 0;
        return s.top + s.height / 2 - (b.top + b.height / 2 - cur);
      };
      gsap.set(block, { x: () => xTo(0), y: () => yTo() });
      gsap.set(wall, { yPercent: -110 });
      gsap.set(tags, { autoAlpha: 0 });
      const setZone = (k: number) => zones.forEach((z, i) => z.classList.toggle("is-active", i === k));
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: "+=320%",
          pin,
          scrub: 0.6,
          anticipatePin: 1,
          invalidateOnRefresh: true,
          onRefresh: () => gsap.set(block, { x: () => xTo(0), y: () => yTo() }),
          onUpdate: (self) => setZone(self.progress < 0.3 ? 0 : self.progress < 0.68 ? 1 : 2),
        },
      });
      tl.to({}, { duration: 1 }, 0);
      tl.to(block, { x: () => xTo(1), duration: 0.18, ease: "none" }, 0.18);
      if (wall) tl.to(wall, { yPercent: 0, duration: 0.12, ease: "none" }, 0.4);
      tl.to(block, { x: () => xTo(2), duration: 0.18, ease: "none" }, 0.58);
      if (halves.length === 2) {
        tl.to(halves[0], { x: -dir * 44, duration: 0.14, ease: "none" }, 0.8);
        tl.to(halves[1], { x: dir * 44, duration: 0.14, ease: "none" }, 0.8);
      }
      tl.to(tags, { autoAlpha: 1, duration: 0.1, ease: "none" }, 0.9);
      return () => setZone(0);
    });
  }

  for (const table of q<HTMLElement>(root, "[data-table-build]")) {
    const rows = q<HTMLElement>(table, "tbody tr");
    if (!rows.length) continue;
    gsap.fromTo(
      rows,
      { autoAlpha: 0, x: dir * 28 },
      { autoAlpha: 1, x: 0, ease: "none", stagger: 0.18, scrollTrigger: { trigger: table, start: "top 85%", end: "bottom 55%", scrub: 0.5 } },
    );
  }

  for (const section of q<HTMLElement>(root, "[data-constellation]")) {
    const links = section.querySelector<SVGPathElement>("[data-const-links]");
    if (!links) continue;
    let tween: gsap.core.Tween | undefined;
    const build = () => {
      const total = links.getTotalLength();
      if (!total) return;
      tween?.scrollTrigger?.kill();
      tween?.kill();
      links.style.strokeDasharray = `${total}`;
      tween = gsap.fromTo(
        links,
        { strokeDashoffset: total },
        { strokeDashoffset: 0, ease: "none", scrollTrigger: { trigger: section, start: "top 75%", end: "bottom 70%", scrub: 0.4 } },
      );
    };
    section.addEventListener("constellation:update", build);
    build();
    mm.add("all", () => () => section.removeEventListener("constellation:update", build));
  }

  for (const section of q<HTMLElement>(root, "[data-stepper]")) {
    mm.add(DESKTOP, () => {
      const pin = section.querySelector<HTMLElement>(".stepper-pin") ?? section;
      const steps = q<HTMLElement>(section, "[data-stepper-step]");
      const dots = q<HTMLElement>(section, "[data-stepper-dot]");
      const n = steps.length;
      if (n < 2) return;
      gsap.set(steps, { autoAlpha: (i: number) => (i === 0 ? 1 : 0) });
      const tl = gsap.timeline({ scrollTrigger: { trigger: section, start: "top top", end: `+=${(n - 1) * 75}%`, pin, scrub: 0.6, anticipatePin: 1 } });
      tl.to({}, { duration: 1 }, 0);
      const seg = 1 / (n - 1);
      for (let i = 1; i < n; i++) {
        const at = (i - 1) * seg + seg * 0.35;
        tl.to(steps[i - 1], { autoAlpha: 0, y: -30, duration: seg * 0.3, ease: "none" }, at);
        tl.fromTo(steps[i], { autoAlpha: 0, y: 40 }, { autoAlpha: 1, y: 0, duration: seg * 0.3, ease: "none" }, at + seg * 0.15);
        tl.call(() => dots.forEach((d, k) => d.classList.toggle("is-active", k === i)), [], at + seg * 0.2);
        tl.call(() => dots.forEach((d, k) => d.classList.toggle("is-active", k === i - 1)), [], at + seg * 0.2 - 0.0001);
      }
      tl.eventCallback("onUpdate", () => {
        const k = Math.min(n - 1, Math.floor(tl.progress() / seg + 0.65));
        dots.forEach((d, i) => d.classList.toggle("is-active", i === k));
      });
      return () => {
        gsap.set(steps, { clearProps: "all" });
        dots.forEach((d, i) => d.classList.toggle("is-active", i === 0));
      };
    });
  }

  for (const mark of q<HTMLElement>(root, "[data-voice-react]")) {
    const bars = q<SVGRectElement>(mark, ".voice-live-bar");
    const scope = mark.closest<HTMLElement>("[data-voice-scope]") ?? root;
    if (!bars.length) continue;
    const idle = gsap.to(bars, {
      scaleY: (i: number) => 0.55 + 0.25 * Math.sin(i * 1.1),
      duration: 1.4,
      ease: "sine.inOut",
      yoyo: true,
      repeat: -1,
      stagger: { each: 0.09, yoyo: true, repeat: -1 },
      paused: true,
    });
    ScrollTrigger.create({ trigger: mark, start: "top bottom", end: "bottom top", onToggle: (self) => (self.isActive ? idle.play() : idle.pause()) });
    let relax = 0;
    const onInput = () => {
      idle.pause();
      gsap.to(bars, {
        scaleY: () => 0.4 + Math.random() * 1.5,
        duration: 0.16,
        ease: "power2.out",
        stagger: { each: 0.018, from: "center" },
        overwrite: true,
      });
      window.clearTimeout(relax);
      relax = window.setTimeout(() => {
        gsap.to(bars, { scaleY: 1, duration: 0.7, ease: "power2.inOut", overwrite: true, onComplete: () => idle.play() });
      }, 220);
    };
    scope.addEventListener("input", onInput);
    mm.add("all", () => () => {
      scope.removeEventListener("input", onInput);
      window.clearTimeout(relax);
    });
  }
}
