import { clamp, ease, lerp, ramp, springAt, window } from "../lib/math";
import { ACCENT, DISPLAY, INK, MONO, NIGHT, NIGHT_INK, PAPER, SERIF, W } from "../theme";
import {
  CUE,
  STORY,
  TERMINATOR_SOFT,
  axisScale,
  clockLabel,
  dayName,
  lineY,
  nightAt,
  nightness,
  nowX,
  storyTime,
  terminator,
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
  bg: (a: number) => string;
  accent: (a: number) => string;
};

const hexRGB = (h: string) => {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const INK_RGB = hexRGB(INK);
const NIGHT_INK_RGB = hexRGB(NIGHT_INK);
const PAPER_RGB = hexRGB(PAPER);
const NIGHT_RGB = hexRGB(NIGHT);
const ACC_RGB = hexRGB(ACCENT);
const rgbaOf = (a: number[], b: number[], k: number) => {
  const c = a.map((v, i) => Math.round(lerp(v, b[i], k)));
  return (alpha: number) => `rgba(${c[0]},${c[1]},${c[2]},${clamp(alpha)})`;
};

export const view = (t: number, night = nightness(t)): View => ({
  t,
  T: storyTime(t),
  nx: nowX(t),
  ly: lineY(t),
  S: axisScale(t),
  night,
  ink: rgbaOf(INK_RGB, NIGHT_INK_RGB, night),
  bg: rgbaOf(PAPER_RGB, NIGHT_RGB, night),
  accent: (a) => `rgba(${ACC_RGB[0]},${ACC_RGB[1]},${ACC_RGB[2]},${clamp(a)})`,
});

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
const rewriteK = (t: number) => ramp(t, CUE.rewrite + 0.1, CUE.rewrite + 0.9, ease.inOut);
const flightRemindAt = (t: number) => lerp(STORY.remind, STORY.remindNew, rewriteK(t));
const flightDepartAt = (t: number) => lerp(STORY.flight, STORY.flightNew, rewriteK(t));

/* ---------- helpers ---------- */

const font = (px: number, family: string, style = "", weight = 400) =>
  `${style} ${weight} ${px}px '${family}'`.trim();

const roundRect = (ctx: C, x: number, y: number, w: number, h: number, r: number) => {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
};

/** A label on a quiet backing, so it reads over the memory cloud. */
const pill = (ctx: C, v: View, x: number, y: number, w: number, h: number, a: number) => {
  ctx.fillStyle = v.bg(0.86 * a);
  roundRect(ctx, x, y, w, h, Math.min(10, h / 2));
  ctx.fill();
};

type Run = { s: string; f: string; c: string; strike?: number };
/** Draw styled runs left to right; returns total width. */
const runs = (ctx: C, parts: Run[], x: number, y: number, measureOnly = false) => {
  let cx = x;
  for (const p of parts) {
    ctx.font = p.f;
    const w = ctx.measureText(p.s).width;
    if (!measureOnly) {
      ctx.fillStyle = p.c;
      ctx.fillText(p.s, cx, y);
      if (p.strike) {
        ctx.strokeStyle = p.c;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(cx - 1, y - 7);
        ctx.lineTo(cx - 1 + (w + 2) * p.strike, y - 7);
        ctx.stroke();
      }
    }
    cx += w;
  }
  return cx - x;
};

/* ---------- axis ---------- */

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

/* ---------- recipes ---------- */

/** Point on the arc from a memory to a moment on the line. */
export const arcPoint = (x0: number, y0: number, x1: number, y1: number, k: number) => {
  const span = Math.abs(x1 - x0);
  const cx = (x0 + x1) / 2;
  const cy = Math.min(y0, y1) - clamp(30 + 0.22 * span, 26, 220);
  const u = 1 - k;
  return {
    x: u * u * x0 + 2 * u * k * cx + k * k * x1,
    y: u * u * y0 + 2 * u * k * cy + k * k * y1,
  };
};

const strokeArc = (ctx: C, x0: number, y0: number, x1: number, y1: number, from: number, to: number) => {
  const steps = 56;
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const p = arcPoint(x0, y0, x1, y1, lerp(from, to, i / steps));
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  }
  ctx.stroke();
};

