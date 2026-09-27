---
type: note
title: MacBook Pro
recipe: |
  from memento import at, when, notify

  @when(
      "my 14-inch MacBook Pro was damaged, dropped, spilled on, or stopped "
      "working",
      unless="it is someone else's laptop, or news about new MacBooks",
      until=at("2029-09-10 09:00"),
  )
  def covered(news):
      notify("Your MacBook Pro has AppleCare+ with accidental damage until "
             "Sep 2029. Don't pay for the repair: book the Genius Bar.")
---

# MacBook Pro

Bought a 14-inch MacBook Pro on Sep 10, 2026, with AppleCare+ (accidental
damage included) until September 2029.
