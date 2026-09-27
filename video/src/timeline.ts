/**
 * The one clock everything reads: visuals, the presenter track, and the
 * generated score (scripts/export-cues.ts hands these numbers to audio/score.py).
 *
 * t = seconds into the film. T = story time, in hours since Fri Sep 25 2026, 00:00.
 * When the real voice recording arrives, re-time LINES and the CUEs that hang off them.
 */
import { track } from "./lib/pchip";

export const DURATION = 60;

/** Story time: d = 0 Fri, 1 Sat, 2 Sun, 3 Mon. */
export const day = (d: number, h: number, m = 0) => d * 24 + h + m / 60;

export const STORY = {
  saved: day(0, 18, 40),
  flight: day(3, 11, 0),
  flightNew: day(3, 10, 40),
  remind: day(3, 8, 0),
  remindNew: day(3, 7, 40),
};

export const CUE = {
  // 1. a memory is born
  lineDraw: 0.15,
  chatType: 0.9,
  chatReply: 2.45,
  condense: 3.05,
  land: 3.6,
  drops: [4.4, 5.0, 5.55],
  // 2. passive
  flowStart: 6.1,
  crossFlight: 11.1,
  // 3. rewind, the memory today
  rewind: 13.55,
  rewindEnd: 14.75,
  cardOpen: 14.95,
  // 4. the memory with memento
  recipeOpen: 18.1,
  typeStart: 18.55,
  typeEnd: 23.9,
  mathHi: 24.75,
  jevHi: 26.45,
  cardClose: 28.55,
  alive: 29.05,
  // 5. night
  ffStart: 29.85,
  nightFull: 31.35,
  email1: 34.1,
  fan1: 34.55,
  no: 38.75,
  email2: 39.35,
  fan2: 39.75,
  yes: 40.95,
  rewrite: 41.55,
  // 6. morning
  dawn: 42.9,
  fire: 45.0,
  notify: 45.55,
  // 7. close
  outro: 50.5,
  wordmark: 52.15,
  tagline: 53.7,
  credits: 55.9,
  fadeOut: 58.6,
};

export type Line = { id: number; text: string; at: number; dur: number };

/** The presenter's script, with estimated read times at a calm pace. */
export const LINES: Line[] = [
  { id: 1, text: "Your AI remembers everything you tell it.", at: 1.15, dur: 2.5 },
  { id: 2, text: "But memory only speaks when spoken to.", at: 6.05, dur: 2.5 },
  { id: 3, text: "It knew about Monday's flight.", at: 8.95, dur: 1.8 },
  { id: 4, text: "It just never brought it up.", at: 11.45, dur: 1.9 },
  { id: 5, text: "Here's that memory today: just a page.", at: 15.1, dur: 2.6 },
  { id: 6, text: "Memento adds a recipe to its front matter:", at: 18.05, dur: 2.6 },
  { id: 7, text: "a few lines of Python, written by River the moment it's saved.", at: 20.8, dur: 3.6 },
  { id: 8, text: "Time is plain math.", at: 24.75, dur: 1.4 },
  { id: 9, text: "Meaning is a question, for Jev.", at: 26.45, dur: 2.0 },
  { id: 10, text: "Sunday night. You're asleep.", at: 31.5, dur: 2.1 },
  { id: 11, text: "Every email is checked against every memory, in one call.", at: 33.9, dur: 3.4 },
  { id: 12, text: "An airline promo? No.", at: 37.45, dur: 1.8 },
  { id: 13, text: "Your flight moved? Yes.", at: 39.35, dur: 1.9 },
  { id: 14, text: "It rewrites itself, and reaches you right on time.", at: 43.35, dur: 3.1 },
  { id: 15, text: "Memento.", at: 52.1, dur: 0.9 },
  { id: 16, text: "Memories that know when they matter.", at: 53.6, dur: 2.3 },
];

/** Story clock. Keys are hand-placed so the Monday 11:00 marker crosses "now" on CUE.crossFlight
 * and the re-timed reminder reaches "now" exactly on CUE.fire. */
