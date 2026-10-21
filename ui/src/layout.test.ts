import { describe, expect, it } from "vitest";
import { CARD_H, CARD_W, layout, plusFor, rowsThatFit, shortLabel } from "./layout";
import type { Element } from "./types";

const overlaps = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  a.x < b.x + CARD_W && b.x < a.x + CARD_W && a.y < b.y + CARD_H && b.y < a.y + CARD_H;

describe("layout", () => {
  it("never overlaps, for any level sizes", () => {
    for (const [nBack, nFwd, height] of [[1, 0, 600], [20, 9, 500], [60, 3, 300], [7, 40, 900]]) {
      const items = [{ id: "root", level: 0 }];
      for (let i = 0; i < nBack; i++) items.push({ id: `b${i}`, level: -1 });
      for (let i = 0; i < nFwd; i++) items.push({ id: `f${i}`, level: 1 });
      const pos = [...layout(items, () => ["root"], height).values()];
      for (let i = 0; i < pos.length; i++) for (let j = i + 1; j < pos.length; j++) {
        expect(overlaps(pos[i], pos[j])).toBe(false);
      }
    }
  });

  it("puts inputs left and consumers right of the root, busy levels in sub-columns", () => {
    const items = [{ id: "root", level: 0 }, { id: "in", level: -1 }, { id: "out", level: 1 }];
    const pos = layout(items, () => [], 600);
    expect(pos.get("in")!.x).toBeLessThan(pos.get("root")!.x);
    expect(pos.get("out")!.x).toBeGreaterThan(pos.get("root")!.x);
    const many = [{ id: "root", level: 0 }, ...Array.from({ length: 30 }, (_, i) => ({ id: `b${i}`, level: -1 }))];
    const xs = new Set([...layout(many, () => [], 500).values()].map((p) => p.x));
    expect(xs.size).toBeGreaterThan(2); // root column + at least two sub-columns
    expect(rowsThatFit(500)).toBeGreaterThanOrEqual(4);
  });
});

describe("plusFor", () => {
  const el = (inputs: number, consumers: number): Element =>
    ({ id: "t:/x", label: "/x", kind: "topic", layer: "sensing", inputs, consumers });
  it("offers +n only where something opens (issue 2)", () => {
    expect(plusFor(el(3, 0), -1, false, false, "back")).toEqual({ dir: "back", count: 3 });
    expect(plusFor(el(0, 2), -1, false, false, "back")).toBeNull();
    expect(plusFor(el(0, 2), 1, false, true, "back")).toEqual({ dir: "fwd", count: 2 });
    expect(plusFor(el(1, 5), 0, true, false, "back")).toEqual({ dir: "fwd", count: 5 });
    expect(plusFor(el(1, 5), 0, true, true, "back")).toBeNull(); // a box root shows its level already
  });
});

describe("shortLabel", () => {
  it("keeps a node's namespace and a topic's last segments", () => {
    expect(shortLabel("n:/sensing/lidar/top/distortion_corrector_node")).toBe("top/distortion_corrector_node");
    expect(shortLabel("t:/sensing/lidar/concatenated/pointcloud")).toBe("/lidar/concatenated/pointcloud");
  });
});
