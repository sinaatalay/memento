"""memento: proactive memory for GBrain.

Any GBrain page can carry a `recipe:` in its frontmatter, a small Python module
that says when the memory should come back on its own. This package is both
the language recipes import and the runtime that runs them.

    from memento import at, when, notify, update, this, now, fetch
    from memento import minutes, hours, days, weeks
"""

from .api import (
    Page,
    RecipeError,
    at,
    days,
    fetch,
    hours,
    minutes,
    notify,
    now,
    this,
    update,
    weeks,
    when,
)

__all__ = [
    "Page",
    "RecipeError",
    "at",
    "days",
    "fetch",
    "hours",
    "minutes",
    "notify",
    "now",
    "this",
    "update",
    "weeks",
    "when",
]
