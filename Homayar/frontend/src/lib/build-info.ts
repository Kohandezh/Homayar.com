/**
 * The console build identity (HOM-232 V1/V2): the version to show in the
 * admin sidebar footer and the short SHA of the source it was built from.
 * Both values come from the `__CONSOLE_BUILD__` constant that Vite `define`
 * injects at build time, so the shipped bundle reads nothing at runtime.
 */
export interface ConsoleBuild {
  version: string;
  /** Seven hex characters, or null when the build carries no source SHA. */
  sha: string | null;
}

export const CONSOLE_BUILD: ConsoleBuild = __CONSOLE_BUILD__;
