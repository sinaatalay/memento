---
type: event
title: Flight to NYC
tags: [travel]
recipe: |
  from memento import at, when, notify, update, hours

  flight = at("2026-09-28 08:05")

  @at(flight - hours(12))
  def early_flight_tomorrow():
      notify("UA123 leaves SFO at 8:05 tomorrow. Early night, bag by the door.")

  @at(flight - hours(3))
  def leave_for_the_airport():
      notify("Time to leave for SFO. UA123 to JFK departs at 8:05.")

  @when(
      "flight UA123 on Monday, September 28 is delayed, rescheduled or cancelled",
      unless="it only confirms the booking, the seat or offers an upgrade",
      until=flight,
  )
  def flight_changed(news):
      if news.says("flight UA123 was cancelled"):
          notify("UA123 was cancelled. Rebook now.")
      else:
          notify(f"UA123 changed: {news.title}")
      update(news)
---

# Flight to NYC

Flying UA123 SFO to JFK on Monday, September 28, departing 8:05am.
Seat 14C, confirmation K7Q2LM.
