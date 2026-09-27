import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { ease, lerp, ramp, springAt } from "../lib/math";
import { ACCENT, SANS } from "../theme";
import { CUE } from "../timeline";

/** The moment the memory reaches you. */
export const Notification: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < CUE.notify - 0.05 || t > CUE.outro + 0.8) return null;
  const inK = springAt(t, CUE.notify, 180, 20);
  const out = ramp(t, CUE.outro - 0.1, CUE.outro + 0.5, ease.inOut);
  const x = lerp(620, 0, inK) + out * 40;
  return (
    <div
      style={{
        position: "absolute",
        right: 72,
        top: 72,
        width: 600,
        transform: `translateX(${x}px)`,
        opacity: Math.min(1, inK * 1.4) * (1 - out),
        borderRadius: 28,
        background: "rgba(251,250,248,0.94)",
        boxShadow: "0 24px 60px rgba(30,24,18,0.16), 0 2px 8px rgba(30,24,18,0.08)",
        border: "1px solid rgba(28,27,25,0.07)",
        padding: "24px 28px 26px 24px",
        display: "flex",
        gap: 20,
        fontFamily: SANS,
        color: "#1C1B19",
      }}
    >
      <div
        style={{
          flex: "0 0 auto",
          width: 64,
          height: 64,
          borderRadius: 15,
          background: "#EDEAE4",
          border: "1px solid rgba(28,27,25,0.08)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div style={{ width: 16, height: 16, borderRadius: 8, background: ACCENT }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 21, lineHeight: "28px" }}>
          <span style={{ fontWeight: 600 }}>Memento</span>
          <span style={{ color: "rgba(28,27,25,0.45)" }}>now</span>
        </div>
        <div style={{ fontSize: 24, lineHeight: "32px", fontWeight: 600, marginTop: 4 }}>Time to leave for SFO.</div>
        <div style={{ fontSize: 22, lineHeight: "30px", color: "rgba(28,27,25,0.75)", marginTop: 2 }}>
          UA 123 now departs at 10:40, not 11:00.
        </div>
      </div>
    </div>
  );
};
