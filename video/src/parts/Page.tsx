import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { clamp, ease, lerp, ramp, springAt } from "../lib/math";
import { ACCENT, INK, MONO, SERIF } from "../theme";
import { FRONT_END, FRONT_TOP, JEV, MATH, RECIPE, TYPING, type Highlight } from "../recipe";
import { CUE, lineY, nowX } from "../timeline";

/** The flight memory as a GBrain page: first as it is today, then with its recipe. */

const FS = 21;
const LH = 33;
const CH = FS * 0.6;
const PAD_X = 50;
const PAD_Y = 34;
const CARD_W = 1000;
const BODY_H = 118;

const ink = (a: number) => `rgba(28,27,25,${clamp(a)})`;
const STR = "#8A6A4A";

const BEFORE_H = PAD_Y * 2 + (FRONT_TOP.length + FRONT_END.length) * LH + BODY_H;
const RECIPE_H = RECIPE.length * LH;

const revealed = (lineIdx: number, t: number) => {
  if (lineIdx === 0) return RECIPE[0].length;
  const s = TYPING[lineIdx - 1];
  return clamp(Math.floor((t - s.start) / s.perChar), 0, s.len);
};

type Tok = { s: string; color: string; weight?: number };
const WORD = /("[^"]*"?|[A-Za-z_][A-Za-z_0-9]*|\d[\d\-:. ]*\d|\s+|.)/g;
const tokenize = (line: string, yamlKey = false): Tok[] => {
  const out: Tok[] = [];
  const parts = line.match(WORD) ?? [];
  parts.forEach((p, i) => {
    const next = parts[i + 1];
    let color = ink(0.88);
    let weight = 400;
    if (p.startsWith('"')) color = STR;
    else if (p === "memento") {
      color = ACCENT;
      weight = 500;
    } else if (p === "from" || p === "import") color = ink(0.45);
    else if (p === "def") color = ink(0.45);
    else if (p === "@") color = ACCENT;
    else if (/^[A-Za-z_]/.test(p) && parts[i - 1] === "@") {
      color = ACCENT;
      weight = 500;
    } else if (/^[A-Za-z_]/.test(p) && next === "(") weight = 500;
    else if (/^[A-Za-z_]/.test(p) && next === ":" && yamlKey) color = ink(0.45);
    else if (/^[^\sA-Za-z0-9"]$/.test(p)) color = ink(0.42);
    out.push({ s: p, color, weight });
  });
  return out;
};

const CodeLine: React.FC<{ text: string; shown?: number; yamlKey?: boolean }> = ({ text, shown, yamlKey }) => {
  const toks = tokenize(text, yamlKey);
  let used = 0;
  return (
    <div style={{ height: LH, lineHeight: `${LH}px`, whiteSpace: "pre" }}>
      {toks.map((tk, i) => {
        const visible = shown === undefined ? tk.s.length : clamp(shown - used, 0, tk.s.length);
        used += tk.s.length;
        return (
          <span key={i} style={{ color: tk.color, fontWeight: tk.weight }}>
            {tk.s.slice(0, visible)}
            <span style={{ color: "transparent" }}>{tk.s.slice(visible)}</span>
          </span>
        );
      })}
    </div>
  );
};

export const Page: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < CUE.cardOpen - 0.05 || t > CUE.cardClose + 0.6) return null;

  const open = springAt(t, CUE.cardOpen, 150, 19);
  const close = ramp(t, CUE.cardClose, CUE.cardClose + 0.5, ease.inOut);
  const s = clamp(open) * (1 - close);
  if (s <= 0.001) return null;

  const nx = nowX(t);
  const ly = lineY(t);
  const recipeK = springAt(t, CUE.recipeOpen, 120, 19);
  const fullH = BEFORE_H + RECIPE_H * recipeK;
  const bottom = ly - 36;
  const fullTop = bottom - fullH;
  const cx = lerp(nx, 960, s);
  const cy = lerp(ly, fullTop + fullH / 2, s);
  const w = lerp(13, CARD_W, s);
  const h = lerp(13, fullH, s);
  const content = ramp(t, CUE.cardOpen + 0.12, CUE.cardOpen + 0.45) * (1 - ramp(t, CUE.cardClose - 0.15, CUE.cardClose + 0.1));

  const labelBefore = 1 - ramp(t, CUE.recipeOpen - 0.1, CUE.recipeOpen + 0.3);
  const labelAfter = ramp(t, CUE.recipeOpen + 0.1, CUE.recipeOpen + 0.5);

  const caretLine = (() => {
    for (let i = RECIPE.length - 1; i >= 1; i--) {
      if (t >= TYPING[i - 1].start && RECIPE[i].length > 0) return i;
    }
    return 1;
  })();
  const caretCol = revealed(caretLine, t);
  const typing = t >= CUE.typeStart - 0.3 && t < CUE.typeEnd + 0.5;
  const caretOn = t < CUE.typeEnd || Math.floor(t * 2.4) % 2 === 0;
  const writtenA = ramp(t, CUE.typeEnd + 0.3, CUE.typeEnd + 0.8);

  const hi = (spec: Highlight) => {
    const k = ramp(t, spec.at, spec.at + 0.45, ease.out);
    return k * (1 - ramp(t, CUE.cardClose - 0.4, CUE.cardClose));
  };
  const mathK = hi(MATH);
  const jevK = hi(JEV);

  const topFront = PAD_Y;
  const recipeTop = PAD_Y + FRONT_TOP.length * LH;
  const endTop = recipeTop + RECIPE_H * recipeK;
  const bodyTop = endTop + FRONT_END.length * LH + 14;

  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {/* stem to the memory on the line */}
      <div
        style={{
          position: "absolute",
          left: nx - 0.5,
          top: bottom,
          width: 1,
          height: Math.max(0, ly - 9 - bottom) * s,
          background: ink(0.35 * content),
        }}
      />
      {/* labels */}
      <div
        style={{
          position: "absolute",
          left: cx - w / 2,
          top: cy - h / 2 - 40,
          fontFamily: MONO,
          fontSize: 16,
          letterSpacing: 0.3,
          color: ink(0.5),
          opacity: content,
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ opacity: labelBefore }}>a gbrain memory</span>
        <span style={{ position: "absolute", left: 0, opacity: labelAfter }}>
          the same memory, with <span style={{ color: ACCENT }}>memento</span>
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          left: cx - w / 2,
          top: cy - h / 2,
          width: w,
          height: h,
          borderRadius: lerp(6.5, 18, s),
          background: "#F7F5F1",
          boxShadow: `0 ${30 * s}px ${70 * s}px rgba(40,30,20,${0.13 * s}), 0 1px 3px rgba(40,30,20,${0.08 * s})`,
          border: `1px solid ${ink(0.09 * s)}`,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: PAD_X,
            top: 0,
            width: CARD_W - PAD_X * 2,
            height: fullH,
            opacity: content,
            fontFamily: MONO,
            fontSize: FS,
          }}
        >
          <div style={{ position: "absolute", top: topFront }}>
            {FRONT_TOP.map((l, i) => (
              <CodeLine key={i} text={l} yamlKey />
            ))}
          </div>
          {/* the recipe unfolds inside the front matter */}
          <div style={{ position: "absolute", top: recipeTop, height: RECIPE_H * recipeK, overflow: "hidden", width: "100%" }}>
            {/* highlight bands */}
            {[{ spec: MATH, k: mathK }, { spec: JEV, k: jevK }].flatMap(({ spec, k }, i) =>
              k > 0
                ? spec.ranges.map((r, j) => (
                    <div
                      key={`${i}-${j}`}
                      style={{
                        position: "absolute",
                        top: r.line * LH + 3,
                        left: r.col * CH - 5,
                        width: (r.len * CH + 10) * k,
                        height: LH - 6,
                        borderRadius: 6,
                        background: `rgba(236,78,32,${0.12 * k})`,
                        boxShadow: `inset 0 -2px 0 rgba(236,78,32,${0.8 * k})`,
                      }}
                    />
                  ))
                : [],
            )}
            {RECIPE.map((l, i) => (
              <CodeLine key={i} text={l} yamlKey={i === 0} shown={revealed(i, t)} />
            ))}
            {typing && caretOn && (
              <div
                style={{
                  position: "absolute",
                  top: caretLine * LH + 6,
                  left: caretCol * CH + 1,
                  width: 2.5,
                  height: LH - 12,
                  background: ACCENT,
                }}
              />
            )}
            {typing && t < CUE.typeEnd + 0.2 && (
              <div
                style={{
                  position: "absolute",
                  top: caretLine * LH - 10,
                  left: caretCol * CH + 10,
                  fontSize: 13,
                  color: ACCENT,
                  fontWeight: 500,
                  opacity: ramp(t, CUE.typeStart - 0.3, CUE.typeStart) * (1 - ramp(t, CUE.typeEnd - 0.1, CUE.typeEnd + 0.2)),
                }}
              >
                river
              </div>
            )}
            <div
              style={{
                position: "absolute",
                top: 0,
                right: 0,
                height: LH,
                lineHeight: `${LH}px`,
                fontSize: 15,
                color: ink(0.45),
                opacity: writtenA,
              }}
            >
              written by <span style={{ color: ACCENT }}>river</span> as the memory was saved
            </div>
          </div>
          <div style={{ position: "absolute", top: endTop }}>
            {FRONT_END.map((l, i) => (
              <CodeLine key={i} text={l} />
            ))}
          </div>
          <div style={{ position: "absolute", top: bodyTop, fontFamily: SERIF }}>
            <div style={{ fontSize: 42, lineHeight: "54px", fontWeight: 500, color: ink(0.92), letterSpacing: -0.3 }}>
              <span style={{ fontFamily: MONO, fontSize: 26, color: ink(0.3), marginRight: 14, fontWeight: 400 }}>#</span>
              Flight to New York
            </div>
            <div style={{ fontSize: 26, lineHeight: "40px", color: ink(0.62) }}>
              UA123 · SFO → JFK · Monday, Sep 28 at 11:00
            </div>
          </div>
        </div>
      </div>
      {/* annotations, outside the page */}
      {[{ spec: MATH, k: mathK }, { spec: JEV, k: jevK }].map(({ spec, k }, i) => {
        if (k <= 0) return null;
        const cardLeft = cx - w / 2;
        const y = cy - h / 2 + recipeTop + spec.noteLine * LH + LH / 2;
        const widest = Math.max(...spec.ranges.map((r) => RECIPE[r.line].length));
        const x0 = cardLeft + PAD_X + widest * CH + 18;
        const x1 = cardLeft + CARD_W + 36;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: "absolute",
                left: x0,
                top: y,
                width: (x1 - x0) * k,
                height: 1,
                background: `rgba(236,78,32,${0.75 * k})`,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: x1 + 12,
                top: y - 16,
                fontFamily: MONO,
                fontSize: 21,
                lineHeight: "28px",
                color: ink(0.88),
                opacity: ramp(t, spec.at + 0.25, spec.at + 0.6) * (1 - ramp(t, CUE.cardClose - 0.4, CUE.cardClose)),
                whiteSpace: "nowrap",
              }}
            >
              {spec.note}
              <div style={{ fontSize: 14, color: ink(0.45), marginTop: 2 }}>{spec.sub}</div>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};
