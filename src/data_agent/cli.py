"""Command line: ask questions, start a local dev database, serve the API.

data-agent ask "Which customers are at risk of churning?"
data-agent ask "..." --provider gemini --json
data-agent dev-db        # local PostgreSQL with demo data, no Docker needed
data-agent serve         # FastAPI on :8000
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from .agent import StepTrace
from .llm import PROVIDERS, LLMError

console = Console()
ROOT = pathlib.Path(__file__).resolve().parents[2]
SQL_PREVIEW_CHARS = 320


def _show_step(step: StepTrace) -> None:
    icon = "[green]✓[/]" if step.ok else "[red]✗[/]"
    args = step.arguments or {}
    label = args.get("purpose") or args.get("table") or args.get("name") or args.get("workflow") or ""
    console.print(f" {icon} [bold]{step.tool}[/] [dim]{label}[/] [dim]({step.duration_ms} ms)[/]")
    sql = step.detail.get("sql") or (args.get("sql") if step.tool == "run_sql" else None)
    if sql:  # also show the attempted SQL when it failed; full SQL is in --json / the API
        shown = sql if len(sql) <= SQL_PREVIEW_CHARS else sql[:SQL_PREVIEW_CHARS].rstrip() + " …"
        console.print(Syntax(shown, "sql", theme="ansi_dark", word_wrap=True, padding=(0, 3)))
    if step.error:
        console.print(f"   [red]{step.error[:300]}[/]")


def cmd_ask(args: argparse.Namespace) -> int:
    from .config import Settings, build_agent

    settings = Settings.from_env()
    if args.save_svg:  # record everything printed, to export a real-run screenshot
        console.record = True
        console.width = args.width
    agent = build_agent(settings, args.provider, args.model, on_step=None if args.json else _show_step)
    if not args.json:
        console.print(
            Panel(args.question, title=f"[bold]{agent.llm.provider} · {agent.llm.model}[/]", border_style="cyan")
        )
    try:
        result = agent.ask(args.question)
    except LLMError as e:
        console.print(f"[red]LLM error:[/] {e}")
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0
    console.print(Panel(Markdown(result.answer), title="Answer", border_style="green"))
    cost = f" · ${result.cost_usd:.4f}" if result.cost_usd is not None else ""
    console.print(
        f"[dim]{len(result.steps)} tool calls · {result.usage.input_tokens:,} in / "
        f"{result.usage.output_tokens:,} out tokens{cost} · {result.duration_ms / 1000:.1f}s · "
        f"stop={result.stop}[/]"
    )
    if args.save_svg:
        console.save_svg(args.save_svg, title="data-agent ask")
        print(f"Saved {args.save_svg}")
    return 0


def cmd_dev_db(args: argparse.Namespace) -> int:
    """Start a local PostgreSQL (pgserver), load schema + seed, print the URLs. For machines without Docker."""
    try:
        import pgserver
    except ImportError:
        console.print('[red]pgserver is not installed:[/] pip install -e ".[dev]"')
        return 1
    import psycopg

    data_dir = pathlib.Path(args.data_dir).resolve()
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    server_url = server.get_uri()
    base = server_url.rsplit("/", 1)[0]
    with psycopg.connect(server_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'analytics'").fetchone()
        if exists and not args.reset:
            console.print("[yellow]Database 'analytics' already exists[/] (use --reset to recreate it)")
        else:
            conn.execute("DROP DATABASE IF EXISTS analytics WITH (FORCE)")
            conn.execute("CREATE DATABASE analytics")
            with psycopg.connect(f"{base}/analytics", autocommit=True) as db:
                for f in ["sql/schema/01_schema.sql", "sql/seed/01_seed.sql", "sql/schema/02_agent_readonly_role.sql"]:
                    with console.status(f"Loading {f}"):
                        db.execute((ROOT / f).read_text(encoding="utf-8"))
            console.print("[green]Demo database loaded.[/]")
    agent_url = base.replace("postgres:@", "agent_readonly:@").replace("postgres@", "agent_readonly@") + "/analytics"
    console.print(f"\nAdd this to your .env:\n[bold]AGENT_DATABASE_URL={agent_url}[/]\n")
    console.print(
        "[dim]The server keeps running in the background (the port can change after a reboot); data lives in "
        f"{data_dir}. Run this command again after a reboot.[/]"
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("data_agent.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-agent", description="Ask business questions to your SQL database.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask a question")
    ask.add_argument("question")
    ask.add_argument("--provider", choices=PROVIDERS, help="Override LLM_PROVIDER")
    ask.add_argument("--model", help="Override LLM_MODEL")
    ask.add_argument("--json", action="store_true", help="Print the full result (trace, SQL, usage) as JSON")
    ask.add_argument("--save-svg", metavar="PATH", help="Also save the terminal output as an SVG screenshot")
    ask.add_argument("--width", type=int, default=110, help="Terminal width used for --save-svg")
    ask.set_defaults(func=cmd_ask)

    dev = sub.add_parser("dev-db", help="Start a local demo PostgreSQL without Docker")
    dev.add_argument("--data-dir", default=".pgdata")
    dev.add_argument("--reset", action="store_true", help="Drop and reload the demo data")
    dev.set_defaults(func=cmd_dev_db)

    serve = sub.add_parser("serve", help="Run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
