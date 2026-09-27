---
type: decision
title: "Decision: stay on one Postgres"
recipe: |
  from memento import when, notify, update

  @when(
      "our Postgres write volume is sustained above 5,000 writes per second",
      unless="it is a benchmark, a projection, or a one-off spike",
  )
  def writes_too_high(news):
      notify("Writes are past 5k/sec. Time to revisit the single-Postgres call.")
      update(news)

  @when(
      "a customer requires their data to be stored in the EU",
      unless="it is a general question about GDPR, or a prospect just asking",
  )
  def eu_residency(news):
      notify("A customer needs EU data residency. The single-Postgres decision "
             "assumed none would.")
      update(news)
---

# Decision: stay on one Postgres

Decided (Sep 2026) to stay on a single Postgres instance instead of sharding.
Revisit if sustained writes go above 5k/sec, or if a customer needs EU data
residency.
