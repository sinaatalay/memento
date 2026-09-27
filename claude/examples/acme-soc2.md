---
type: project
title: Acme pilot
tags: [acme, security, follow-up]
people: [Dan Kim (Acme security)]
recipe: |
  from memento import on, email, noul, alert, rewrite, this

  on(
      email,
      when=noul(
          "Does `email` say our SOC 2 report has been issued?",
          yes="The report is finished and can be shared now.",
          no="A plan, a schedule, fieldwork, or anything not done.",
      ),
      do=[alert("SOC 2 is out. Send it to Dan at Acme today."), rewrite(this)],
  )
---
# Acme pilot

Acme's pilot is blocked on security review. Dan Kim (Acme security) needs our
SOC 2 report before he can approve it. I promised to send it the day it's issued.

Pricing: $18k/yr agreed with their CFO; keep this note as the pilot's home.
