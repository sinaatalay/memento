/**
 * The memory page, before and after Memento, and the moment River types each character of its
 * recipe. Shared by the page on screen (film/Page.tsx) and the score (scripts/export-cues.ts).
 */
import { CUE } from "../story";

export const PATH = "gbrain · projects/monorepo-deploy.md";

export const FRONT_TOP = ["---", "type: project", "title: Monorepo deploy"];
/** The recipe, exactly as the product writes it. */
export const RECIPE = [
  "recipe: |",
  "  from memento import at, fetch, notify, update, hours",
  "",
  '  @at("2026-09-28 09:00", every=hours(6))',
  "  def check_upstream():",
  '      issue = fetch("https://api.github.com/repos/vercel/next.js/issues/89207")',
  '      if issue.says("#89207 is fixed: closed as completed or a fix was released",',
  '                    unless="still open, closed as not planned, or a different issue"):',
  '          notify("Next.js fixed #89207. Your monorepo deploy is unblocked.")',
  "          update(issue)   # River retires the check",
];
export const FRONT_END = ["---"];

/** The page body (Markdown, rendered in serif). Line 1 is the one the agent reads and L3 lights. */
export const BODY_TITLE = "Monorepo deploy";
export const BODY = [
  "Deploys fail on Turbopack: it can't resolve workspace packages.",
  "Blocked on vercel/next.js#89207. We ship the migration once it's fixed upstream.",
];
/** After update(issue): what River writes instead of the blocked line. */
export const BODY_AFTER = "Unblocked: #89207 fixed in Next.js 16.1.3 (Wed, Sep 30). Ship the migration.";

/** Highlights on recipe lines: [line, column, length]. */
export type Range = { line: number; col: number; len: number };
export const HI_EVERY: Range[] = [
  { line: 3, col: 2, len: 39 },
  { line: 5, col: 14, len: 65 },
];
export const HI_SAYS: Range[] = [
  { line: 6, col: 9, len: 72 },
  { line: 7, col: 20, len: 64 },
];
export const HI_FIRE: Range[] = [
  { line: 8, col: 10, len: 66 },
  { line: 9, col: 10, len: 13 },
];

/** River types steadily, with a breath at each line end. */
export const TYPING = (() => {
  const rate = 1 / 40;
  const pause = 0.14;
  let t = 0;
  const lines = RECIPE.slice(1).map((l) => {
    const start = t;
    t += l.length === 0 ? 0.08 : l.trimStart().length * rate + pause;
    return { start, len: l.length, indent: l.length - l.trimStart().length };
  });
  const scale = (CUE.typeEnd - CUE.typeStart) / t;
  return lines.map((l) => ({ start: CUE.typeStart + l.start * scale, perChar: rate * scale, len: l.len, indent: l.indent }));
})();

/** How many characters of recipe line i are visible at film time t (indentation appears at once). */
export const revealed = (i: number, t: number) => {
  if (i === 0) return RECIPE[0].length;
  const s = TYPING[i - 1];
  if (t < s.start) return 0;
  return Math.min(s.len, s.indent + Math.floor((t - s.start) / s.perChar));
};

/** Film times at which each visible (non-space) recipe character lands. */
export const keystrokes = () => {
  const out: number[] = [];
  TYPING.forEach((l, i) => {
    const text = RECIPE[i + 1];
    for (let c = l.indent; c < l.len; c++) if (text[c] !== " ") out.push(l.start + (c - l.indent + 1) * l.perChar);
  });
  return out;
};

/** The check log: one fetch every six hours, each answered by Jev. */
export const CHECKS = [
  { when: "mon 09:00", state: "open", detail: "214 comments", p: 0.01 },
  { when: "mon 15:00", state: "open", detail: "+3 comments", p: 0.01 },
  { when: "tue 03:00", state: "open", detail: "fix in review: PR #89311", p: 0.04 },
  { when: "wed 21:00", state: "closed as completed", detail: "fixed in v16.1.3", p: 0.97 },
];
