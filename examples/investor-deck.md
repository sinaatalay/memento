---
type: note
title: Investor deck for Priya
recipe: |
  from memento import at, when, notify, update, this, hours

  due = at("2026-09-30 17:00")

  @at(due - hours(8))
  def send_the_deck():
      if not this.says("the investor deck was already sent to Priya"):
          notify("Send Priya the investor deck today. Her partners see it Thu.")

  @when("the investor deck was sent to Priya Raman", until=due)
  def deck_sent(news):
      update(news)

  @when("Priya Raman asks about or chases the investor deck", until=due)
  def priya_asks(news):
      notify("Priya is asking about the deck. You promised it by Wednesday.")
---

# Investor deck for Priya

Priya Raman (Northwind VC) asked for our seed deck. I promised it by Wednesday
end of day so she can share it with her partners on Thursday.
