---
type: lesson
title: "Lesson: the March outage"
recipe: |
  from memento import when, notify

  @when(
      "a database migration is scheduled for a Friday, or for the day before "
      "a launch",
      unless="the migration already ran, or it is not a database change",
  )
  def not_on_a_friday(news):
      notify("A DB migration before a weekend or a launch cost us a weekend in "
             "March. Move it to a Tuesday morning.")
---

# Lesson: the March outage

Post-mortem lesson: never run a database migration on a Friday or right before
a launch. We lost a whole weekend in March.
