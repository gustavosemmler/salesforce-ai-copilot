"""Command line entry point: `sf-copilot serve | index | chat`."""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_serve(_args) -> None:
    from .mcp_server import main

    main()


def cmd_index(args) -> None:
    from .mcp_server import runtime
    from .rag import fetch_apex_sources

    print("Fetching Apex classes and triggers from the org...")
    sources = fetch_apex_sources(runtime.sf)
    print(f"Found {len(sources)} files. Embedding and indexing (first run downloads a small model)...")
    stats = runtime.index.build(sources, reset=not args.append)
    print(f"Done: {stats['files']} files -> {stats['chunks']} chunks stored in {runtime.settings.chroma_path}")


def cmd_chat(_args) -> None:
    import anthropic

    from .agent import CopilotAgent, format_tool_call
    from .mcp_server import runtime, server

    agent = CopilotAgent(
        server=server,
        client=anthropic.AsyncAnthropic(),
        model=runtime.settings.anthropic_model,
        on_tool_call=lambda name, args: print(format_tool_call(name, args)),
    )
    print("Salesforce AI Copilot. Ask anything about your org. Type 'exit' to quit.\n")

    async def loop():
        while True:
            try:
                question = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if question.lower() in {"exit", "quit", "sair"}:
                return
            if not question:
                continue
            answer = await agent.ask(question)
            print(f"\ncopilot> {answer}\n")

    asyncio.run(loop())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sf-copilot", description="AI copilot for Salesforce orgs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run the MCP server over stdio").set_defaults(func=cmd_serve)
    p_index = sub.add_parser("index", help="Index the org's Apex code for semantic search")
    p_index.add_argument("--append", action="store_true", help="Add to the existing index instead of rebuilding")
    p_index.set_defaults(func=cmd_index)
    sub.add_parser("chat", help="Chat with the copilot in the terminal").set_defaults(func=cmd_chat)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # friendly message instead of a stack trace for setup errors
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
