---
type: company
title: "Competitor: Linear"
recipe: |
  from memento import when, notify, update

  @when(
      "Linear launched native time tracking",
      unless="it is a rumor, a roadmap item, a waitlist, or a third-party plugin",
  )
  def wedge_gone(news):
      notify("Linear shipped native time tracking. Our agency wedge is gone: "
             "rework the battlecard before the next agency demo.")
      update(news)
---

# Competitor: Linear

Battlecard: Linear has no native time tracking. That's our main wedge with
agencies; lead every agency demo with our time tracking.
