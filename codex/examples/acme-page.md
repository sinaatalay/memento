---
title: Acme pilot
type: project
tags: [acme, customer]
custom: {owner: existing-assistant}
recipe: |
  from memento import on, email, noul, alert, rewrite, this

  on(
      email,
      when=noul(
          "Does this email confirm our SSO has actually shipped and is "
          "available to customers?",
          true="A concrete release confirmation says our SSO is available "
          "now, satisfying the condition for my Acme follow-up.",
          false="A roadmap announcement, planned or estimated release, "
          "feature request, unfinished implementation, or another "
          "company's SSO release does not confirm our SSO has shipped.",
      ),
      do=[
          alert("SSO has shipped. You promised Maya at Acme a follow-up."),
          rewrite(this),
      ],
      threshold=0.85,
  )
---
# Acme pilot
Maya is evaluating us. Keep the original pricing discussion in this note.

## Follow-up commitment

I promised Maya at Acme a follow-up when our SSO actually ships. Notify me
when a release confirmation establishes that it is available to customers.
A roadmap announcement or planned release does not satisfy this condition.

After confirmed shipping, retire the shipment watch and preserve the
outstanding follow-up commitment until I have handled it. Notifying me does
not mean Maya has received a message or that the follow-up is complete.
