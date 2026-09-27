import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { clamp, ramp } from "../lib/math";
import { MONO, SERIF } from "../theme";
import { LINES, lineAt, nightness, presenterOn } from "../timeline";

/** Where you'll stand. A placeholder until the recording arrives. */
export const Presenter: React.FC<{ guides: boolean }> = ({ guides }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const on = clamp(presenterOn(t));
  const n = nightness(t);
  const ink = (a: number) => (n > 0.5 ? `rgba(239,236,230,${a})` : `rgba(28,27,25,${a})`);
  const line = lineAt(t);
  const lineK = line ? ramp(t, line.at - 0.05, line.at + 0.15) * (1 - ramp(t, line.at + line.dur + 0.1, line.at + line.dur + 0.35)) : 0;
  if (!guides) return null;
  return (
    <>
      {on > 0.01 && (
        <svg width={1920} height={1080} style={{ position: "absolute", inset: 0, opacity: on }}>
          <g transform="translate(380 0)">
            <path
              d="M -215 1080 C -215 860 -190 720 -95 690 C -60 680 -40 668 -38 640 L -38 600 C -92 570 -112 505 -112 440 C -112 352 -62 300 0 300 C 62 300 112 352 112 440 C 112 505 92 570 38 600 L 38 640 C 40 668 60 680 95 690 C 190 720 215 860 215 1080 Z"
              fill={ink(0.035)}
              stroke={ink(0.22)}
              strokeWidth={1.2}
              strokeDasharray="6 7"
            />
            <text x={0} y={262} textAnchor="middle" fontFamily={MONO} fontSize={14} fill={ink(0.4)} letterSpacing={1}>
              YOU · PRESENTER
            </text>
          </g>
        </svg>
      )}
      {line && lineK > 0 && (
        <div
          style={{
            position: "absolute",
            left: on > 0.5 ? 80 : 0,
            width: on > 0.5 ? 600 : 1920,
            bottom: 34,
            textAlign: "center",
            fontFamily: SERIF,
            fontStyle: "italic",
            fontSize: 25,
            lineHeight: "32px",
            color: ink(0.55 * lineK),
          }}
        >
          <span style={{ fontFamily: MONO, fontStyle: "normal", fontSize: 12, letterSpacing: 1, marginRight: 12, color: ink(0.35 * lineK) }}>
            {String(line.id).padStart(2, "0")}/{LINES.length}
          </span>
          “{line.text}”
        </div>
      )}
    </>
  );
};
