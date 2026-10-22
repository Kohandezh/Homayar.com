/**
 * Whether a global playback shortcut must leave the key for the control that
 * has focus: a text field eats typing, and a dialog (the revision history
 * drawer, a confirm) owns its keys while it is open.
 */
export function shortcutTargetBlocked(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) return true;
  return Boolean(target.closest('[role="dialog"], [role="alertdialog"]'));
}