const isVisible = (v: View, m: Memory) => {
  if (m.appearAt !== undefined && v.t < m.appearAt) return false;
  return v.T >= m.created - 1e-6;
};

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
    if (m.listens && x > -20 && x < W + 20) {
      const ph = (m.created * 0.37) % 1;
      const rr = m.r + 4 + 1.3 * Math.sin(2 * Math.PI * (v.t * 0.42 + ph));
      ctx.strokeStyle = v.ink((hero ? 0.55 : 0.1 + 0.04 * v.night) * woke * dim * fade);
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
    ctx.lineWidth = hero ? 1.3 : 1;
    ctx.strokeStyle = hero ? v.ink(0.85 * woke * fade) : v.ink((0.075 + 0.045 * v.night) * woke * dim * fade);
    strokeArc(ctx, x, y, x1, v.ly, 0, woke);
    if (woke > 0.98 && x1 < W + 10) {
      ctx.fillStyle = hero ? v.ink(0.9 * fade) : v.ink(0.25 * dim * fade);
      ctx.beginPath();
      ctx.arc(x1, v.ly, hero ? 3 : 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
  }
};

/* ---------- memories ---------- */

const drawMemories = (ctx: C, v: View) => {
  const fade = worldFade(v.t);
  const vPrev = view(v.t - 1 / 60, v.night);
  for (const m of MEMORIES) {
    if (!isVisible(v, m)) continue;
    const { x, y } = memPos(v, m);
    if (x < -30 || x > W + 30) continue;
    const k = ageK(v.T - m.created);
    const hero = m === FLIGHT;
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
      ctx.fillStyle = v.ink(alpha);
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

/** A sentence gathers to its middle and becomes one drop of ink; the drop falls onto "now". */
const condense = (
  ctx: C,
  v: View,
  text: string,
  x0: number,
  y0: number,
  size: number,
  alpha: number,
  t0: number,
  gather: number,
  fall: number,
  dotR: number,
  style = "",
) => {
  ctx.font = font(size, SERIF, style, 400);
  ctx.textAlign = "left";
  const offs = charOffsets(ctx, text);
  const w = ctx.measureText(text).width;
  const cx = x0 + w / 2;
  const cy = y0 - size * 0.3;
  const g = ramp(v.t, t0, t0 + gather, ease.inOut);
  // letters slide together, thinning as they meet
  if (g < 1) {
    for (let i = 0; i < text.length; i++) {
      const lx = x0 + offs[i];
      const px = lerp(lx, cx - size * 0.15, g);
      ctx.fillStyle = v.ink(alpha * Math.pow(1 - g, 1.7));
      ctx.fillText(text[i], px, y0);
    }
  }
  // the drop: forms where the words met, then falls to now
  const form = ramp(v.t, t0 + gather * 0.45, t0 + gather, ease.out);
  const f = ramp(v.t, t0 + gather, t0 + gather + fall, ease.inOut);
  if (form > 0 && f < 1) {
    const px = lerp(cx, v.nx, f);
    const py = lerp(cy, v.ly, f) - Math.sin(Math.PI * f) * 38;
    ctx.fillStyle = v.ink(0.92);
    ctx.beginPath();
    ctx.arc(px, py, dotR * form, 0, Math.PI * 2);
    ctx.fill();
  }
};

const drawChat = (ctx: C, v: View) => {
  const t = v.t;
  if (t > CUE.land + 0.1) return;
  const text = FLIGHT.chat!;
  const typed = clamp((t - CUE.chatType) * 32, 0, text.length);
  const labelA = ramp(t, CUE.chatType - 0.35, CUE.chatType) * (1 - ramp(t, CUE.condense - 0.1, CUE.condense + 0.2));
  ctx.textAlign = "left";
  ctx.font = font(14, MONO, "", 500);
  ctx.fillStyle = v.ink(0.42 * labelA);
  ctx.fillText("you", CHAT_X, CHAT_Y - 58);

  if (t < CUE.condense) {
    ctx.font = font(44, SERIF, "", 400);
    const shown = text.slice(0, Math.floor(typed));
    ctx.fillStyle = v.ink(0.92);
    ctx.fillText(shown, CHAT_X, CHAT_Y);
    const on = typed < text.length || Math.floor((t - CUE.chatType) * 2.2) % 2 === 0;
    if (on && t > CUE.chatType - 0.2) {
      const cx = CHAT_X + ctx.measureText(shown).width + 3;
      ctx.fillStyle = v.accent(0.95);
      ctx.fillRect(cx, CHAT_Y - 36, 2.5, 46);
    }
  } else {
    condense(ctx, v, text, CHAT_X, CHAT_Y, 44, 0.92, CUE.condense, 0.34, CUE.land - CUE.condense - 0.34, FLIGHT.r);
  }
  const replyA = ramp(t, CUE.chatReply, CUE.chatReply + 0.3) * (1 - ramp(t, CUE.condense - 0.05, CUE.condense + 0.2));
  if (replyA > 0) {
    ctx.font = font(14, MONO, "", 500);
    ctx.fillStyle = v.ink(0.34 * replyA);
    ctx.fillText("ai", CHAT_X, CHAT_Y + 52);
    ctx.font = font(34, SERIF, "italic", 400);
    ctx.fillStyle = v.ink(0.5 * replyA);
    ctx.fillText("noted. have a great trip.", CHAT_X, CHAT_Y + 100);
  }
};

const drawMontage = (ctx: C, v: View) => {
  MONTAGE.forEach((m) => {
    const t0 = m.appearAt!;
    if (v.t < t0 - 0.72 || v.t > t0 + 0.02) return;
    const text = m.chat!;
    ctx.font = font(30, SERIF, "", 400);
    const w = ctx.measureText(text).width;
    const x0 = v.nx - 60 - w;
    const y0 = v.ly - 120;
    const a = ramp(v.t, t0 - 0.72, t0 - 0.55);
    condense(ctx, v, text, x0, y0, 30, 0.75 * a, t0 - 0.42, 0.2, 0.22, m.r);
  });
};

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
  ripple(ctx, v.nx, v.ly, v.t, CUE.land, v.ink, 40);
  MONTAGE.forEach((m) => {
    const p = memPos(v, m);
    ripple(ctx, p.x, p.y, v.t, m.appearAt!, v.ink, 22);
  });
  const fp = memPos(v, FLIGHT);
  const sa = window(v.t, CUE.land + 0.15, CUE.flowStart + 0.4, 0.3, 0.5);
  if (sa > 0) {
    ctx.textAlign = "left";
    ctx.font = font(14, MONO);
    ctx.fillStyle = v.ink(0.55 * sa);
    ctx.fillText("saved to gbrain", fp.x + 14, fp.y - 14);
  }
  const ka = window(v.t, 8.7, CUE.rewind + 0.1, 0.4, 0.4);
  if (ka > 0) {
    ctx.strokeStyle = v.ink(0.55 * ka);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(fp.x, fp.y, 13, 0, Math.PI * 2);
    ctx.stroke();
    ctx.textAlign = "left";
    ctx.font = font(24, SERIF, "italic");
    const w = ctx.measureText("flight to new york").width;
    pill(ctx, v, fp.x + 16, fp.y - 46, w + 16, 34, ka);
    ctx.fillStyle = v.ink(0.88 * ka);
    ctx.fillText("flight to new york", fp.x + 24, fp.y - 21);
  }
};

/** The departure itself, sitting in the future. */
const drawDeparture = (ctx: C, v: View) => {
  const vis =
    Math.max(window(v.t, 0.9, CUE.rewind + 0.2, 0.6, 0.35), window(v.t, CUE.nightFull - 0.6, CUE.fire + 0.6, 0.6, 0.5)) *
    worldFade(v.t);
  if (vis <= 0) return;
  const at = flightDepartAt(v.t);
  const x = X(v, at);
  if (x < -60 || x > W + 60) return;
  const past = at < v.T;
  const a = vis * (past ? 1 - clamp((v.T - at) / 2.5) : 1);
  if (a > 0) {
    ctx.strokeStyle = v.ink(0.8 * a);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(x, v.ly, 4.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.font = font(15, MONO);
    if (v.t < CUE.rewind) {
      ctx.textAlign = "right";
      ctx.fillStyle = v.ink(0.7 * a);
      ctx.fillText("ua123 departs mon 11:00", x + 6, v.ly - 18);
    } else {
      const ch = rewriteK(v.t);
      ctx.textAlign = "left";
      ctx.fillStyle = v.ink(0.7 * a);
      ctx.fillText("ua123 departs", x - 30, v.ly - 44);
      runs(
        ctx,
        [
          { s: "11:00", f: font(15, MONO), c: v.ink(lerp(0.75, 0.38, ch) * a), strike: ch },
          { s: "  10:40", f: font(15, MONO, "", 500), c: v.accent(a * ch) },
        ],
        x - 30,
        v.ly - 22,
      );
    }
  }
  ripple(ctx, v.nx, v.ly, v.t, CUE.crossFlight, v.ink, 30);
};

/* ---------- act 3: night, Jev ---------- */

type Email = { t: number; fan: number; resolve: number; yes: boolean; subject: string; key: "p1" | "p2"; ms: number };
export const EMAILS: Email[] = [
  { t: CUE.email1, fan: CUE.fan1, resolve: CUE.no, yes: false, subject: "“New York from $99. This weekend only.”", key: "p1", ms: 146 },
  { t: CUE.email2, fan: CUE.fan2, resolve: CUE.yes, yes: true, subject: "“UA123 schedule change: now departs 10:40.”", key: "p2", ms: 139 },
];

const fanOrder = (() => {
  const v = view(CUE.fan1, 1);
  const withD = LISTENERS.map((m) => {
    const p = memPos(v, m);
    return { m, d: Math.hypot(p.x - v.nx, p.y - v.ly) };
  });
  const max = Math.max(...withD.map((w) => w.d));
  return new Map(withD.map((w) => [w.m.id, w.d / max]));
})();

/** Nearest memories hear the question first. Exported for the score. */
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
        lw = lerp(1, 1.7, resolved);
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

      const shown = ramp(v.t, e.fan + d + 0.3, e.fan + d + 0.55);
      if ((m.showP || hero) && shown > 0) {
        const heroYes = e.yes && hero;
        const pa = shown * (heroYes ? gone : 1 - resolved) * gone;
        if (pa > 0) {
          ctx.textAlign = "left";
          ctx.font = font(heroYes ? 22 : hero ? 15 : 12, MONO, "", heroYes ? 500 : 400);
          const val = heroYes ? lerp(0.03, m[e.key], ramp(v.t, e.fan + d + 0.3, e.resolve, ease.out)) : m[e.key];
          ctx.fillStyle = heroYes && resolved > 0 ? v.accent(pa) : v.ink((hero ? 0.85 : 0.5) * pa);
          ctx.fillText(val.toFixed(2), p.x + 11, p.y - 11);
        }
      }
    }
    // every memory is asked its own question
    if (!e.yes) {
      const qa = window(v.t, e.fan + 0.3, e.resolve + 0.3, 0.5, 0.45);
      if (qa > 0) {
        for (const m of [FLIGHT, ...MONTAGE]) {
          if (!m.question) continue;
          const p = memPos(v, m);
          ctx.font = font(21, SERIF, "italic");
          const w = ctx.measureText(m.question).width;
          pill(ctx, v, p.x - 24 - w - 10, p.y - 17, w + 20, 32, qa);
          ctx.textAlign = "right";
          ctx.fillStyle = v.ink((m === FLIGHT ? 0.95 : 0.7) * qa);
          ctx.fillText(m.question, p.x - 24, p.y + 6);
        }
      }
    }

    // the email
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
      ctx.fillText(`new email · ${clockLabel(v.T).slice(4)}`, ex + 22, v.ly - 108);
      ctx.font = font(28, SERIF, "italic");
      ctx.fillStyle = v.ink(0.94 * la);
      ctx.fillText(e.subject, ex + 22, v.ly - 76);
    }
    const sa =
      ramp(v.t, e.fan + 0.95, e.fan + 1.25) *
      (e.yes ? 1 - ramp(v.t, CUE.rewrite + 0.6, CUE.rewrite + 1.1) : 1 - ramp(v.t, e.resolve + 0.1, e.resolve + 0.5));
    if (sa > 0) {
      ctx.textAlign = "left";
      ctx.font = font(14, MONO);
      ctx.fillStyle = v.ink(0.7 * sa);
      ctx.fillText(`1 jev call · ${LISTENERS.length} questions · ${e.ms} ms`, ex + 22, v.ly + 72);
    }
    if (e.yes) {
      const glow = ramp(v.t, e.resolve, e.resolve + 0.4);
      const p = memPos(v, FLIGHT);
      if (glow > 0 && v.t < CUE.fire + 0.6) {
        ctx.fillStyle = v.accent(0.95 * glow);
        ctx.beginPath();
        ctx.arc(p.x, p.y, FLIGHT.r + 0.6, 0, Math.PI * 2);
        ctx.fill();
        ripple(ctx, p.x, p.y, v.t, e.resolve, v.accent, 40);
      }
      // the memory rewrites itself
      const ra = window(v.t, CUE.rewrite, CUE.dawn + 0.9, 0.35, 0.6);
      if (ra > 0) {
        const ch = rewriteK(v.t);
        const line: Run[] = [
          { s: "Flight to New York · Mon ", f: font(22, SERIF, "italic"), c: v.ink(0.9 * ra) },
          { s: "11:00", f: font(22, SERIF, "italic"), c: v.ink(lerp(0.9, 0.4, ch) * ra), strike: ch },
          { s: " 10:40", f: font(22, SERIF, "italic", 500), c: v.accent(ra * ch) },
        ];
        const w = runs(ctx, line, 0, 0, true);
        const x0 = p.x - 26 - w;
        const y0 = p.y + 54;
        pill(ctx, v, x0 - 12, y0 - 26, w + 24, 62, ra);
        ctx.textAlign = "left";
        runs(ctx, line, x0, y0);
        ctx.font = font(13, MONO);
        ctx.fillStyle = v.ink(0.55 * ra);
        ctx.fillText("update(news) · rewritten by river", x0, y0 + 24);
      }
    }
  }
};

