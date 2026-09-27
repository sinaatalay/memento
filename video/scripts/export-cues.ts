/**
 * Everything the score needs to land on the picture, from the same code that draws it.
 * bun scripts/export-cues.ts  ->  audio/cues.json
 */
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { keystrokes } from "../src/recipe";
import { CUE, DURATION, LINES, STORY, storyTime, terminator } from "../src/timeline";
import { EMAILS, SYLLABLES, fanDelay, memPos, view } from "../src/world/draw";
import { FLIGHT, LISTENERS, MEMORIES, MONTAGE } from "../src/world/memories";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const W = 1920;
const H = 1080;

// the chat is typed at 32 characters a second
const chat = FLIGHT.chat!;
const chatKeys = [...chat].flatMap((ch, i) => (ch === " " ? [] : [CUE.chatType + (i + 1) / 32]));

// midnights and quarter-days passing "now" while time flows
const ticks: { t: number; major: boolean }[] = [];
let prev = storyTime(0);
for (let t = 0.001; t < DURATION; t += 0.001) {
  const T = storyTime(t);
  const a = Math.floor(prev / 6);
  const b = Math.floor(T / 6);
  if (a !== b && Math.abs(T - prev) < 1) {
    const edge = Math.max(a, b) * 6;
    ticks.push({ t: +t.toFixed(3), major: edge % 24 === 0 });
  }
  prev = T;
}

// memories surfacing on the first line (nearest first), and waking into recipes
const surfacing = MEMORIES.filter((m) => m.appearAt === undefined)
  .map((m) => {
    const v = view(2.5);
    const p = memPos(v, m);
    const k = Math.min(1, Math.log(1 + (STORY.saved - m.created) / 8) / Math.log(1 + 4200 / 8));
    return { t: 1.0 + 1.9 * k, x: p.x / W, y: (p.y - v.ly) / H };
  })
  .sort((a, b) => a.t - b.t);

const wakes = MEMORIES.filter((m) => m.listens || m.remindAt !== undefined)
  .map((m) => {
    const v = view(m.wake);
    const p = memPos(v, m);
    return { t: m.wake, x: p.x / W, y: (p.y - v.ly) / H, hero: m === FLIGHT };
  })
  .sort((a, b) => a.t - b.t);

// each of Jev's questions reaching its memory
const fans = EMAILS.map((e) => ({
  t: e.t,
  land: e.t + 0.42,
  resolve: e.resolve,
  yes: e.yes,
  hits: LISTENERS.map((m) => {
    const hitT = e.fan + fanDelay(m) + 0.38;
    const v = view(hitT, 1);
    const p = memPos(v, m);
    return { t: +hitT.toFixed(4), x: p.x / W, y: (p.y - v.ly) / H, hero: m === FLIGHT };
  }).sort((a, b) => a.t - b.t),
}));

const sweeps = [CUE.ffStart + 0.2, CUE.dawn + 0.2].map((t) => {
  // sample the terminator to find when it enters and leaves the frame
  let enter = -1;
  let leave = -1;
  for (let s = t - 1; s < t + 3; s += 0.005) {
    const { edge } = terminator(s);
    if (enter < 0 && edge < W + 300) enter = s;
    if (leave < 0 && edge < -300) leave = s;
  }
  return { enter: +enter.toFixed(3), leave: +leave.toFixed(3) };
});

const out = {
  duration: DURATION,
  cue: CUE,
  lines: LINES,
  chatKeys,
  recipeKeys: keystrokes(),
  montage: MONTAGE.map((m) => m.appearAt),
  ticks,
  surfacing,
  wakes,
  fans,
  sweeps,
  syllables: SYLLABLES,
};
writeFileSync(path.join(root, "audio/cues.json"), JSON.stringify(out, null, 1));
console.log(
  `cues: ${chatKeys.length} chat keys, ${out.recipeKeys.length} recipe keys, ${ticks.length} ticks, ` +
    `${surfacing.length} surfacing, ${wakes.length} wakes, ${fans.map((f) => f.hits.length).join("+")} fan hits`,
);
