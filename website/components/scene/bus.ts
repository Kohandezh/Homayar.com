export type SceneKind = "tunnel" | "warp" | "brain" | "field" | "particles" | "ride" | "map" | "vault" | "gallery" | "orbit" | "lift" | "beacon" | "dome";

const progress = new WeakMap<Element, number>();

export const sceneBus = {
  set(el: Element, p: number) {
    progress.set(el, p);
  },
  get(el: Element) {
    return progress.get(el) ?? 0;
  },
  pointer: { x: 0, y: 0 },
};
