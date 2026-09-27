---
type: project
title: Acme pilot
recipe: |
  from memento import when, notify, update

  @when(
      "our SOC 2 Type I report has been issued",
      unless="the audit is only scheduled, planned or in progress",
  )
  def soc2_ready(news):
      notify("SOC 2 is out. Send it to Dan Kim at Acme today; it unblocks the pilot.")
      update(news)

  @when("Dan Kim at Acme writes about the pilot")
  def dan_writes(news):
      mood = news.rate("How frustrated is Dan?", ["calm", "impatient", "escalating"])
      if mood == 2:
          notify("Dan at Acme is escalating about the pilot. Call him today.")
      elif mood == 1:
          notify("Dan is getting impatient about the pilot. Send him a status.")
---

# Acme pilot

Dan Kim (Acme security) can't sign the pilot until he has our SOC 2 Type I
report. I promised to send it the day it's issued.
