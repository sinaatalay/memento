import React from "react";
import { Audio } from "@remotion/media";
import { AbsoluteFill, staticFile } from "remotion";
import { Backdrop, Grain } from "./parts/Backdrop";
import { Notification } from "./parts/Notification";
import { Page } from "./parts/Page";
import { Presenter } from "./parts/Presenter";
import { World } from "./world/World";

export type MementoProps = { guides: boolean };

export const Memento: React.FC<MementoProps> = ({ guides }) => (
  <AbsoluteFill>
    <Backdrop />
    <World />
    <Page />
    <Notification />
    <Presenter guides={guides} />
    <Grain />
    <Audio src={staticFile("audio/score.wav")} />
  </AbsoluteFill>
);
