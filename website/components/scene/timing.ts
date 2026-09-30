export const BRAIN_LINES = 5;
export const ENTER_END = 0.18;
export const LINE_START = 0.2;
export const LINE_GAP = 0.105;
export const REPORT_IN = 0.72;
export const REPORT_PEAK = 0.9;
export const REPORT_OUT = 1;

export function lineAt(i: number) {
  return LINE_START + i * LINE_GAP;
}

export function focusAt(p: number) {
  if (p < LINE_START || p >= REPORT_IN) return -1;
  return Math.min(BRAIN_LINES - 1, Math.floor((p - LINE_START) / LINE_GAP));
}
