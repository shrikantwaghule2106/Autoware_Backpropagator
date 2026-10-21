// Pure helpers for the Live dashboard charts (D31): one x scale (probe window index) shared by
// every chart, so the pointer marks the same moment in all of them.

/** SVG path of a series in a `0 0 (n-1) h` viewBox; a missing value (null) breaks the line. */
export function sparkPath(values: (number | null)[], max: number, h: number): string {
  const top = max > 0 ? max : 1;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || v === undefined) {
      pen = false;
      return;
    }
    const y = h - (Math.min(Math.max(v, 0), top) / top) * h;
    d += `${pen ? "L" : "M"}${i} ${+y.toFixed(2)}`;
    pen = true;
  });
  return d;
}

/** Top of a chart's y range: the largest value or threshold plus headroom, never 0. */
export function niceMax(values: (number | null)[], ...extra: (number | null | undefined)[]): number {
  const top = Math.max(0, ...values.filter((v): v is number => v !== null), ...extra.filter((v): v is number => !!v));
  return top > 0 ? top * 1.15 : 1;
}

/** The window index under a pointer at `x` pixels in a chart `width` pixels wide. */
export function indexAt(x: number, width: number, n: number): number {
  if (n <= 1 || width <= 0) return 0;
  return Math.min(n - 1, Math.max(0, Math.round((x / width) * (n - 1))));
}

/** Horizontal position (percent) of window `i` among `n`. */
export const xPct = (i: number, n: number) => (n <= 1 ? 0 : (i / (n - 1)) * 100);

export const clock = (t: string) => (t.length >= 19 ? t.slice(11, 19) : t);

export function fmtHz(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}

/** "15 min" for 180 windows of 5 s. */
export function rangeLabel(windows: number, windowS: number): string {
  const min = (windows * windowS) / 60;
  return min >= 60 ? `${+(min / 60).toFixed(1)} h` : `${Math.round(min)} min`;
}

/** The latest value of a series, or the one at the pointer. */
export function valueAt(values: (number | null)[], hover: number | null): number | null {
  if (hover !== null && hover < values.length) return values[hover];
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return values[i];
  return null;
}
