---
title: Monday flight to New York
sources:
  - demo/flight-confirmation
recipe: |
  from memento import at, hours, remind, expires
  from memento import on, email, chat, noul, alert, surface, rewrite, this

  departure = at("2026-09-28 11:00", tz="America/Los_Angeles")
  remind(departure - hours(3), "Your UA123 flight leaves at 11am today.")
  on(
      email,
      when=noul("Does this change or cancel my UA123 flight on Sep 28?"),
      do=[alert("Your flight plans changed."), rewrite(this)],
      threshold=0.85,
  )
  on(
      chat,
      when=noul("Does this involve plans on Sep 28 before noon in SF?"),
      do=surface(this),
      threshold=0.8,
  )
  expires(departure + hours(12))
---

# Monday flight to New York

This is a demo memory. Flight UA123 departs San Francisco for New York at
11:00am Pacific on Monday, September 28, 2026. Leave time for the airport.
Any proposed morning plans should account for this flight.
