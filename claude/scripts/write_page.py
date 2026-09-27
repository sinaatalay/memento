"""Write an ordinary GBrain page the way an agent would (gbrain put), e.g. a page with a recipe.

    uv run python scripts/write_page.py examples/acme-soc2.md projects/acme-pilot
"""

import asyncio
import sys
from pathlib import Path

from memento import gbrain


async def main(file: str, slug: str, source: str = "default") -> None:
    await gbrain.put_page(source, slug, Path(file).read_text())
    print(f"wrote {source}:{slug}")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
