import { clamp, ease, lerp, ramp, springAt, window } from "../lib/math";
import { ACCENT, DISPLAY, INK, MONO, NIGHT_INK, SERIF, W } from "../theme";
import {
  CUE,
  STORY,
  axisScale,
  clockLabel,
  dayName,
  lineY,
  nightness,
  nowX,
  storyTime,
} from "../timeline";
import { FLIGHT, LISTENERS, MEMORIES, MONTAGE, type Memory } from "./memories";

type C = CanvasRenderingContext2D;

const TAU = 8; // hours; below this the axis is ~linear, above it logarithmic

type View = {
  t: number;
  T: number;
  nx: number;
  ly: number;
  S: number;
  night: number;
  ink: (a: number) => string;
  accent: (a: number) => string;
};

const hexRGB = (h: string) => {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const INK_RGB = hexRGB(INK);
const NIGHT_RGB = hexRGB(NIGHT_INK);
const ACC_RGB = hexRGB(ACCENT);

export const view = (t: number): View => {
  const night = nightness(t);
  const c = INK_RGB.map((v, i) => Math.round(lerp(v, NIGHT_RGB[i], night)));
  return {
    t,
    T: storyTime(t),
    nx: nowX(t),
    ly: lineY(t),
    S: axisScale(t),
    night,
    ink: (a) => `rgba(${c[0]},${c[1]},${c[2]},${clamp(a)})`,
    accent: (a) => `rgba(${ACC_RGB[0]},${ACC_RGB[1]},${ACC_RGB[2]},${clamp(a)})`,
  };
};

export const X = (v: View, Tp: number) => {
  const d = Tp - v.T;
  return v.nx + Math.sign(d) * v.S * Math.log(1 + Math.abs(d) / TAU);
};
const spread = (age: number) => 5 + 24 * Math.log(1 + Math.max(0, age) / 10);
export const memPos = (v: View, m: Memory) => ({
  x: X(v, m.created),
  y: v.ly + m.g * spread(v.T - m.created),
});
const ageK = (age: number) => clamp(Math.log(1 + Math.max(0, age) / 8) / Math.log(1 + 4200 / 8));

/* ---------- film-level envelopes ---------- */

const cardFocus = (t: number) => window(t, CUE.cardOpen - 0.3, CUE.cardClose + 0.5, 0.5, 0.7);
const worldFade = (t: number) => 1 - ramp(t, CUE.outro, CUE.outro + 1.3, ease.inOut);
const flightRemindAt = (t: number) =>
  lerp(STORY.remind, STORY.remindNew, ramp(t, CUE.rewrite + 0.1, CUE.rewrite + 0.9, ease.inOut));
const flightDepartAt = (t: number) =>
  lerp(STORY.flight, STORY.flightNew, ramp(t, CUE.rewrite + 0.1, CUE.rewrite + 0.9, ease.inOut));

/* ---------- pieces ---------- */

const font = (px: number, family: string, style = "", weight = 400) =>
  `${style} ${weight} ${px}px '${family}'`.trim();

const drawAxis = (ctx: C, v: View) => {
  const p = ramp(v.t, CUE.lineDraw, CUE.lineDraw + 1.2, ease.inOut);
  const left = v.nx - p * (v.nx + 60);
  const right = v.nx + p * (W - v.nx + 60);
  const g = ctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, v.ink(0));
  g.addColorStop(0.12, v.ink(0.42));
  g.addColorStop(0.9, v.ink(0.42));
  g.addColorStop(1, v.ink(0));
  ctx.strokeStyle = g;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(left, v.ly + 0.5);
  ctx.lineTo(right, v.ly + 0.5);
  ctx.stroke();

  // day ticks
  const labels = ramp(v.t, 0.9, 1.8);
  const d0 = Math.floor((v.T - 4400) / 24);
  const d1 = Math.ceil((v.T + 2400) / 24);
  let lastX = -1e9;
  let lastLabelX = -1e9;
  ctx.font = font(14, MONO);
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  for (let d = d0; d <= d1; d++) {
    const x = X(v, d * 24);
    if (x < -40 || x > W + 40) continue;
    if (Math.abs(x - lastX) < 9) continue;
    if (x < left || x > right) continue;
    const dist = Math.abs(d * 24 - v.T);
    const edge = clamp(Math.min(x, W - x) / 160);
    const tickA = 1 - ease.smooth(clamp((dist - 200) / 500));
    if (tickA <= 0) continue;
    ctx.strokeStyle = v.ink(0.3 * edge * labels * tickA);
    ctx.beginPath();
    ctx.moveTo(x + 0.5, v.ly + 4);
    ctx.lineTo(x + 0.5, v.ly + 11);
    ctx.stroke();
    lastX = x;
    const labelA = 1 - ease.smooth(clamp((dist - 90) / 80));
    if (labelA > 0 && Math.abs(x - lastLabelX) > 74) {
      const date = new Date(Date.UTC(2026, 8, 25 + d));
      ctx.fillStyle = v.ink(0.42 * edge * labels * labelA);
      ctx.fillText(`${dayName(d)} ${date.getUTCDate()}`, x, v.ly + 34);
      lastLabelX = x;
    }
  }
  // quarter-day ticks near now
  for (let h = Math.floor((v.T - 72) / 6) * 6; h < v.T + 72; h += 6) {
    if (h % 24 === 0) continue;
    const x = X(v, h);
    const xn = X(v, h + 6);
    if (Math.abs(xn - x) < 16 || x < left || x > right) continue;
    ctx.strokeStyle = v.ink(0.16 * labels);
    ctx.beginPath();
    ctx.moveTo(x + 0.5, v.ly + 4);
    ctx.lineTo(x + 0.5, v.ly + 8);
    ctx.stroke();
  }
};

