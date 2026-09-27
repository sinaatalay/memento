/**
 * The flight memory's page, before and after Memento, and the moment River types
 * each character of its recipe. Shared by the page on screen and the score.
 */
import { CUE } from "./timeline";

export const FRONT_TOP = ["---", "title: Flight to New York", "type: event", "tags: [travel]"];
export const RECIPE = [
  "recipe: |",
  "  from memento import at, when, notify, update, hours",
  "",
  '  flight = at("2026-09-28 11:00")',
  "",
  "  @at(flight - hours(3))",
  "  def leave_for_the_airport():",
  '      notify("Time to leave for SFO.")',
  "",
  '  @when("UA123 is delayed or rescheduled",',
  '        unless="it is only a promotion")',
  "  def flight_changed(news):",
  "      update(news)",
];
export const FRONT_END = ["---"];

/** Highlights: ranges in the recipe (line, column, length) and the note beside them. */
export type Highlight = {
  ranges: { line: number; col: number; len: number }[];
  noteLine: number;
  note: string;
  sub: string;
  at: number;
};
export const MATH: Highlight = {
  ranges: [{ line: 5, col: 6, len: 17 }],
  noteLine: 5,
  note: "= mon 08:00",
  sub: "plain math",
  at: CUE.mathHi,
};
export const JEV: Highlight = {
  ranges: [
    { line: 9, col: 8, len: 33 },
    { line: 10, col: 8, len: 31 },
  ],
  noteLine: 9,
  note: "yes or no?",
  sub: "jev asks it of every email",
  at: CUE.jevHi,
};

/** River types steadily, with a breath at each line end. */
export const TYPING = (() => {
  const rate = 1 / 36;
  const pause = 0.15;
  let t = 0;
  const lines = RECIPE.slice(1).map((l) => {
    const start = t;
    t += l.length === 0 ? 0.08 : l.length * rate + pause;
    return { start, len: l.length };
  });
  const scale = (CUE.typeEnd - CUE.typeStart) / t;
  return lines.map((l) => ({ start: CUE.typeStart + l.start * scale, perChar: rate * scale, len: l.len }));
})();

/** Film times at which each visible (non-space) recipe character lands. */
export const keystrokes = () => {
  const out: number[] = [];
  TYPING.forEach((l, i) => {
    const text = RECIPE[i + 1];
    for (let c = 0; c < l.len; c++) if (text[c] !== " ") out.push(l.start + (c + 1) * l.perChar);
  });
  return out;
};
