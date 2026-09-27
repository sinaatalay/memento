---
title: Priya is hiring a founding designer
sources:
  - demo/friend-conversation
recipe: |
  from memento import on, email, chat, noul, alert, surface, this

  candidate = noul(
      "Is an experienced product designer now seeking a founding role?",
      true="A specific designer is available for a founding design role.",
      false="General design discussion or someone who is not available.",
  )
  on(
      email,
      when=candidate,
      do=alert("A possible designer introduction for Priya came up."),
      threshold=0.9,
  )
  on(chat, when=candidate, do=surface(this), threshold=0.85)
---

# Priya is hiring a founding designer

This is a demo memory. Priya is hiring an experienced founding product
designer for an early-stage startup in San Francisco. If someone suitable
becomes available, suggest an introduction and let me decide whether to
send it.
