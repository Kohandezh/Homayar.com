import { ImageResponse } from "next/og";
import { getDictionary } from "@/i18n";
import { isLocale, locales, type Locale } from "@/lib/i18n";

export const dynamicParams = false;

const W = 1200;
const H = 630;
const CX = W / 2;
const CY = H / 2 - 40;

function corridor() {
  const parts: string[] = [];
  const horizon = CY + 30;
  for (let i = -12; i <= 12; i++) parts.push(`M${CX + i * 130} ${H}L${CX + i * 4} ${horizon}`);
  for (let k = 0; k < 14; k++) {
    const y = horizon + (H - horizon) * Math.pow((k + 1) / 14, 2.2);
    parts.push(`M0 ${y.toFixed(1)}L${W} ${y.toFixed(1)}`);
  }
  return parts.join("");
}

function wave() {
  const pts: string[] = [];
  const n = 90;
  for (let i = 0; i <= n; i++) {
    const x = CX - 330 + (660 * i) / n;
    const u = (i / n) * Math.PI * 2;
    const env = Math.exp(-Math.pow((i / n - 0.5) * 2.6, 2));
    const y = CY + (Math.sin(u * 2.3) * 44 + Math.sin(u * 5.1) * 18 + Math.sin(u * 9.7) * 6) * env;
    pts.push(`${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  return pts.join("");
}

export function generateStaticParams() {
  return locales.map((locale) => ({ locale, id: "brand" }));
}

async function localeOf(params: Promise<{ locale: string; id: string }>): Promise<Locale> {
  const { locale } = await params;
  return isLocale(locale) ? locale : "fa";
}

export async function GET(_request: Request, { params }: { params: Promise<{ locale: string; id: string }> }) {
  const locale = await localeOf(params);
  const dict = await getDictionary(locale);
  const bars = [
    { x: 30, h: 22 },
    { x: 46, h: 44 },
    { x: 62, h: 66 },
    { x: 78, h: 44 },
    { x: 94, h: 26 },
  ];
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", position: "relative", background: "#0a161a", color: "#ffffff", fontFamily: "sans-serif" }}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position: "absolute", top: 0, left: 0 }}>
          <path d={corridor()} fill="none" stroke="#22bec6" strokeWidth="2" opacity="0.32" />
          <path d={wave()} fill="none" stroke="#7fdde2" strokeWidth="5" strokeLinecap="round" opacity="0.95" />
          <rect x="0" y="360" width={W} height="270" fill="#0a161a" opacity="0.92" />
        </svg>
        <div style={{ position: "absolute", left: 72, bottom: 64, display: "flex", alignItems: "center", gap: 28 }}>
          <div style={{ width: 124, height: 124, borderRadius: 32, background: "#22bec6", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="124" height="124" viewBox="0 0 124 124">
              {bars.map((b, i) => (
                <rect key={i} x={b.x - 5} y={62 - b.h / 2} width="10" height={b.h} rx="5" fill="#031011" />
              ))}
            </svg>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 76, fontWeight: 700, letterSpacing: -2, lineHeight: 1 }}>Homayar</div>
            <div style={{ fontSize: 30, color: "#b7c9cc" }}>{locale === "fa" ? "homayar.com" : dict.tagline}</div>
          </div>
        </div>
        <div style={{ position: "absolute", right: 72, bottom: 78, fontSize: 24, color: "#22bec6", display: "flex" }}>{locale === "fa" ? "FA" : "EN"}</div>
      </div>
    ),
    { width: W, height: H },
  );
}
