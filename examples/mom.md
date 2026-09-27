---
type: person
title: Mom
recipe: |
  from memento import at, notify, days, weeks

  birthday = at("2026-10-12 09:00")

  @at("2026-10-04 18:00", every=weeks(1))
  def sunday_call():
      notify("Sunday evening: call Mom.")

  @at(birthday - days(5))
  def order_the_gift():
      notify("Mom's birthday is Monday the 12th. Order the new Murakami now.")

  @at(birthday)
  def birthday_today():
      notify("It's Mom's birthday. Call her this morning.")
---

# Mom

Call her every Sunday evening. Her birthday is October 12; she's been wanting
the new Murakami novel.
