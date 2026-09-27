from pathlib import Path

import yaml

from memento.collect import collect
from memento.gbrain import page_markdown, read_page

EXAMPLE = Path(__file__).parents[1] / "examples" / "acme-soc2.md"


def test_example_page_recipe_collects():
    fm, _ = read_page(EXAMPLE)
    recipe = collect(fm["recipe"], memory="projects/acme-pilot")
    [trigger] = recipe.triggers
    assert trigger.kind == "event" and trigger.source.value == "email"
    assert [a.kind for a in trigger.actions] == ["alert", "rewrite"]


def test_page_markdown_keeps_every_field_and_the_recipe_block():
    fm, body = read_page(EXAMPLE)
    fm["custom"] = {"nested": [1, 2]}
    text = page_markdown(fm, body + "\n\n## Update\n\nnew")
    again, again_body = read_page_text(text)
    assert again["tags"] == ["acme", "security", "follow-up"]
    assert again["custom"] == {"nested": [1, 2]}
    assert again["recipe"].rstrip("\n") == fm["recipe"].rstrip("\n")
    assert "recipe: |" in text  # literal block, so the file reads as Python
    assert again_body.endswith("## Update\n\nnew")


def read_page_text(text: str):
    _, fm, body = text.split("---\n", 2)
    return yaml.safe_load(fm), body.strip()
