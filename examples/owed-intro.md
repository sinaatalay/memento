---
type: person
title: Maria Santos
recipe: |
  from memento import at, when, notify, update, this

  friday = at("2026-10-02 16:00")

  @at(friday)
  def nudge_maria():
      if not this.says("Maria Santos sent the intro to her friend at Stripe"):
          notify("Maria offered an intro to her Stripe infra friend this week. "
                 "It hasn't come; a short nudge is fine.")

  @when("Maria Santos introduced me to her friend at Stripe", until=friday)
  def intro_arrived(news):
      notify("Maria's Stripe intro landed. Reply today while it's warm.")
      update(news)
---

# Maria Santos

Maria Santos (CTO at Pilot) offered to intro me to her friend who runs
infrastructure at Stripe. She said she'd send it this week.