/* ---------- act 4: it reaches you ---------- */

const drawReminder = (ctx: C, v: View) => {
  const la = window(v.t, CUE.nightFull - 0.4, CUE.fire + 0.1, 0.6, 0.2) * worldFade(v.t);
  if (la > 0) {
    const x = X(v, flightRemindAt(v.t));
    const ch = rewriteK(v.t);
    ctx.textAlign = "left";
    ctx.font = font(15, MONO);
    ctx.fillStyle = v.ink(0.7 * la);
    ctx.fillText("leave for sfo", x - 30, v.ly + 30);
    runs(
      ctx,
      [
        { s: "08:00", f: font(15, MONO), c: v.ink(lerp(0.75, 0.38, ch) * la), strike: ch },
        { s: "  07:40", f: font(15, MONO, "", 500), c: v.accent(la * ch) },
      ],
      x - 30,
      v.ly + 52,
    );
  }
  if (v.t >= CUE.fire && v.t < CUE.outro + 1.4) {
    const p = memPos(v, FLIGHT);
    const k = ramp(v.t, CUE.fire, CUE.notify, ease.inOut);
    const trail = Math.max(0, k - 0.35);
    ctx.strokeStyle = v.accent(0.85 * (1 - ramp(v.t, CUE.notify, CUE.notify + 0.8)));
    ctx.lineWidth = 1.7;
    strokeArc(ctx, p.x, p.y, v.nx, v.ly, trail, k);
    const head = arcPoint(p.x, p.y, v.nx, v.ly, k);
    ctx.fillStyle = v.accent(v.t > CUE.outro + 0.2 ? 0 : 1);
    ctx.beginPath();
    ctx.arc(head.x, head.y, lerp(4, 7.5, k), 0, Math.PI * 2);
    ctx.fill();
    ripple(ctx, v.nx, v.ly, v.t, CUE.fire, v.accent, 36);
    ripple(ctx, v.nx, v.ly, v.t, CUE.notify, v.accent, 60);
  }
};

