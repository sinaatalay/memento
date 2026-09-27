---
type: note
title: Northwind early exercise
recipe: |
  from memento import when, notify, update

  # The 30-day clock starts at the exercise, so wait for it: update(news)
  # has River turn "within 30 days" into dated, conditional reminders.
  @when(
      "the early exercise of the Northwind options was completed",
      unless="it is only submitted, pending approval, or being planned",
  )
  def exercised(news):
      notify("Northwind exercise is done. The 83(b) election must reach the "
             "IRS within 30 days, no extensions.")
      update(news)
---

# Northwind early exercise

I'm early-exercising my 40,000 Northwind options. My lawyer was very clear:
the 83(b) election has to reach the IRS within 30 days of the exercise date.
No extensions, and missing it is very expensive.
