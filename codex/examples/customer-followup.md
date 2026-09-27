---
title: Acme is waiting for SSO
sources:
  - demo/customer-meeting
recipe: |
  from memento import on, email, noul, alert, rewrite, this

  on(
      email,
      when=noul(
          "Does this announce that SSO is now shipped and available?",
          true="SSO is available to customers, clearing Acme's blocker.",
          false="A plan, estimate, unrelated feature, or unfinished SSO.",
      ),
      do=[alert("SSO shipped. Follow up with Maya at Acme."), rewrite(this)],
      threshold=0.85,
  )
---

# Acme is waiting for SSO

This is a demo memory. Maya at Acme wants to start a pilot, but SSO is a
prerequisite. I promised to follow up when it ships. A release plan does not
clear the blocker; SSO must actually be available to customers.