/* ---------- close ---------- */

export const WORDMARK = { text: "memento", size: 190, y: 560 };
export const SYLLABLES = [CUE.wordmark, CUE.wordmark + 0.32, CUE.wordmark + 0.64];

const drawWordmark = (ctx: C, v: View) => {
  if (v.t < CUE.outro) return;
  ctx.font = font(WORDMARK.size, DISPLAY);
  ctx.textAlign = "left";
  const w = ctx.measureText(WORDMARK.text).width;
  const dotR = WORDMARK.size * 0.052;
  const total = w + dotR * 2.6;
  const x0 = W / 2 - total / 2;
  const base = WORDMARK.y;
  const out = 1 - ramp(v.t, CUE.fadeOut, CUE.fadeOut + 1.2);
  let x = x0;
  ["me", "men", "to"].forEach((s, i) => {
    const pw = ctx.measureText(s).width;
    const a = ramp(v.t, SYLLABLES[i], SYLLABLES[i] + 0.55, ease.out);
    ctx.fillStyle = v.ink(a * out);
    ctx.save();
    ctx.translate(0, (1 - a) * 18);
    ctx.fillText(s, x, base);
    ctx.restore();
    x += pw;
  });
  // the memory that came back becomes the full stop
  const dx = x0 + w + dotR * 1.5;
  const dy = base - dotR;
  const k = ramp(v.t, CUE.outro + 0.2, SYLLABLES[2], ease.inOut);
  const sx = lerp(v.nx, dx, k);
  const sy = lerp(v.ly, dy, k) - Math.sin(Math.PI * k) * 120;
  ctx.fillStyle = v.accent(1 - ramp(v.t, CUE.fadeOut + 0.2, CUE.fadeOut + 1.3));
  ctx.beginPath();
  ctx.arc(sx, sy, lerp(7.5, dotR, k), 0, Math.PI * 2);
  ctx.fill();

  const ta = ramp(v.t, CUE.tagline, CUE.tagline + 0.8) * out;
  if (ta > 0) {
    ctx.textAlign = "center";
    ctx.font = font(44, SERIF, "italic", 300);
    ctx.fillStyle = v.ink(0.8 * ta);
    ctx.fillText("memories that know when they matter.", W / 2, base + 100);
  }
  const ca = ramp(v.t, CUE.credits, CUE.credits + 0.8) * out;
  if (ca > 0) {
    ctx.textAlign = "center";
    ctx.font = font(15, MONO);
    ctx.fillStyle = v.ink(0.5 * ca);
    ctx.fillText("a proactive extension for gbrain   ·   recipes by river   ·   judgment by jev", W / 2, 1000);
  }
};

