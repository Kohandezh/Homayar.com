import Link from "next/link";
import Image from "next/image";

/**
 * Homayar mark — the brand icon (speech bubble + waveform + network), served
 * from /homayar-icon.png with its white background stripped to transparency so
 * it sits cleanly on the dark UI.
 */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <Image
      src="/homayar-icon.png"
      alt="Homayar"
      width={size}
      height={size}
      className="object-contain"
    />
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <Link
      href="/"
      className="flex items-center gap-2.5 shrink-0"
      aria-label="Homayar — home"
    >
      <LogoMark size={size} />
      <span className="font-display text-lg font-semibold tracking-tight text-ink">
        Homayar
      </span>
    </Link>
  );
}

/**
 * Full brand lockup (icon + "Homayar" wordmark + tagline) from
 * /homayar-logo.png. The source artwork has a dark navy wordmark, so on the dark
 * UI it's rendered in white (brightness-0 invert) to stay legible.
 */
export function FullLogo({ width = 190 }: { width?: number }) {
  return (
    <Link href="/" className="inline-block shrink-0" aria-label="Homayar — home">
      <Image
        src="/homayar-logo.png"
        alt="Homayar"
        width={width}
        height={Math.round((width * 443) / 1448)}
        className="h-auto object-contain brightness-0 invert"
        style={{ width }}
      />
    </Link>
  );
}
