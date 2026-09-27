import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { NIGHT, NIGHT_EDGE, PAPER, PAPER_EDGE } from "../theme";
import { nightness } from "../timeline";

/** Paper by day, ink by night. */
export const Backdrop: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const n = nightness(frame / fps);
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse 75% 70% at 50% 46%, ${PAPER} 0%, ${PAPER} 45%, ${PAPER_EDGE} 100%)` }} />
      <AbsoluteFill
        style={{
          opacity: n,
          background: `radial-gradient(ellipse 75% 70% at 55% 48%, ${NIGHT} 0%, ${NIGHT} 40%, ${NIGHT_EDGE} 100%)`,
        }}
      />
    </AbsoluteFill>
  );
};

/** Fine grain over everything, so flat colour reads as material. */
export const Grain: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const n = nightness(frame / fps);
  const shift = (Math.floor(frame / 2) * 137) % 512;
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `url(${staticFile("grain.png")})`,
        backgroundSize: "512px 512px",
        backgroundPosition: `${shift}px ${(shift * 7) % 512}px`,
        mixBlendMode: n > 0.5 ? "screen" : "multiply",
        opacity: n > 0.5 ? 0.05 : 0.09,
        pointerEvents: "none",
      }}
    />
  );
};
