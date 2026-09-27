import React from "react";
import { AbsoluteFill, staticFile, useCurrentFrame } from "remotion";
import { PAPER, PAPER_EDGE } from "../theme";

/** Paper. */
export const Backdrop: React.FC = () => (
  <AbsoluteFill style={{ background: `radial-gradient(ellipse 75% 70% at 50% 46%, ${PAPER} 0%, ${PAPER} 45%, ${PAPER_EDGE} 100%)` }} />
);

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
