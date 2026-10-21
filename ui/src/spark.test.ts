import { describe, expect, it } from "vitest";
import { fmtHz, indexAt, niceMax, rangeLabel, sparkPath, valueAt, xPct } from "./spark";

describe("dashboard chart helpers (D31)", () => {
  it("draws a path in window coordinates and breaks it at missing values", () => {
    expect(sparkPath([10, 5, null, 0], 10, 20)).toBe("M0 0L1 10M3 20");
    expect(sparkPath([30], 10, 20)).toBe("M0 0"); // clamped to the top
  });

  it("scales to the largest value or threshold with headroom", () => {
    expect(niceMax([2, null, 8])).toBeCloseTo(9.2);
    expect(niceMax([2, 3], 10)).toBeCloseTo(11.5);
    expect(niceMax([null, 0])).toBe(1);
  });

  it("maps the pointer to the same window in every chart", () => {
    expect(indexAt(0, 400, 11)).toBe(0);
    expect(indexAt(200, 400, 11)).toBe(5);
    expect(indexAt(999, 400, 11)).toBe(10);
    expect(xPct(5, 11)).toBe(50);
  });

  it("formats values and ranges", () => {
    expect([fmtHz(null), fmtHz(7.123), fmtHz(30.04), fmtHz(250)]).toEqual(["—", "7.12", "30.0", "250"]);
    expect([rangeLabel(180, 5), rangeLabel(720, 5)]).toEqual(["15 min", "1 h"]);
    expect([valueAt([1, 2, null], null), valueAt([1, 2, null], 0)]).toEqual([2, 1]);
  });
});
