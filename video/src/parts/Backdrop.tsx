import React from "react";
import { AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { NIGHT, NIGHT_EDGE, PAPER, PAPER_EDGE } from "../theme";
import { TERMINATOR_SOFT, nightness, terminator } from "../timeline";

/** Paper by day; night sweeps in from the future (right) and leaves the same way. */
export const Backdrop: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const term = terminator(t);
  const full = nightness(t);
  let mask: string | undefined;
  let opacity = full;
  if (term.active) {
    opacity = 1;
    const a = term.edge - TERMINATOR_SOFT;
    const b = term.edge + TERMINATOR_SOFT;
    mask = term.nightOnRight
      ? `linear-gradient(to right, transparent ${a}px, black ${b}px)`
      : `linear-gradient(to right, black ${a}px, transparent ${b}px)`;
  }
  // a faint warm band rides the terminator: dusk and dawn
  const glow = term.active ? Math.sin(Math.PI * term.p) : 0;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse 75% 70% at 50% 46%, ${PAPER} 0%, ${PAPER} 45%, ${PAPER_EDGE} 100%)` }} />
      <AbsoluteFill
        style={{
          opacity,
          background: `radial-gradient(ellipse 75% 70% at 55% 48%, ${NIGHT} 0%, ${NIGHT} 40%, ${NIGHT_EDGE} 100%)`,
          WebkitMaskImage: mask,
          maskImage: mask,
        }}
      />
      {glow > 0 && (
        <AbsoluteFill
          style={{
            opacity: 0.22 * glow,
            background: `linear-gradient(to right, transparent ${term.edge - 260}px, #E8834E ${term.edge}px, transparent ${term.edge + 260}px)`,
            mixBlendMode: "soft-light",
          }}
        />
      )}
    </AbsoluteFill>
  );
};

/** Fine grain over everything, so flat colour reads as material. */
export const Grain: React.FC = () => {
  const frame = useCurrentFrame();
  const shift = (Math.floor(frame / 2) * 137) % 512;
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `url(${staticFile("grain.png")})`,
        backgroundSize: "512px 512px",
        backgroundPosition: `${shift}px ${(shift * 7) % 512}px`,
        mixBlendMode: "soft-light",
        opacity: 0.32,
        pointerEvents: "none",
      }}
    />
  );
};
