import { Audio } from "@remotion/media";
import React from "react";
import { AbsoluteFill, staticFile } from "remotion";
import { Backdrop, Grain } from "./Backdrop";
import { Chat } from "./Chat";
import { Checks } from "./Checks";
import { Issue } from "./Issue";
import { Notification } from "./Notification";
import { Page } from "./Page";
import { Presenter } from "./Presenter";
import { Verbs } from "./Verbs";
import { Wordmark } from "./Wordmark";

export type FilmProps = { guides: boolean };

/** Layer order, back to front. Each scene reads the clock in ../story.ts and draws itself. */
export const Film: React.FC<FilmProps> = ({ guides }) => (
  <AbsoluteFill>
    <Backdrop />
    <Issue />
    <Chat />
    <Checks />
    <Page />
    <Verbs />
    <Wordmark />
    <Notification />
    <Presenter guides={guides} />
    <Grain />
    <Audio src={staticFile("audio/score.wav")} />
  </AbsoluteFill>
);