/* ---------- frame ---------- */

const paint = (ctx: C, v: View) => {
  ctx.clearRect(0, 0, W, 1080);
  ctx.globalAlpha = worldFade(v.t);
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

/** Keep only the part of a pass that belongs to its side of the terminator. */
const maskPass = (ctx: C, t: number, wantNight: boolean) => {
  const { edge } = terminator(t);
  const g = ctx.createLinearGradient(edge - TERMINATOR_SOFT, 0, edge + TERMINATOR_SOFT, 0);
  for (let i = 0; i <= 12; i++) {
    const x = edge - TERMINATOR_SOFT + (2 * TERMINATOR_SOFT * i) / 12;
    const n = nightAt(t, x);
    g.addColorStop(i / 12, `rgba(0,0,0,${wantNight ? n : 1 - n})`);
  }
  ctx.globalCompositeOperation = "destination-in";
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, 1080);
  ctx.globalCompositeOperation = "source-over";
};

export const drawWorld = (ctx: C, t: number, scratch: [C, C] | null) => {
  const term = terminator(t);
  if (!term.active || !scratch) {
    paint(ctx, view(t));
    return;
  }
  const [day, night] = scratch;
  paint(day, view(t, 0));
  maskPass(day, t, false);
  paint(night, view(t, 1));
  maskPass(night, t, true);
  ctx.clearRect(0, 0, W, 1080);
  ctx.drawImage(day.canvas, 0, 0, W, 1080);
  ctx.drawImage(night.canvas, 0, 0, W, 1080);
};
