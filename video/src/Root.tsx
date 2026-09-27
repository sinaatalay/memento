import React from "react";
import { Composition } from "remotion";
import "./fonts";
import { Film } from "./film/Film";
import { DURATION } from "./story";
import { FPS, H, W } from "./theme";

export const Root: React.FC = () => (
  <>
    <Composition id="Memento" component={Film} durationInFrames={Math.round(DURATION * FPS)} fps={FPS} width={W} height={H} defaultProps={{ guides: true }} />
    <Composition id="MementoClean" component={Film} durationInFrames={Math.round(DURATION * FPS)} fps={FPS} width={W} height={H} defaultProps={{ guides: false }} />
  </>
);
