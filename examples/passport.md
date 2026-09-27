---
type: note
title: Passport renewal
recipe: |
  from memento import at, when, notify, update, this, now, days

  trip = at("2027-03-15 09:00")
  apply_by = trip - days(70)  # renewals take 6-8 weeks

  @at(apply_by - days(14))
  def start_renewal():
      if not this.says("the passport renewal was already submitted"):
          left = (apply_by - now()).days
          notify(f"Start your passport renewal: {left} days left to apply for Tokyo.")

  @when("the passport renewal application was submitted", until=trip)
  def submitted(news):
      update(news)
---

# Passport renewal

My passport expires in January 2027. The Tokyo trip is mid-March 2027, and
renewals take 6-8 weeks.
