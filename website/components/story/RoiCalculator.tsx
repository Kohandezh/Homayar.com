"use client";

import { useId, useState } from "react";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { compute, exampleText, fieldKeys, formatTenths, formatText, limits, MINUTES_PER_HOUR, parseField, tenthsText, type FieldKey, type Parsed } from "./roi-math";

type Copy = Dictionary["roi"];

const DASH = "—";

const symbols: Record<FieldKey, string> = { agents: "N", calls: "K", minutesToday: "M1", minutesHomayar: "M2", rate: "W" };

// The review time calculator: five fields, the result, and every formula with the visitor's numbers put in.
// The server render already shows the worked example, so the page reads fully without JavaScript.
// The results also go on the root as data attributes; the "stacks" scene reads them to size its two columns.
export function RoiCalculator({ copy, locale }: { copy: Copy; locale: Locale }) {
  const uid = useId();
  const [values, setValues] = useState<Record<FieldKey, string>>(() => {
    const out = {} as Record<FieldKey, string>;
    for (const k of fieldKeys) out[k] = exampleText(k, locale);
    return out;
  });

  const parsed = {} as Record<FieldKey, Parsed | null>;
  for (const k of fieldKeys) parsed[k] = parseField(values[k], k);
  const r = compute(parsed);
  const allValid = fieldKeys.every((k) => parsed[k] !== null);

  // Results are whole numbers of tenths (bigint), so they print exactly at any size.
  const num = (v: bigint | null) => (v === null ? DASH : formatTenths(v, locale));
  const input = (k: FieldKey) => (parsed[k] ? formatText(parsed[k].text, locale) : DASH);
  const sixty = formatText(String(MINUTES_PER_HOUR), locale);
  const errorFor = (k: FieldKey) => (limits[k].whole ? copy.errors.whole : k === "rate" ? copy.errors.rate : copy.errors.minutes);

  // S never goes below 0. When H1 < H2 the line says so instead of showing a negative number.
  const below = r.H1 !== null && r.H2 !== null && r.H1 < r.H2;
  const lines: Array<{ key: "T" | "H1" | "H2" | "S" | "C"; formula: string; filled: string }> = [
    { key: "T", formula: "T = N × K", filled: `${input("agents")} × ${input("calls")} = ${num(r.T)}` },
    { key: "H1", formula: `H1 = T × M1 ÷ ${sixty}`, filled: `${num(r.T)} × ${input("minutesToday")} ÷ ${sixty} = ${num(r.H1)}` },
    { key: "H2", formula: `H2 = T × M2 ÷ ${sixty}`, filled: `${num(r.T)} × ${input("minutesHomayar")} ÷ ${sixty} = ${num(r.H2)}` },
    { key: "S", formula: "S = H1 − H2", filled: below ? `${num(r.H1)} − ${num(r.H2)} < ${formatText("0", locale)} → ${num(r.S)}` : `${num(r.H1)} − ${num(r.H2)} = ${num(r.S)}` },
    { key: "C", formula: "C = S × W", filled: `${num(r.S)} × ${input("rate")} = ${num(r.C)}` },
  ];

  // The dot glyph: one row per way of working. The longer row is the full width.
  const top = Math.max(Number(r.H1 ?? 0), Number(r.H2 ?? 0));
  const pct = (v: bigint | null) => (v === null || top <= 0 ? 0 : (Number(v) / top) * 100);
  const gapStart = pct(r.H2);
  const gapWidth = r.S !== null && r.S > BigInt(0) ? Math.max(0, pct(r.H1) - pct(r.H2)) : 0;

  return (
    <div data-roi data-h1={r.H1 === null ? "" : tenthsText(r.H1)} data-h2={r.H2 === null ? "" : tenthsText(r.H2)} data-testid="roi-calculator">
      <p className="border-s-2 border-accent ps-4 text-sm text-ink-2" data-testid="roi-sample">
        {copy.sample}
      </p>

      <div className="mt-8 grid gap-x-6 gap-y-6 sm:grid-cols-2">
        {fieldKeys.map((k) => {
          const f = copy.fields[k];
          const invalid = parsed[k] === null;
          const id = `${uid}-${k}`;
          const describedBy = [f.hint ? `${id}-hint` : "", invalid ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
          return (
            <div key={k} className={k === "rate" ? "sm:col-span-2" : undefined}>
              <label htmlFor={id} className="flex items-baseline gap-2 text-sm font-semibold">
                <bdi dir="ltr" className="roi-symbol">{symbols[k]}</bdi>
                <span>{f.label}</span>
              </label>
              <div className="mt-2 flex items-center gap-3">
                <input
                  id={id}
                  name={k}
                  type="text"
                  inputMode={limits[k].whole ? "numeric" : "decimal"}
                  autoComplete="off"
                  spellCheck={false}
                  className="field-input roi-input"
                  value={values[k]}
                  onChange={(e) => {
                    const next = e.target.value;
                    setValues((v) => ({ ...v, [k]: next }));
                  }}
                  aria-invalid={invalid || undefined}
                  aria-describedby={describedBy}
                  data-testid={`roi-input-${k}`}
                />
                <span className="w-20 shrink-0 text-sm text-ink-3">{f.unit}</span>
              </div>
              {f.hint ? (
                <p id={`${id}-hint`} className="mt-1.5 text-sm text-ink-3">
                  {f.hint}
                </p>
              ) : null}
              {invalid ? (
                <p id={`${id}-error`} className="mt-1.5 text-sm text-danger" data-testid={`roi-error-${k}`}>
                  {errorFor(k)}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      <section className="mt-14 border-t border-accent pt-8" aria-labelledby={`${uid}-result`} data-testid="roi-result">
        <h2 id={`${uid}-result`} className="label text-ink-3">
          {copy.result.title}
        </h2>
        <p className="mt-3 flex flex-wrap items-baseline gap-x-3">
          <span className="roi-big" aria-live="polite" data-result="S" data-testid="roi-S">
            {num(r.S)}
          </span>
          <span className="text-xl font-semibold">{copy.result.hoursUnit}</span>
        </p>
        <p className="mt-1 text-lg text-ink-2">{copy.formulas.items.S}</p>

        <div className="roi-glyph mt-8" aria-hidden="true">
          <div className="roi-glyph-row">
            <span className="roi-glyph-label">{copy.result.today}</span>
            <span className="dot-track roi-track">
              <span className="dot-fill roi-fill" style={{ width: `${pct(r.H1)}%` }} />
            </span>
            <span className="roi-glyph-value">{num(r.H1)}</span>
          </div>
          <div className="roi-glyph-row">
            <span className="roi-glyph-label">{copy.result.withHomayar}</span>
            <span className="dot-track roi-track">
              <span className="dot-fill roi-fill" style={{ width: `${pct(r.H2)}%` }} />
              <span className="roi-gap" style={{ insetInlineStart: `${gapStart}%`, width: `${gapWidth}%` }} />
            </span>
            <span className="roi-glyph-value">{num(r.H2)}</span>
          </div>
          <p className="roi-glyph-legend">
            <span className="roi-gap-key" />
            {copy.result.gap}: {num(r.S)} {copy.result.hoursUnit}
          </p>
        </div>

        <p className="mt-8 flex flex-wrap items-baseline gap-x-3 border-t border-border-2 pt-5">
          <span className="text-ink-2">{copy.formulas.items.C}</span>
          <span className="roi-cost text-2xl font-bold" data-result="C" data-testid="roi-C">
            {num(r.C)}
          </span>
          <span className="text-sm text-ink-3">{copy.result.costUnit}</span>
        </p>

        {!allValid ? (
          <p className="mt-5 text-sm text-danger" data-testid="roi-invalid">
            {copy.result.invalid}
          </p>
        ) : r.S === BigInt(0) ? (
          <p className="mt-5 font-semibold text-accent-text" data-testid="roi-none">
            {copy.result.none}
          </p>
        ) : null}
      </section>

      <section className="mt-16" aria-labelledby={`${uid}-formulas`} data-testid="roi-formulas">
        <h2 id={`${uid}-formulas`} className="display-2">
          {copy.formulas.title}
        </h2>
        <p className="mt-4 text-lg text-ink-2">{copy.formulas.text}</p>
        <ol className="mt-8 divide-y divide-border-2 border-y border-border-2">
          {lines.map((l) => (
            <li key={l.key} className="roi-line" data-testid={`roi-line-${l.key}`}>
              <span className={`roi-line-label ${l.key === "S" ? "font-semibold text-ink" : "text-ink-2"}`}>{copy.formulas.items[l.key]}</span>
              <bdi dir="ltr" className="roi-formula">{l.formula}</bdi>
              <bdi dir="ltr" className={`roi-filled ${l.key === "S" ? "text-accent-text" : ""}`}>
                {l.filled}
              </bdi>
            </li>
          ))}
        </ol>
        <p className="mt-5 text-sm text-ink-3">{copy.formulas.minutes}</p>
        <p className="mt-1 text-sm text-ink-3">{copy.formulas.rounding}</p>
      </section>
    </div>
  );
}
