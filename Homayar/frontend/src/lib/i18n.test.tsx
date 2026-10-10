import { render, screen } from "@testing-library/react";
import { useLocale } from "@heroui/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "@/lib/i18n";

function LocaleProbe() {
  const { locale, direction } = useLocale();
  return <output>{`${locale} ${direction}`}</output>;
}

describe("I18nProvider locale for HeroUI and React Aria", () => {
  it("gives Persian (fa-IR, rtl) by default", () => {
    render(
      <I18nProvider>
        <LocaleProbe />
      </I18nProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("fa-IR rtl");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.documentElement).toHaveAttribute("lang", "fa");
  });

  it("gives English (en-US, ltr) when English was chosen", () => {
    window.localStorage.setItem("homayar_lang", "en");
    render(
      <I18nProvider>
        <LocaleProbe />
      </I18nProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("en-US ltr");
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
    expect(document.documentElement).toHaveAttribute("lang", "en");
  });
});
