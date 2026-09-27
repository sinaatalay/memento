export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
export const invLerp = (a: number, b: number, x: number) => clamp((x - a) / (b - a));

export const ease = {
  linear: (k: number) => k,
  inOut: (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2),
  out: (k: number) => 1 - Math.pow(1 - k, 3),
  in: (k: number) => k * k * k,
  outExpo: (k: number) => (k >= 1 ? 1 : 1 - Math.pow(2, -10 * k)),
  inOutSine: (k: number) => -(Math.cos(Math.PI * k) - 1) / 2,
  outBack: (k: number) => {
    const c1 = 1.4;
    const c3 = c1 + 1;
    return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
  },
  smooth: (k: number) => k * k * (3 - 2 * k),
};

/** 0..1 progress of t between a and b, eased. */
export const ramp = (t: number, a: number, b: number, e: (k: number) => number = ease.inOut) =>
  e(invLerp(a, b, t));

/** Rises over [a, a+fadeIn], holds, falls over [b-fadeOut, b]. */
export const window = (t: number, a: number, b: number, fadeIn = 0.3, fadeOut = 0.3) =>
  Math.min(ramp(t, a, a + fadeIn, ease.out), 1 - ramp(t, b - fadeOut, b, ease.inOut));

/** Critically damped-ish spring from 0 to 1 starting at t0 (seconds). */
export const springAt = (t: number, t0: number, stiffness = 170, damping = 20) => {
  if (t <= t0) return 0;
  const x = t - t0;
  const w0 = Math.sqrt(stiffness);
  const zeta = damping / (2 * w0);
  if (zeta >= 1) {
    return 1 - (1 + w0 * x) * Math.exp(-w0 * x);
  }
  const wd = w0 * Math.sqrt(1 - zeta * zeta);
  return 1 - Math.exp(-zeta * w0 * x) * (Math.cos(wd * x) + ((zeta * w0) / wd) * Math.sin(wd * x));
};

type RGB = [number, number, number];
const hex = (h: string): RGB => {
  const n = parseInt(h.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
export const mix = (a: string, b: string, k: number, alpha = 1) => {
  const A = hex(a);
  const B = hex(b);
  const c = A.map((v, i) => Math.round(lerp(v, B[i], clamp(k))));
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
};
export const rgba = (h: string, alpha: number) => {
  const c = hex(h);
  return `rgba(${c[0]},${c[1]},${c[2]},${clamp(alpha)})`;
};
