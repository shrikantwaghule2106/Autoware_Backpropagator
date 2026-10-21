import { beforeEach, describe, expect, it } from "vitest";
import { topView, useStore } from "./store";

describe("box stack (D25 close rules)", () => {
  beforeEach(() => useStore.setState({ boxes: [], nextKey: 1 }));

  it("✕ closes only the top box; the one below becomes active", () => {
    const s = useStore.getState();
    s.openBox("main", "n:/a", "back", "back|n:/a");
    s.openBox("box-1", "t:/b", "back", "back|t:/b");
    s.openBox("box-2", "n:/c", "fwd", "fwd|n:/c");
    expect(topView(useStore.getState())).toBe("box-3");
    useStore.getState().closeTop();
    expect(useStore.getState().boxes.map((b) => b.key)).toEqual([1, 2]);
    expect(topView(useStore.getState())).toBe("box-2");
  });

  it("− on an item closes its box and the boxes opened from inside it", () => {
    const s = useStore.getState();
    s.openBox("main", "n:/a", "back", "back|n:/a");
    s.openBox("box-1", "t:/b", "back", "back|t:/b");
    s.openBox("box-2", "n:/c", "fwd", "fwd|n:/c");
    useStore.getState().closeDownTo(2);
    expect(useStore.getState().boxes.map((b) => b.key)).toEqual([1]);
  });
});