const drawNow = (ctx: C, v: View) => {
  const a = ramp(v.t, 0.5, 1.4) * worldFade(v.t);
  if (a <= 0) return;
  ctx.strokeStyle = v.ink(0.85 * a);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(v.nx + 0.5, v.ly - 30);
  ctx.lineTo(v.nx + 0.5, v.ly + 16);
  ctx.stroke();
  ctx.textAlign = "center";
  ctx.font = font(13, MONO, "", 500);
  ctx.fillStyle = v.accent(0.95 * a);
  ctx.fillText("now", v.nx, v.ly - 62);
  ctx.font = font(16, MONO);
  ctx.fillStyle = v.ink(0.8 * a);
  ctx.fillText(clockLabel(v.T), v.nx, v.ly - 40);
};

/** Point on the quadratic arc from a memory to a moment on the line. */
const arcPoint = (x0: number, y0: number, x1: number, y1: number, k: number) => {
  const span = Math.abs(x1 - x0);
  const cx = (x0 + x1) / 2;
  const cy = Math.min(y0, y1) - Math.max(26, span * 0.3);
  const u = 1 - k;
  return {
    x: u * u * x0 + 2 * u * k * cx + k * k * x1,
    y: u * u * y0 + 2 * u * k * cy + k * k * y1,
  };
};

const strokeArc = (
  ctx: C,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  from: number,
  to: number,
) => {
  const steps = 48;
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const k = lerp(from, to, i / steps);
    const p = arcPoint(x0, y0, x1, y1, k);
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  }
  ctx.stroke();
};

const isVisible = (v: View, m: Memory) => {
  if (m.appearAt !== undefined && v.t < m.appearAt) return false;
  return v.T >= m.created - 1e-6;
};

