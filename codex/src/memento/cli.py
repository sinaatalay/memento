"""Run local experiments and the dashboard without deployment."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from .settings import Settings


def build_runtime(settings: Settings, *, use_gbrain=False):
    from .providers import JevEvaluator
    from .runtime import Runtime
    from .storage import Store
    from .telegram import Telegram
    from .writer import MemoryWriter

    store = Store(settings.data_dir / "memento.sqlite3")
    evaluator = JevEvaluator(api_key=settings.jev_api_key) if settings.jev_api_key else None
    writer = MemoryWriter(api_key=settings.river_api_key, model_name=settings.river_model) if settings.river_api_key else None
    telegram = Telegram(settings.telegram_token, store, settings.telegram_chat_id) if settings.telegram_token else None
    brain = None
    if use_gbrain or settings.gbrain_enabled:
        from .gbrain import GBrain
        bun = shutil.which("bun")
        entry = settings.gbrain_checkout / "src/cli.ts"
        if not bun or not entry.exists():
            raise RuntimeError("GBrain checkout or Bun is missing; see research/gbrain.md")
        brain = GBrain([bun, str(entry)], settings.gbrain_home)
    return Runtime(settings, store, evaluator=evaluator, writer=writer, brain=brain, telegram=telegram)


def main():
    parser = argparse.ArgumentParser(prog="memento", description="Memories that know when to come back")
    parser.add_argument("--env-file", help="Private dotenv file; defaults to workspace .context/memento/credentials.env")
    parser.add_argument("--data-dir", type=Path, help="Override private local runtime directory")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the local dashboard and event loop")
    serve.add_argument("--port", type=int, default=8877)
    serve.add_argument("--gbrain", action="store_true", help="Read and write GBrain memory pages")
    serve.add_argument("--google", action="store_true", help="Also sync the already-authorized Google source")
    collect_cmd = sub.add_parser("collect", help="Validate and inspect a recipe or Markdown memory")
    collect_cmd.add_argument("path", type=Path)
    sub.add_parser("doctor", help="Report configuration and check provider connections")
    args = parser.parse_args()
    if args.command == "collect":
        from .collector import collect, collect_markdown
        source = args.path.read_text()
        result = collect_markdown(source) if args.path.suffix == ".md" else collect(source)
        print(result.model_dump_json(indent=2))
        return
    settings = Settings.from_env(args.env_file)
    if args.data_dir:
        settings.data_dir = args.data_dir.resolve()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.data_dir.chmod(0o700)
    if args.command == "doctor":
        asyncio.run(doctor(settings))
        return
    if args.google:
        settings.gbrain_enabled = True
    runtime = build_runtime(settings, use_gbrain=args.gbrain)
    if runtime.telegram and not runtime.telegram.chat_id:
        print(f"Pair your Telegram bot by sending: /start {runtime.telegram.pair_code}")
    print(f"Memento → http://127.0.0.1:{args.port}")
    print(f"Private local state: {settings.data_dir}")
    from .app import create_app
    import uvicorn
    uvicorn.run(create_app(runtime), host="127.0.0.1", port=args.port, log_level="warning")


async def doctor(settings: Settings):
    report = {"jev": "missing key", "river": "missing key", "telegram": "missing token", "gbrain_checkout": settings.gbrain_checkout.exists(), "data_dir": str(settings.data_dir)}
    if settings.jev_api_key:
        from .providers import JevEvaluator, memory_gate
        try:
            result = await JevEvaluator(settings.jev_api_key).decide({"event": {"text": "Confirmed: UA123 departs SFO September 28 at 08:05."}}, {"remember": memory_gate()})
            report["jev"] = {"connected": True, "elapsed_ms": result.elapsed_ms, "probability": result.answers["remember"].probability}
        except Exception as exc:
            report["jev"] = {"connected": False, "error_type": type(exc).__name__}
    if settings.river_api_key:
        from .providers import RiverConnection
        from pydantic_ai import Agent
        connection = RiverConnection(settings.river_api_key, settings.river_model)
        try:
            result = await Agent(connection.model).run("Reply with the single word connected.")
            report["river"] = {"connected": bool(result.output)}
        except Exception as exc:
            report["river"] = {"connected": False, "error_type": type(exc).__name__}
        finally:
            await connection.close()
    if settings.telegram_token:
        from .telegram import Telegram
        from .storage import Store
        store = Store(settings.data_dir / "memento.sqlite3")
        bot = Telegram(settings.telegram_token, store, settings.telegram_chat_id)
        try:
            await bot.connect()
            report["telegram"] = {"connected": True, "username": bot.username, "paired": bool(bot.chat_id)}
        except Exception as exc:
            report["telegram"] = {"connected": False, "error_type": type(exc).__name__}
        finally:
            await bot.close()
            store.close()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
