---
type: research
title: "Hypothesis: churn is onboarding"
recipe: |
  from memento import when, notify, update

  @when("a customer explains why they cancelled or nearly cancelled")
  def churn_reason(news):
      reason = news.which(
          "What does the customer give as the main reason?",
          onboarding="setup, onboarding or getting started was too hard",
          price="price, cost or the bill",
          other="missing features, switching tools, or anything else",
      )
      if reason == "price":
          notify(f"Price churn, against your onboarding hypothesis: {news.title}")
      update(news)  # every interview counts for or against the hypothesis
---

# Hypothesis: churn is onboarding

Working hypothesis: our churn is driven by onboarding friction, not price.
Every churn interview should count for or against it. If price comes up as the
reason, I want to know right away.