/** Recipes: arcs to future moments, halos that listen. */
const drawRecipes = (ctx: C, v: View) => {
  const fade = worldFade(v.t);
  for (const m of MEMORIES) {
    if (!isVisible(v, m)) continue;
    const woke = ramp(v.t, m.wake, m.wake + 0.7, ease.out);
    if (woke <= 0) continue;
    const { x, y } = memPos(v, m);
    if (x < -300 || x > W + 300) continue;
    const hero = m === FLIGHT;
    const dim = hero ? 1 : 1 - 0.8 * cardFocus(v.t);
    if (m.listens && (x > -20 && x < W + 20)) {
      const ph = (m.created * 0.37) % 1;
      const rr = m.r + 5 + 1.6 * Math.sin(2 * Math.PI * (v.t * 0.42 + ph));
      ctx.strokeStyle = v.ink((hero ? 0.5 : 0.2) * woke * dim * fade);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x, y, rr, 0, Math.PI * 2);
      ctx.stroke();
    }
    const at = hero ? flightRemindAt(v.t) : m.remindAt;
    if (at === undefined) continue;
    if (hero && v.t >= CUE.fire) continue; // delivered
    if (at <= v.T) continue;
    const x1 = X(v, at);
    ctx.lineWidth = hero ? 1.25 : 1;
    ctx.strokeStyle = hero
      ? v.ink(0.8 * woke * fade)
      : v.ink((0.075 + 0.05 * v.night) * woke * dim * fade);
    strokeArc(ctx, x, y, x1, v.ly, 0, woke);
    if (woke > 0.98 && x1 < W + 10) {
      ctx.fillStyle = hero ? v.ink(0.9 * fade) : v.ink(0.22 * dim * fade);
      ctx.beginPath();
      ctx.arc(x1, v.ly, hero ? 3 : 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
  }
};

const drawMemories = (ctx: C, v: View) => {
  const fade = worldFade(v.t);
  const dt = 1 / 60;
  const vPrev = view(v.t - dt);
  for (const m of MEMORIES) {
    if (!isVisible(v, m)) continue;
    const { x, y } = memPos(v, m);
    if (x < -30 || x > W + 30) continue;
    const age = v.T - m.created;
    const k = ageK(age);
    const hero = m === FLIGHT;
    // background memories surface during the first line, nearest first
    const intro = m.appearAt !== undefined ? 1 : ramp(v.t, 1.0 + 1.9 * k, 1.5 + 1.9 * k, ease.out);
    const pop = m.appearAt !== undefined ? springAt(v.t, m.appearAt, 320, 16) : 1;
    const dim = hero ? 1 : 1 - 0.8 * cardFocus(v.t);
    const alpha = lerp(0.95, 0.34, k) * intro * dim * fade;
    if (alpha <= 0.002) continue;
    const r = m.r * Math.max(0, pop);
    const p0 = memPos(vPrev, m);
    const dx = x - p0.x;
    const dy = y - p0.y;
    const smear = Math.hypot(dx, dy);
    ctx.fillStyle = v.ink(alpha);
    if (smear > 1.5) {
      ctx.strokeStyle = v.ink(alpha * clamp(3 / smear + 0.35));
      ctx.lineCap = "round";
      ctx.lineWidth = r * 2;
      ctx.beginPath();
      ctx.moveTo(x - dx, y - dy);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.lineCap = "butt";
    } else {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
};

/* ---------- act 1: talking ---------- */

const CHAT_X = 700;
const CHAT_Y = 470;

const charOffsets = (ctx: C, text: string) => {
  const out: number[] = [];
  for (let i = 0; i < text.length; i++) out.push(ctx.measureText(text.slice(0, i)).width);
  return out;
};

const drawChat = (ctx: C, v: View) => {
  const t = v.t;
  if (t > CUE.land + 0.2) return;
  const text = FLIGHT.chat!;
  const typed = clamp((t - CUE.chatType) * 32, 0, text.length);
  if (typed <= 0 && t < CUE.chatType) {
    // the "you" label arrives first
  }
  const labelA = ramp(t, CUE.chatType - 0.35, CUE.chatType) * (1 - ramp(t, CUE.condense - 0.1, CUE.condense + 0.2));
  ctx.textAlign = "left";
  ctx.font = font(14, MONO, "", 500);
  ctx.fillStyle = v.ink(0.42 * labelA);
  ctx.fillText("you", CHAT_X, CHAT_Y - 58);

  ctx.font = font(44, SERIF, "", 400);
  const offs = charOffsets(ctx, text);
  for (let i = 0; i < Math.floor(typed); i++) {
    const x0 = CHAT_X + offs[i];
    const p = ramp(t, CUE.condense + 0.011 * i, CUE.condense + 0.011 * i + 0.42, ease.in);
    const cx = lerp(x0, v.nx, 0.5);
    const cy = Math.min(CHAT_Y, v.ly) - 70;
    const u = 1 - p;
    const px = u * u * x0 + 2 * u * p * cx + p * p * v.nx;
    const py = u * u * (CHAT_Y) + 2 * u * p * cy + p * p * v.ly;
    const s = lerp(1, 0.08, p);
    if (p >= 1) continue;
    ctx.save();
    ctx.translate(px, py);
    ctx.scale(s, s);
    ctx.fillStyle = v.ink(lerp(0.92, 0.6, p));
    ctx.fillText(text[i], 0, 0);
    ctx.restore();
  }
  // caret
  if (t < CUE.condense) {
    const on = typed < text.length || Math.floor((t - CUE.chatType) * 2.2) % 2 === 0;
    if (on && t > CUE.chatType - 0.2) {
      const cx = CHAT_X + (typed >= text.length ? ctx.measureText(text).width : offs[Math.floor(typed)] ?? 0) + 3;
      ctx.fillStyle = v.accent(0.95);
      ctx.fillRect(cx, CHAT_Y - 36, 2.5, 46);
    }
  }
  // the assistant's ordinary reply
  const replyA = ramp(t, CUE.chatReply, CUE.chatReply + 0.3) * (1 - ramp(t, CUE.condense, CUE.condense + 0.3));
  if (replyA > 0) {
    ctx.font = font(14, MONO, "", 500);
    ctx.fillStyle = v.ink(0.34 * replyA);
    ctx.fillText("ai", CHAT_X, CHAT_Y + 52);
    ctx.font = font(34, SERIF, "italic", 400);
    ctx.fillStyle = v.ink(0.5 * replyA);
    ctx.fillText("noted. have a great trip.", CHAT_X, CHAT_Y + 100);
  }
};

/** Chat keeps happening; each line condenses into a memory at now. */
const drawMontage = (ctx: C, v: View) => {
  MONTAGE.forEach((m) => {
    const t0 = m.appearAt!;
    if (v.t < t0 - 0.7 || v.t > t0 + 0.05) return;
    const a = ramp(v.t, t0 - 0.7, t0 - 0.5);
    const p = ramp(v.t, t0 - 0.24, t0, ease.in);
    const text = m.chat!;
    ctx.font = font(30, SERIF, "", 400);
    ctx.textAlign = "left";
    const w = ctx.measureText(text).width;
    const x0 = v.nx - 40 - w;
    const y0 = v.ly - 110;
    const offs = charOffsets(ctx, text);
    for (let i = 0; i < text.length; i++) {
      const cx0 = x0 + offs[i];
      const px = lerp(cx0, v.nx, p);
      const py = lerp(y0, v.ly, p * p);
      const s = lerp(1, 0.1, p);
      ctx.save();
      ctx.translate(px, py);
      ctx.scale(s, s);
      ctx.fillStyle = v.ink(0.72 * a * (1 - 0.4 * p));
      ctx.fillText(text[i], 0, 0);
      ctx.restore();
    }
  });
};

/** A drop lands: a ring spreads from the point. */
const ripple = (ctx: C, x: number, y: number, t: number, t0: number, color: (a: number) => string, size = 34) => {
  const k = (t - t0) / 0.9;
  if (k < 0 || k > 1) return;
  ctx.strokeStyle = color(0.5 * (1 - k));
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, 6 + size * ease.out(k), 0, Math.PI * 2);
  ctx.stroke();
};

const drawLabels1 = (ctx: C, v: View) => {
  // landing ripples
  ripple(ctx, v.nx, v.ly, v.t, CUE.land, v.ink, 40);
  MONTAGE.forEach((m) => {
    const p = memPos(v, m);
    ripple(ctx, p.x, p.y, v.t, m.appearAt!, v.ink, 22);
  });
  const fp = memPos(v, FLIGHT);
  // saved to gbrain
  const sa = window(v.t, CUE.land + 0.15, CUE.flowStart + 0.4, 0.3, 0.5);
  if (sa > 0) {
    ctx.textAlign = "left";
    ctx.font = font(14, MONO);
    ctx.fillStyle = v.ink(0.55 * sa);
    ctx.fillText("saved to gbrain", fp.x + 14, fp.y - 14);
  }
  // it knew about Monday's flight
  const ka = window(v.t, 8.7, CUE.rewind + 0.1, 0.4, 0.4);
  if (ka > 0) {
    ctx.strokeStyle = v.ink(0.55 * ka);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(fp.x, fp.y, 13, 0, Math.PI * 2);
    ctx.stroke();
    ctx.textAlign = "left";
    ctx.font = font(24, SERIF, "italic");
    ctx.fillStyle = v.ink(0.85 * ka);
    ctx.fillText("flight to new york", fp.x + 22, fp.y - 20);
  }
};

/** The departure, sitting in the future. */
const drawDeparture = (ctx: C, v: View) => {
  const vis = Math.max(
    window(v.t, 0.9, CUE.rewind + 0.2, 0.6, 0.35),
    window(v.t, CUE.nightFull - 0.6, CUE.fire + 0.6, 0.6, 0.5),
  ) * worldFade(v.t);
  if (vis <= 0) return;
  const at = flightDepartAt(v.t);
  const x = X(v, at);
  if (x < -60 || x > W + 60) return;
  const past = at < v.T;
  const a = vis * (past ? 1 - clamp((v.T - at) / 2.5) : 1);
  if (a <= 0) return;
  ctx.strokeStyle = v.ink(0.8 * a);
  ctx.fillStyle = v.ink(0.8 * a);
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.arc(x, v.ly, 4.5, 0, Math.PI * 2);
  ctx.stroke();
  ctx.font = font(15, MONO);
  ctx.textAlign = "right";
  const changed = ramp(v.t, CUE.rewrite + 0.1, CUE.rewrite + 0.9);
  const label = v.t < CUE.rewind ? "ua 123 departs mon 11:00" : "ua 123 departs";
  ctx.fillStyle = v.ink(0.7 * a);
  if (v.t < CUE.rewind) {
    ctx.fillText(label, x + 6, v.ly - 18);
  } else {
    ctx.textAlign = "left";
    ctx.fillText(label, x - 30, v.ly - 44);
    ctx.fillStyle = v.ink(0.7 * a * (1 - changed));
    ctx.fillText("11:00", x - 30, v.ly - 22);
    if (changed > 0) {
      const w = ctx.measureText("11:00").width;
      ctx.strokeStyle = v.ink(0.7 * a);
      ctx.beginPath();
      ctx.moveTo(x - 30, v.ly - 27);
      ctx.lineTo(x - 30 + w * changed, v.ly - 27);
      ctx.stroke();
      ctx.fillStyle = v.accent(a * changed);
      ctx.fillText("10:40", x - 30 + w + 12, v.ly - 22);
    }
  }
  // crossing now, silently
  ripple(ctx, v.nx, v.ly, v.t, CUE.crossFlight, v.ink, 30);
};

/* ---------- act 3: night, Jev ---------- */

type Email = { t: number; fan: number; resolve: number; yes: boolean; subject: string; key: "p1" | "p2"; ms: number };
export const EMAILS: Email[] = [
  { t: CUE.email1, fan: CUE.fan1, resolve: CUE.no, yes: false, subject: "“New York from $99. This weekend only.”", key: "p1", ms: 146 },
  { t: CUE.email2, fan: CUE.fan2, resolve: CUE.yes, yes: true, subject: "“UA 123 schedule change: now departs 10:40.”", key: "p2", ms: 139 },
];

const fanOrder = (() => {
  // nearest memories hear the question first
  const v = view(CUE.fan1);
  const withD = LISTENERS.map((m) => {
    const p = memPos(v, m);
    return { m, d: Math.hypot(p.x - v.nx, p.y - v.ly) };
  });
  const max = Math.max(...withD.map((w) => w.d));
  return new Map(withD.map((w) => [w.m.id, w.d / max]));
})();

export const fanDelay = (m: Memory) => 0.55 * (fanOrder.get(m.id) ?? 0);

const drawEmails = (ctx: C, v: View) => {
  for (const e of EMAILS) {
    const end = e.yes ? CUE.dawn : e.resolve + 0.55;
    if (v.t < e.t || v.t > end + 0.4) continue;
    const drop = ramp(v.t, e.t, e.t + 0.42, ease.out);
    const ex = v.nx;
    const ey = lerp(v.ly - 300, v.ly, drop);
    const gone = 1 - ramp(v.t, end - 0.1, end + 0.4);
    const a = ramp(v.t, e.t, e.t + 0.2) * gone;

    // the question fans out to every listening memory
    const resolved = ramp(v.t, e.resolve, e.resolve + (e.yes ? 0.35 : 0.5), ease.inOut);
    for (const m of LISTENERS) {
      if (!isVisible(v, m)) continue;
      const hero = m === FLIGHT;
      const d = fanDelay(m);
      const grow = ramp(v.t, e.fan + d, e.fan + d + 0.38, ease.out);
      if (grow <= 0) continue;
      const p = memPos(v, m);
      let alpha = (0.1 + 0.08 * v.night) * gone;
      let color = v.ink;
      let lw = 1;
      if (e.yes && hero) {
        alpha = lerp(alpha, 0.95 * gone, resolved);
        if (resolved > 0) color = v.accent;
        lw = lerp(1, 1.6, resolved);
      } else {
        alpha *= 1 - resolved;
      }
      if (alpha <= 0.003) continue;
      ctx.strokeStyle = color(alpha);
      ctx.lineWidth = lw;
      ctx.beginPath();
      ctx.moveTo(ex, v.ly);
      ctx.lineTo(lerp(ex, p.x, grow), lerp(v.ly, p.y, grow));
      ctx.stroke();

      // answers
      const shown = ramp(v.t, e.fan + d + 0.3, e.fan + d + 0.55);
      if ((m.showP || hero) && shown > 0) {
        const val = m[e.key];
        const heroYes = e.yes && hero;
        const pa = shown * (heroYes ? gone : 1 - resolved) * gone;
        if (pa > 0) {
          ctx.textAlign = "left";
          ctx.font = font(heroYes ? 20 : hero ? 15 : 12, MONO, "", heroYes ? 500 : 400);
          const shownVal = heroYes ? lerp(0.03, val, ramp(v.t, e.fan + d + 0.3, e.resolve, ease.out)) : val;
          ctx.fillStyle = heroYes && resolved > 0 ? v.accent(pa) : v.ink((hero ? 0.8 : 0.5) * pa);
          ctx.fillText(shownVal.toFixed(2), p.x + 10, p.y - 10);
        }
      }
    }
    // each memory asks its own question
    if (!e.yes) {
      const qa = window(v.t, e.fan + 0.3, e.resolve + 0.3, 0.5, 0.45);
      for (const m of [FLIGHT, ...MONTAGE]) {
        if (!m.question || qa <= 0) continue;
        const p = memPos(v, m);
        ctx.textAlign = "right";
        ctx.font = font(20, SERIF, "italic");
        ctx.fillStyle = v.ink(0.78 * qa);
        ctx.fillText(m.question, p.x - 16, p.y + 7);
      }
    }

    // the email itself
    ctx.strokeStyle = v.ink(0.95 * a);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(ex, ey, 8, 0, Math.PI * 2);
    ctx.stroke();
    if (drop >= 1) ripple(ctx, ex, v.ly, v.t, e.t + 0.42, v.ink, 44);
    const la = ramp(v.t, e.t + 0.25, e.t + 0.6) * (e.yes ? gone : 1 - ramp(v.t, e.resolve + 0.1, e.resolve + 0.5));
    if (la > 0) {
      ctx.textAlign = "left";
      ctx.font = font(13, MONO, "", 500);
      ctx.fillStyle = v.ink(0.5 * la);
      ctx.fillText(`email · ${clockLabel(v.T).slice(4)}`, ex + 22, v.ly - 108);
      ctx.font = font(27, SERIF, "italic");
      ctx.fillStyle = v.ink(0.92 * la);
      ctx.fillText(e.subject, ex + 22, v.ly - 76);
    }
    // one call, every memory
    const sa = ramp(v.t, e.fan + 0.95, e.fan + 1.25) * (e.yes ? 1 - ramp(v.t, CUE.rewrite + 0.6, CUE.rewrite + 1.1) : 1 - ramp(v.t, e.resolve + 0.1, e.resolve + 0.5));
    if (sa > 0) {
      ctx.textAlign = "left";
      ctx.font = font(14, MONO);
      ctx.fillStyle = v.ink(0.55 * sa);
      ctx.fillText(`1 jev call · ${LISTENERS.length} questions · ${e.ms} ms`, ex + 22, v.ly + 64);
    }
    if (e.yes) {
      // the yes
      const glow = ramp(v.t, e.resolve, e.resolve + 0.4);
      const p = memPos(v, FLIGHT);
      if (glow > 0 && v.t < CUE.fire + 0.6) {
        ctx.fillStyle = v.accent(0.95 * glow);
        ctx.beginPath();
        ctx.arc(p.x, p.y, FLIGHT.r + 0.6, 0, Math.PI * 2);
        ctx.fill();
        ripple(ctx, p.x, p.y, v.t, e.resolve, v.accent, 40);
      }
      const ra = window(v.t, CUE.rewrite, CUE.dawn + 0.8, 0.3, 0.6);
      if (ra > 0) {
        ctx.textAlign = "right";
        ctx.font = font(14, MONO);
        ctx.fillStyle = v.ink(0.6 * ra);
        ctx.fillText("rewritten by river", p.x - 16, p.y + 30);
      }
    }
  }
};

/* ---------- act 4: it reaches you ---------- */

const drawReminder = (ctx: C, v: View) => {
  // the reminder's moment, labelled under the line once the recipe exists
  const la = Math.max(window(v.t, CUE.nightFull - 0.4, CUE.fire + 0.1, 0.6, 0.2), 0) * worldFade(v.t);
  if (la > 0) {
    const at = flightRemindAt(v.t);
    const x = X(v, at);
    const changed = ramp(v.t, CUE.rewrite + 0.1, CUE.rewrite + 0.9);
    ctx.textAlign = "left";
    ctx.font = font(15, MONO);
    ctx.fillStyle = v.ink(0.7 * la);
    ctx.fillText("remind", x - 30, v.ly + 30);
    ctx.fillStyle = v.ink(0.7 * la * (1 - changed));
    ctx.fillText("08:00", x - 30, v.ly + 52);
    if (changed > 0) {
      const w = ctx.measureText("08:00").width;
      ctx.strokeStyle = v.ink(0.7 * la);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - 30, v.ly + 47);
      ctx.lineTo(x - 30 + w * changed, v.ly + 47);
      ctx.stroke();
      ctx.fillStyle = v.accent(la * changed);
      ctx.fillText("07:40", x - 30 + w + 12, v.ly + 52);
    }
  }
  // fire: the memory comes back to now along its own arc
  if (v.t >= CUE.fire && v.t < CUE.outro + 1.4) {
    const p = memPos(v, FLIGHT);
    const k = ramp(v.t, CUE.fire, CUE.notify, ease.inOut);
    const trail = Math.max(0, k - 0.35);
    ctx.strokeStyle = v.accent(0.85 * (1 - ramp(v.t, CUE.notify, CUE.notify + 0.8)));
    ctx.lineWidth = 1.6;
    strokeArc(ctx, p.x, p.y, v.nx, v.ly, trail, k);
    const head = arcPoint(p.x, p.y, v.nx, v.ly, k);
    ctx.fillStyle = v.accent(1);
    ctx.beginPath();
    ctx.arc(head.x, head.y, lerp(4, 7.5, k), 0, Math.PI * 2);
    ctx.fill();
    ripple(ctx, v.nx, v.ly, v.t, CUE.fire, v.accent, 36);
    ripple(ctx, v.nx, v.ly, v.t, CUE.notify, v.accent, 60);
  }
};

/* ---------- close ---------- */

export const WORDMARK = { text: "memento", size: 190, y: 560 };

const drawWordmark = (ctx: C, v: View) => {
  if (v.t < CUE.outro) return;
  ctx.font = font(WORDMARK.size, DISPLAY);
  ctx.textAlign = "left";
  const w = ctx.measureText(WORDMARK.text).width;
  const dotR = WORDMARK.size * 0.052;
  const total = w + dotR * 2.6;
  const x0 = W / 2 - total / 2;
  const base = WORDMARK.y;
  // me · men · to, one syllable per note
  const parts = [
    { s: "me", at: CUE.wordmark },
    { s: "men", at: CUE.wordmark + 0.32 },
    { s: "to", at: CUE.wordmark + 0.64 },
  ];
  let x = x0;
  for (const part of parts) {
    const pw = ctx.measureText(part.s).width;
    const a = ramp(v.t, part.at, part.at + 0.55, ease.out);
    ctx.fillStyle = v.ink(a * (1 - ramp(v.t, CUE.fadeOut, CUE.fadeOut + 1.2)));
    ctx.save();
    ctx.translate(0, (1 - a) * 18);
    ctx.fillText(part.s, x, base);
    ctx.restore();
    x += pw;
  }
  // the memory that came back is the full stop
  const dx = x0 + w + dotR * 1.5;
  const dy = base - dotR;
  const k = ramp(v.t, CUE.outro + 0.2, CUE.wordmark + 0.64, ease.inOut);
  const sx = lerp(v.nx, dx, k);
  const sy = lerp(v.ly, dy, k) - Math.sin(Math.PI * k) * 120;
  ctx.fillStyle = v.accent(1 - ramp(v.t, CUE.fadeOut + 0.2, CUE.fadeOut + 1.3));
  ctx.beginPath();
  ctx.arc(sx, sy, lerp(7.5, dotR, k), 0, Math.PI * 2);
  ctx.fill();

  const ta = ramp(v.t, CUE.tagline, CUE.tagline + 0.8) * (1 - ramp(v.t, CUE.fadeOut, CUE.fadeOut + 1.2));
  if (ta > 0) {
    ctx.textAlign = "center";
    ctx.font = font(44, SERIF, "italic", 300);
    ctx.fillStyle = v.ink(0.8 * ta);
    ctx.fillText("memories that know when they matter.", W / 2, base + 100);
  }
  const ca = ramp(v.t, CUE.credits, CUE.credits + 0.8) * (1 - ramp(v.t, CUE.fadeOut, CUE.fadeOut + 1.2));
  if (ca > 0) {
    ctx.textAlign = "center";
    ctx.font = font(15, MONO);
    ctx.fillStyle = v.ink(0.5 * ca);
    ctx.fillText("a proactive extension for gbrain   ·   recipes by river   ·   judgment by jev", W / 2, 1000);
  }
};

/* ---------- frame ---------- */

export const drawWorld = (ctx: C, t: number) => {
  const v = view(t);
  ctx.clearRect(0, 0, W, 1080);
  const fade = worldFade(t);
  ctx.globalAlpha = fade;
  drawAxis(ctx, v);
  ctx.globalAlpha = 1;
  drawRecipes(ctx, v);
  drawDeparture(ctx, v);
  drawEmails(ctx, v);
  drawMemories(ctx, v);
  drawLabels1(ctx, v);
  drawReminder(ctx, v);
  drawNow(ctx, v);
  drawChat(ctx, v);
  drawMontage(ctx, v);
  drawWordmark(ctx, v);
};
