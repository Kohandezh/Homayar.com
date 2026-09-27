import { DESKTOP, FINE_POINTER, MOTION_OK, ScrollTrigger, SplitText, gsap } from "@/lib/gsap";
import { localizeDigits, type Locale } from "@/lib/i18n";

type Cleanup = () => void;

function q<T extends Element>(root: ParentNode, selector: string): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

export function setupPage(root: HTMLElement, locale: Locale): Cleanup {
  const mm = gsap.matchMedia();

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
      gsap.fromTo(
        el,
        { opacity: 0, y: 28 },
        { opacity: 1, y: 0, duration: 0.9, delay, ease: "expo.out", scrollTrigger: { trigger: el, start: "top 92%", once: true } },
      );
    }

    for (const group of q<HTMLElement>(root, "[data-reveal-group]")) {
      const children = Array.from(group.children) as HTMLElement[];
      if (children.length === 0) continue;
      gsap.fromTo(
        children,
        { opacity: 0, y: 24 },
        { opacity: 1, y: 0, duration: 0.8, stagger: 0.07, ease: "expo.out", scrollTrigger: { trigger: group, start: "top 88%", once: true } },
      );
    }

    for (const el of q<HTMLElement>(root, "[data-parallax]")) {
      const amount = Number(el.dataset.parallax || -10);
      gsap.fromTo(
        el,
        { yPercent: -amount },
        { yPercent: amount, ease: "none", scrollTrigger: { trigger: el.parentElement ?? el, start: "top bottom", end: "bottom top", scrub: true } },
      );
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
      if (fill) {
        gsap.fromTo(
          fill,
          { scaleY: 0 },
          { scaleY: 1, ease: "none", scrollTrigger: { trigger: rail, start: "top 65%", end: "bottom 65%", scrub: true } },
        );
      }
      for (const step of q<HTMLElement>(rail, "[data-step]")) {
        ScrollTrigger.create({
          trigger: step,
          start: "top 65%",
          once: true,
          onEnter: () => step.classList.add("is-done"),
        });
      }
    }

    for (const box of q<HTMLElement>(root, "[data-mask]")) {
      const original = box.querySelector<HTMLElement>(".mask-original");
      const masked = box.querySelector<HTMLElement>(".mask-masked");
      const badge = box.querySelector<HTMLElement>(".mask-badge");
      if (!original || !masked) continue;
      gsap
        .timeline({ scrollTrigger: { trigger: box, start: "top 75%", end: "top 25%", scrub: 0.5 } })
        .fromTo(original, { opacity: 1, y: 0 }, { opacity: 0, y: -10, ease: "none" }, 0)
        .fromTo(masked, { opacity: 0, y: 10 }, { opacity: 1, y: 0, ease: "none" }, 0)
        .fromTo(badge, { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1, ease: "none" }, 0.4);
    }

    for (const stage of q<HTMLElement>(root, "[data-stack]")) {
      const cards = q<HTMLElement>(stage, "[data-stack-card]");
      const items = q<HTMLElement>(stage, "[data-stack-item]");
      if (cards.length < 2) continue;
      mm.add(DESKTOP, () => {
        stage.classList.add("is-stacked");
        const setActive = (idx: number) => items.forEach((it, k) => it.classList.toggle("is-active", k === idx));
        setActive(0);
        const tl = gsap.timeline({
          scrollTrigger: {
            trigger: stage,
            start: "top top+=72",
            end: () => `+=${cards.length * 60}%`,
            pin: true,
            scrub: 0.6,
            anticipatePin: 1,
            onUpdate: (self) => setActive(Math.min(cards.length - 1, Math.floor(self.progress * cards.length * 0.999))),
          },
        });
        cards.forEach((card, i) => {
          if (i === 0) {
            gsap.set(card, { opacity: 1, y: 0, scale: 1 });
            return;
          }
          tl.fromTo(card, { opacity: 0, y: 140, scale: 0.94 }, { opacity: 1, y: 0, scale: 1, duration: 1, ease: "none" }, i - 1).to(
            cards[i - 1],
            { y: -40, scale: 0.95, opacity: 0, duration: 1, ease: "none" },
            i - 1,
          );
        });
        tl.to({}, { duration: 0.5 });
        return () => stage.classList.remove("is-stacked");
      });
    }

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
