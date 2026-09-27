import yaml

from memento.gbrain import parse, with_recipe

PAGE = """---
type: note
title: Flight to NYC
recipe: |
  from memento import at

  x = at("2026-09-28 08:05")
source_kind: put_page
tags:
  - travel
---

# Flight to NYC

UA123 on Monday.
"""

NEW = 'from memento import at, notify\n\n@at("2026-09-28 05:05")\ndef leave():\n    notify("Go.")\n'


def front(markdown):
    return yaml.safe_load(markdown.split("---\n")[1])


def test_parse_reads_title_recipe_and_body():
    stored = parse("trips/nyc", PAGE)
    assert stored.page.title == "Flight to NYC"
    assert stored.page.text == "# Flight to NYC\n\nUA123 on Monday."
    assert stored.recipe.startswith("from memento import at\n")


def test_with_recipe_replaces_only_the_recipe():
    updated = with_recipe(PAGE, NEW)
    fm = front(updated)
    assert fm["recipe"] == NEW
    assert (
        fm["tags"] == ["travel"]
        and fm["source_kind"] == "put_page"
        and fm["title"] == "Flight to NYC"
    )
    assert updated.endswith("# Flight to NYC\n\nUA123 on Monday.\n")


def test_with_recipe_removes_it_when_empty():
    updated = with_recipe(PAGE, "")
    assert "recipe" not in front(updated)
    assert front(updated)["tags"] == ["travel"]


def test_with_recipe_adds_one_to_a_plain_page():
    plain = "---\ntitle: Dentist\n---\n\nThursday 3:30pm.\n"
    assert front(with_recipe(plain, NEW)) == {"title": "Dentist", "recipe": NEW}


def test_ntfy_push_is_utf8_json():
    import json

    from memento.deliver import push_request

    request = push_request(
        "https://ntfy.sh/memento-demo", "Priya Raman — hiring", "Intro for Priya? «Alex»"
    )
    assert request.full_url == "https://ntfy.sh/"
    assert json.loads(request.data) == {
        "topic": "memento-demo",
        "title": "Priya Raman — hiring",
        "message": "Intro for Priya? «Alex»",
        "tags": ["brain"],
    }
