"""Ordinary Markdown remains ordinary when a recipe is added or rewritten."""
import pytest

from memento.memory import parse_memory, render_memory, split_markdown


def test_plain_markdown_is_passive_and_uses_its_heading():
    memory = parse_memory("# Travel preferences\n\nI prefer aisle seats.\n")
    assert memory == {
        "title": "Travel preferences", "body": "# Travel preferences\n\nI prefer aisle seats.",
        "recipe": "", "sources": [],
    }


def test_optional_null_recipe_and_sources_are_passive():
    memory = parse_memory("---\nrecipe: null\nsources: null\n---\n# Ordinary note\n")
    assert memory["recipe"] == "" and memory["sources"] == []


@pytest.mark.parametrize("value", ["false", "5", "[]", "{}"])
def test_non_string_recipe_is_rejected(value):
    with pytest.raises(ValueError, match="Python source string"):
        parse_memory(f"---\nrecipe: {value}\n---\nA note")


def test_rewrite_preserves_other_agents_metadata_and_markdown_dividers():
    original = """---
title: Supplier deadline
type: project
tags: [work, acme]
custom:
  owner: human
  review: true
recipe: ''
---
# Supplier deadline

Send Friday.

---

## Context
Keep this explanation.
"""
    before = parse_memory(original)
    changed = render_memory(
        "Supplier deadline", before["body"].replace("Friday", "Monday"),
        'from memento import remind, at\nremind(at("2027-01-01T12:00Z"), "Send proposal")',
        ["chat:change"], original_markdown=original,
    )
    metadata, body = split_markdown(changed)
    assert metadata["type"] == "project"
    assert metadata["tags"] == ["work", "acme"]
    assert metadata["custom"] == {"owner": "human", "review": True}
    assert body == before["body"].replace("Friday", "Monday")
    assert parse_memory(changed)["sources"] == ["chat:change"]


def test_frontmatter_separator_must_occupy_a_whole_line():
    with pytest.raises(ValueError, match="closing"):
        parse_memory("---\ntitle: example\n---not-a-separator\nbody")
