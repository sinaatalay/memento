/**
 * The one clock for the film. Every scene reads it, and scripts/export-cues.ts hands it to the score.
 * When the recorded voice arrives, re-time LINES and the CUEs that hang off them.
 *
 * The film: a memory page says our deploy is blocked on vercel/next.js#89207. Memento gives it a
 * recipe that fetches the issue every six hours and asks Jev whether it's really fixed. It is, one
 * night; you get told; River updates the page and retires the check.
 */
import { track } from "./lib/pchip";

export const DURATION = 59;

export type Line = { id: number; text: string; at: number; dur: number };

/** The presenter's script (SCRIPT.md), with estimated read times at a calm pace. */
export const LINES: Line[] = [
  { id: 1, text: "This is a memory: a Markdown page in GBrain.", at: 0.8, dur: 3.0 },
  { id: 2, text: "When you chat, your agent reads it. That's how it remembers.", at: 4.1, dur: 3.8 },
  { id: 3, text: "This one says our deploy is blocked by a Next.js bug.", at: 8.2, dur: 3.4 },
  { id: 4, text: "But memory only speaks when you ask, and nobody checks a GitHub issue every six hours.", at: 12.0, dur: 5.2 },
  { id: 5, text: "So Memento gives every memory a recipe.", at: 17.7, dur: 2.5 },
  { id: 6, text: "A few lines of Python in the front matter, written by River the moment the memory is saved.", at: 20.4, dur: 5.2 },
  { id: 7, text: "Every six hours, it fetches the issue.", at: 26.0, dur: 2.4 },
  { id: 8, text: "Is it really fixed? That's not a regex, it's a judgment. Jev makes it in a hundred milliseconds.", at: 28.8, dur: 5.6 },
  { id: 9, text: "Still open, fix in review? No.", at: 35.0, dur: 2.2 },
  { id: 10, text: "Closed, and the fix is released? Yes.", at: 37.6, dur: 2.4 },
  { id: 11, text: "Then River updates the memory and retires the check.", at: 42.6, dur: 3.0 },
  { id: 12, text: "Memory that doesn't just remember.", at: 46.0, dur: 1.9 },
  { id: 13, text: "It watches, it judges, it tells you.", at: 48.1, dur: 2.4 },
  { id: 14, text: "Memento. Proactive memory for GBrain.", at: 51.2, dur: 2.6 },
];

export const CUE = {
  // 1. this is a memory (page alone, then a chat that reads it)
  pageIn: 0.25,
  chatIn: 4.0,
  ask: 4.3, // the user's question types in
  sent: 5.2,
  read: 5.45, // a hairline from the page's "blocked" line to the chat: the agent reads the page
  answer: 6.3,
  // 2. the blocked line
  blockedHi: 8.6,
  // 3. nobody checks
  chatOut: 11.7,
  issueIn: 12.3, // the GitHub issue card arrives on the right
  daysRoll: 13.6, // sep 28 -> oct 3 rolls by; "last checked: never"
  daysRollEnd: 16.2,
  // 4. the recipe
  issueOut: 17.3,
  pageCenter: 17.45,
  recipeOpen: 18.2,
  typeStart: 18.7,
  typeEnd: 25.5,
  everyHi: 26.0, // @at(..., every=hours(6)) and fetch(...) highlighted
  saysHi: 29.3, // issue.says(...) highlighted, "jev · yes / no · ~100 ms"
  // 5. the checks
  toChecks: 33.6, // page slides left and shrinks; the check log opens on the right
  checks: [34.1, 34.55, 35.1, 37.7], // rows land: mon 09:00, mon 15:00, tue 03:00 (fix in review), wed 21:00 (fixed)
  verdicts: [34.35, 34.8, 35.9, 38.5], // Jev's number settles for each row
  fire: 39.3, // the yes: the says() line and notify() light up
  notify: 40.2, // the banner arrives
  // 6. update and retire
  update: 42.7, // River rewrites the page body
  retire: 44.0, // the recipe folds away: "check retired"
  // 7. watches, judges, tells you
  verbs: 45.9,
  verbWords: [48.1, 48.85, 49.6], // "watches", "judges", "tells you"
  verbsOut: 50.6,
  // 8. the name
  wordmark: 51.2,
  syllables: [51.2, 51.52, 51.84],
  tagline: 52.4,
  credits: 54.4,
  fadeOut: 57.3,
};

/** The memory page's place on screen: centre x, top y, scale, brightness, opacity. */
export const PAGE = {
  cx: track([
    [0, 640],
    [CUE.pageCenter, 640],
    [CUE.pageCenter + 0.9, 960],
    [CUE.toChecks, 960],
    [CUE.toChecks + 0.8, 560],
    [CUE.update - 0.6, 560],
    [CUE.update, 960],
  ]),
  top: track([
    [0, 300],
    [CUE.pageCenter, 300],
    [CUE.pageCenter + 0.9, 150],
    [CUE.toChecks, 150],
    [CUE.toChecks + 0.8, 190],
    [CUE.update - 0.6, 190],
    [CUE.update, 170],
  ]),
  scale: track([
    [0, 1],
    [CUE.toChecks, 1],
    [CUE.toChecks + 0.8, 0.74],
    [CUE.update - 0.6, 0.74],
    [CUE.update, 0.92],
  ]),
  light: track([
    [0, 1],
    [CUE.chatOut, 1],
    [CUE.chatOut + 0.8, 0.45],
    [CUE.pageCenter, 0.45],
    [CUE.pageCenter + 0.7, 1],
  ]),
  alpha: track([
    [0, 1],
    [CUE.verbs - 0.4, 1],
    [CUE.verbs + 0.2, 0],
  ]),
};

/** The presenter sits bottom-left through the film and steps out for the name. */
export const presenterOn = track([
  [0, 1],
  [CUE.verbsOut, 1],
  [CUE.verbsOut + 0.6, 0],
]);

export const lineAt = (t: number) => LINES.find((l) => t >= l.at - 0.05 && t <= l.at + l.dur + 0.35);
