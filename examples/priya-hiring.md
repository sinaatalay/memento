---
type: person
title: Priya Raman
recipe: |
  from memento import when, notify

  @when(
      "an experienced product designer is looking for a new role",
      unless="it is a job post, or the designer is not available",
  )
  def possible_intro(news):
      notify(f"Intro for Priya? \"{news.title}\" fits her founding designer role.")
---

# Priya Raman

Partner at Northwind VC, leaving to start a company in SF. She is hiring a
founding product designer; I said I'd keep an eye out.
