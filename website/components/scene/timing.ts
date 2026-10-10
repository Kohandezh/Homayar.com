export const BRAIN_LINES = 5;
export const ENTER_END = 0.18;
export const LINE_START = 0.2;
export const LINE_GAP = 0.105;
// After the last line, the sentences clear and the camera flies through the brain (REPORT_IN to BRAIN_PASS).
// Then the report comes in from far away (until REPORT_PEAK) and stays until the section ends.
export const REPORT_IN = 0.7;
export const BRAIN_PASS = 0.78;
export const REPORT_PEAK = 0.86;
export const REPORT_OUT = 1;

export function lineAt(i: number) {
  return LINE_START + i * LINE_GAP;
}

export function focusAt(p: number) {
  if (p < LINE_START || p >= REPORT_IN) return -1;
  return Math.min(BRAIN_LINES - 1, Math.floor((p - LINE_START) / LINE_GAP));
}

// Product gallery: each painting gets an equal part of the pinned scroll. Inside that part:
// walk to it, turn and step closer, look, turn back to the hall.
export const EXHIBIT_ARRIVE = 0.25;
export const EXHIBIT_FACE = 0.42;
export const EXHIBIT_LEAVE = 0.86;
