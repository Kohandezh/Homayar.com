"use client";

import dynamic from "next/dynamic";
import {
  Component,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactNode,
} from "react";

const VoiceField = dynamic(() => import("./voice-field"), { ssr: false });

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Probe for a usable, hardware-accelerated WebGL context once. Returns false
 *  when WebGL is missing/disabled (old iOS 16, low-end Android, locked down) OR
 *  when the browser is falling back to a SOFTWARE renderer (SwiftShader,
 *  llvmpipe, Microsoft Basic Render) — those rasterize the particle field on the
 *  CPU and make the whole page crawl, so we drop to the pure-CSS gradient. */
function hasWebGL() {
  if (typeof window === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    if (!gl) return false;
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    if (info) {
      const renderer = String(
        gl.getParameter(info.UNMASKED_RENDERER_WEBGL) || "",
      );
      if (/swiftshader|llvmpipe|software|basic render|paint/i.test(renderer)) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}

/** Stops any runtime error inside the WebGL subtree (lost context, driver bug,
 *  out-of-memory on an old GPU) from bubbling up and blanking the page. On
 *  failure it simply renders nothing — the CSS gradient behind it remains. */
class CanvasErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Hydration-safe media-query read: false on the server, live on the client.
 *  Uses useSyncExternalStore so there is no setState-in-effect cascade. */
function useMedia(query: string) {
  return useSyncExternalStore(
    (onChange) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** False during SSR and the first client render, true afterwards. */
function useMounted() {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

/** True only while the referenced element is on screen AND the tab is visible.
 *  Drives the WebGL render loop so it pauses the moment the hero scrolls away or
 *  the tab is backgrounded — no wasted GPU, and the page stays responsive. */
function useActiveInView(ref: React.RefObject<HTMLElement | null>) {
  const [active, setActive] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let inView = true;
    let tabVisible = document.visibilityState === "visible";
    const update = () => setActive(inView && tabVisible);
    const io = new IntersectionObserver(
      ([entry]) => {
        inView = entry.isIntersecting;
        update();
      },
      { threshold: 0.01 },
    );
    io.observe(el);
    const onVis = () => {
      tabVisible = document.visibilityState === "visible";
      update();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [ref]);
  return active;
}

/** Lazy WebGL hero. Renders the 3D particle field on every viewport that is OK
 *  with motion — a lighter, lower-density build on phones — and keeps it masked
 *  to the trailing edge so it never collides with the hero text or card borders.
 *  When motion is reduced the CSS gradient behind it carries the look. */
export function HeroCanvas() {
  const mounted = useMounted();
  const reduced = useMedia("(prefers-reduced-motion: reduce)");
  const mobile = useMedia("(max-width: 767px)");
  const hostRef = useRef<HTMLDivElement>(null);
  const active = useActiveInView(hostRef);
  // Probe WebGL only on the client, once. No setState-in-effect.
  const webgl = useMemo(() => (mounted ? hasWebGL() : false), [mounted]);

  if (!mounted || reduced || !webgl) return null;

  return (
    <div
      ref={hostRef}
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
      aria-hidden
    >
      <div
        className="absolute end-[-30%] top-[3%] h-[22rem] w-[22rem] opacity-40 sm:end-[-14%] sm:h-[30rem] sm:w-[30rem] sm:opacity-60 md:end-[-8%] md:opacity-90 lg:end-[-6%] lg:top-1/2 lg:h-[44rem] lg:w-[44rem] lg:max-w-[55vw] lg:-translate-y-1/2 lg:opacity-100"
        style={{
          WebkitMaskImage:
            "radial-gradient(closest-side, #000 68%, transparent 100%)",
          maskImage: "radial-gradient(closest-side, #000 68%, transparent 100%)",
        }}
      >
        {/* keyed so the buffer geometry rebuilds cleanly when density flips.
            Bloom post-processing is dropped on phones — it's the heaviest GPU
            pass and the most likely to jank or fail on old mobile hardware.
            visible pauses the render loop when the hero is offscreen/tab hidden. */}
        <CanvasErrorBoundary key={mobile ? "low" : "high"}>
          <VoiceField density={mobile ? "low" : "high"} bloom={!mobile} visible={active} />
        </CanvasErrorBoundary>
      </div>
    </div>
  );
}

/** Fake-3D tilt: the card leans toward the pointer with a soft parallax sheen. */
export function TiltCard({
  children,
  className = "",
  max = 7,
}: {
  children: ReactNode;
  className?: string;
  max?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<CSSProperties>({});
  const reduced = useRef(false);

  useEffect(() => {
    reduced.current = prefersReducedMotion();
  }, []);

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    if (reduced.current) return;
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    setStyle({
      transform: `perspective(900px) rotateX(${-py * max}deg) rotateY(${px * max}deg) translateZ(6px)`,
      // light sheen following the cursor
      ["--mx" as string]: `${(px + 0.5) * 100}%`,
      ["--my" as string]: `${(py + 0.5) * 100}%`,
    });
  }

  function reset() {
    setStyle({});
  }

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={reset}
      style={style}
      className={`tilt-card liquid-edge transition-transform duration-200 ease-out will-change-transform ${className}`}
    >
      {children}
    </div>
  );
}
