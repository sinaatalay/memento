import { gaussian, rng } from "../lib/rng";
import { CUE, STORY, day, storyTime } from "../timeline";

export type Memory = {
  id: string;
  created: number; // story time
  appearAt?: number; // film time it first lands (chat-born memories)
  g: number; // vertical offset, in units of the cloud's spread at its age
  r: number; // radius, px
  remindAt?: number; // story time of a time trigger
  listens: boolean; // has an event watch
  wake: number; // film time its recipe appears
  p1: number; // Jev's answer to the airline promo
  p2: number; // Jev's answer to the schedule change
  showP?: boolean;
  question?: string;
  chat?: string;
};

const r = rng(20260927);

/** The memories born on screen, from chat. */
export const FLIGHT: Memory = {
  id: "flight",
  created: STORY.saved,
  appearAt: CUE.land,
  g: 0,
  r: 6.5,
  remindAt: STORY.remind,
  listens: true,
  wake: CUE.alive,
  p1: 0.03,
  p2: 0.97,
  showP: true,
  question: "UA123 is delayed or rescheduled",
  chat: "flying to new york on monday. ua 123, 11am.",
};

const CHAT = [
  { chat: "acme needs our soc 2 report before the pilot.", g: -1.25, question: "our SOC 2 report has been issued", listens: true, p1: 0.0, p2: 0.01 },
  { chat: "priya's hiring a founding designer.", g: 1.2, question: "a product designer is looking for a role", listens: true, p1: 0.01, p2: 0.0 },
  { chat: "sam's birthday is october 3rd.", g: -0.35, remindAt: day(5, 10, 0), listens: false, p1: 0, p2: 0 },
];

export const MONTAGE: Memory[] = CHAT.map((c, i) => ({
  id: `chat${i}`,
  created: storyTime(CUE.drops[i]),
  appearAt: CUE.drops[i],
  g: c.g,
  r: 4.6,
  remindAt: c.remindAt,
  listens: c.listens,
  wake: CUE.alive + 0.08 + i * 0.05,
  p1: c.p1,
  p2: c.p2,
  showP: c.listens,
  question: c.question,
  chat: c.chat,
}));

/** Months of ordinary memories. Log-uniform in age, so they sit evenly on the log axis. */
const N = 260;
const background: Memory[] = [];
for (let i = 0; i < N; i++) {
  const age = 1.5 * Math.pow(4200 / 1.5, r()); // hours before the flight was saved
  const g = Math.max(-2.4, Math.min(2.4, gaussian(r)));
  const size = r();
  const kind = r();
  const listens = kind < 0.58;
  const hasRemind = kind > 0.45 && kind < 0.67;
  // future reminders: anywhere from Monday morning to months out
  const lead = 9 * Math.pow(1800 / 9, r());
  const ageK = Math.log(1 + age / 8) / Math.log(1 + 4200 / 8);
  background.push({
    id: `m${i}`,
    created: STORY.saved - age,
    g,
    r: 1.8 + 3.6 * size * size,
    remindAt: hasRemind ? day(2, 23, 50) + lead : undefined,
    listens,
    wake: CUE.alive + 0.15 + 1.25 * ageK + 0.12 * r(),
    p1: Math.round(Math.pow(r(), 6) * 6) / 100,
    p2: Math.round(Math.pow(r(), 7) * 3) / 100,
    showP: listens && r() < 0.16,
  });
}
// A couple of travel memories are plausible matches for the promo, and still say no.
background.filter((m) => m.listens).slice(0, 3).forEach((m, i) => {
  m.p1 = [0.06, 0.04, 0.02][i];
});

export const MEMORIES: Memory[] = [...background, ...MONTAGE, FLIGHT];
export const LISTENERS = MEMORIES.filter((m) => m.listens);