export const storyTime = track([
  [0, day(0, 18, 39)],
  [CUE.land, STORY.saved],
  [4.3, day(0, 19, 40)],
  [CUE.flowStart, day(1, 16, 40)],
  [8.2, day(2, 6, 0)],
  [CUE.crossFlight, STORY.flight],
  [12.3, day(3, 12, 30)],
  [CUE.rewind, day(3, 12, 36)],
  [CUE.rewindEnd, day(0, 18, 41)],
  [CUE.ffStart, day(0, 18, 41)],
  [CUE.nightFull, day(2, 23, 50)],
  [CUE.dawn, day(2, 23, 57)],
  [CUE.fire, STORY.remindNew],
  [DURATION, day(3, 7, 47)],
]);

/** Camera over the time axis. */
export const nowX = track([
  [0, 1330],
  [CUE.rewind, 1330],
  [CUE.rewindEnd, 960],
  [CUE.cardClose, 960],
  [CUE.nightFull, 1250],
  [CUE.dawn, 1250],
  [CUE.fire - 0.6, 1330],
  [CUE.outro, 1330],
  [CUE.wordmark, 960],
]);
export const lineY = track([
  [0, 655],
  [CUE.rewind, 655],
  [CUE.rewindEnd, 950],
  [CUE.cardClose, 950],
  [CUE.nightFull, 600],
  [CUE.dawn, 600],
  [CUE.fire - 0.6, 655],
  [CUE.outro, 655],
  [CUE.wordmark, 560],
]);
/** Pixels per log-unit of time distance. */
export const axisScale = track([
  [0, 250],
  [CUE.rewind, 250],
  [CUE.rewindEnd, 250],
  [CUE.cardClose, 250],
  [CUE.nightFull, 265],
  [CUE.outro, 265],
  [CUE.wordmark, 150],
]);

/**
 * Night arrives from the future: a soft terminator sweeps in from the right at dusk,
 * and day follows it the same way at dawn. nightAt(t, x) is 0 (paper) .. 1 (night).
 */
const DUSK: [number, number] = [CUE.ffStart + 0.15, CUE.nightFull - 0.05];
const DAWN: [number, number] = [CUE.dawn + 0.1, CUE.fire - 0.3];
const SOFT = 320;
const sweep = (t: number, [a, b]: [number, number]) => {
  const k = Math.min(1, Math.max(0, (t - a) / (b - a)));
  return k * k * (3 - 2 * k);
};
export const terminator = (t: number) => {
  const dawnSide = t > (CUE.nightFull + CUE.dawn) / 2;
  const p = sweep(t, dawnSide ? DAWN : DUSK);
  return { edge: 1920 + 420 - p * (1920 + 840), nightOnRight: !dawnSide, p, active: p > 0 && p < 1 };
};
export const nightAt = (t: number, x: number) => {
  const { edge, nightOnRight, p } = terminator(t);
  if (p <= 0) return nightOnRight ? 0 : 1;
  if (p >= 1) return nightOnRight ? 1 : 0;
  const k = Math.min(1, Math.max(0, (x - (edge - SOFT)) / (2 * SOFT)));
  const s = k * k * (3 - 2 * k);
  return nightOnRight ? s : 1 - s;
};
export const nightness = (t: number) => nightAt(t, 960);
export const TERMINATOR_SOFT = SOFT;

/** Where the presenter stands (left third) and whether they're on screen. */
export const presenterOn = track([
  [0, 1],
  [CUE.rewind - 0.2, 1],
  [CUE.rewind + 0.5, 0],
  [CUE.fire - 0.55, 0],
  [CUE.fire + 0.25, 1],
  [CUE.outro - 0.2, 1],
  [CUE.outro + 0.5, 0],
]);

export const lineAt = (t: number) => LINES.find((l) => t >= l.at - 0.05 && t <= l.at + l.dur + 0.35);

/** Inverse of the story clock over a window (bisection; storyTime is monotone within windows we ask about). */
export const whenStory = (T: number, a: number, b: number) => {
  let lo = a;
  let hi = b;
  const rising = storyTime(b) >= storyTime(a);
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    const v = storyTime(mid);
    if (v < T === rising) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
};

const DAYS = ["fri", "sat", "sun", "mon", "tue", "wed", "thu"];
export const clockLabel = (T: number) => {
  const d = Math.floor(T / 24);
  const mins = Math.floor((T - d * 24) * 60 + 1e-6);
  const hh = String(Math.floor(mins / 60)).padStart(2, "0");
  const mm = String(mins % 60).padStart(2, "0");
  return `${DAYS[((d % 7) + 7) % 7]} ${hh}:${mm}`;
};
export const dayName = (d: number) => DAYS[((d % 7) + 7) % 7];
