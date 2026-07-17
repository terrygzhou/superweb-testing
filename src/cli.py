"""CLI entry point for SuperWeb Testing."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import typer
from rich.console import Console

console = Console()
app = typer.Typer(
    name="superweb",
    help="AI-driven E2E web app testing pipeline",
    add_completion=True,
)


def resolve_source(source: str, output: str) -> str:
    """If source is a git URL, clone it to output/source; return local path."""
    if source.startswith("https://") or source.startswith("git@"):
        dest = Path(output) / "source"
        dest.mkdir(parents=True, exist_ok=True)
        console.print(f"[yellow]Cloning: {source} → {dest}[/yellow]")
        subprocess.run(["git", "clone", source, str(dest)], check=True)
        return str(dest)
    return source


# --- Commands ---


@app.command()
def run(
    target: str = typer.Option(
        "", "--target", "-t",
        help="URL of the target webapp (e.g. http://localhost:8081)",
    ),
    source: str = typer.Option(
        "", "--source", "-s",
        help="Local path or git URL of the webapp source code",
    ),
    output: str = typer.Option(
        "./superweb_output", "--output", "-o",
        help="Output/report directory for all artifacts",
    ),
    config: Path = typer.Option(
        None, "--config", "-c",
        help="Optional config.yaml (defaults to self-contained)",
    ),
    llm_url: str = typer.Option(
        "http://172.25.0.1:8080", "--llm-url",
        help="LLM endpoint base URL (OpenAI-compatible)",
    ),
    llm_model: str = typer.Option(
        "Qwen3.6-27B", "--llm-model",
        help="LLM model name",
    ),
    variations: int = typer.Option(
        3, "--variations", "-v",
        help="Test data variations per form (1-5)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Only analyze source, don't run browser tests",
    ),
    mode: str = typer.Option(
        "scripted", "--mode",
        help="Execution mode: scripted (deterministic pipeline) or agent (OpenHands-powered)",
    ),
    agent_workspace: str = typer.Option(
        "", "--agent-workspace",
        help="Host directory to mount into the OpenHands container (agent mode only). "
        "E.g. /home/terry/workspace/projects/openhands_output",
    ),
    agent_timeout: int = typer.Option(
        600, "--agent-timeout",
        help="Agent timeout in seconds (agent mode only, default 600)",
    ),
):
    """Run the full testing pipeline against a webapp."""
    async def run_main():
        from src.pipeline import Pipeline

        if not target:
            console.print("[red]Error: --target URL is required[/red]")
            raise SystemExit(1)
        if not source:
            console.print("[red]Error: --source path/git URL is required[/red]")
            raise SystemExit(1)

        source_path = resolve_source(source, output)

        p = Pipeline(
            config_path=str(config) if config else None,
            output_dir=output,
            target_url=target,
            source_root=source_path,
            llm_url=llm_url,
            llm_model=llm_model,
            n_variations=variations,
            mode=mode,
            agent_workspace=agent_workspace,
            agent_timeout=agent_timeout,
        )

        if dry_run:
            console.print("[yellow]Dry run: source analysis only[/yellow]")
            schemas = await p.phase1_analyze(source_path)
            console.print(f"Found {len(schemas)} form schemas")
            return

        report = await p.run(source_override=source_path, target_override=target)
        console.print(f"\n[bold]Pipeline complete.[/bold] Report: {output}/report/correlation_report.json")

    asyncio.run(run_main())


@app.command()
def analyze(
    source: str = typer.Option(
        "", "--source", "-s",
        help="Local path or git URL of the webapp source code",
    ),
    output: str = typer.Option(
        "./superweb_output", "--output", "-o",
        help="Output directory",
    ),
    config: Path = typer.Option(
        None, "--config", "-c",
    ),
):
    """Phase 1 only: Analyze source code for form schemas."""
    if not source:
        console.print("[red]Error: --source is required[/red]")
        raise SystemExit(1)

    source_path = resolve_source(source, output)

    async def main():
        from src.pipeline import Pipeline

        p = Pipeline(
            config_path=str(config) if config else None,
            output_dir=output,
            source_root=source_path,
        )
        schemas = await p.phase1_analyze(source_path)

        console.print(f"\n[bold]Found {len(schemas)} form schemas:[/bold]")
        for s in schemas:
            form = s.get("form_name", "Unknown")
            fields = s.get("fields", [])
            console.print(f"  • {form}: {len(fields)} fields")

    asyncio.run(main())


@app.command()
def generate(
    schemas_file: Path = typer.Option(
        Path("data/schemas.json"), "--schemas",
        help="Path to schemas.json",
    ),
    output: str = typer.Option(
        "./superweb_output", "--output", "-o",
        help="Output directory for test data",
    ),
    llm_url: str = typer.Option(
        "http://172.25.0.1:8080", "--llm-url",
    ),
    llm_model: str = typer.Option(
        "Qwen3.6-27B", "--llm-model",
    ),
    variations: int = typer.Option(
        3, "--variations", "-v",
    ),
):
    """Phase 2 only: Generate test data from schemas."""
    async def main():
        from src.data_generator import DataGenerator

        schemas = __import__("json").loads(schemas_file.read_text())

        gen = DataGenerator(
            llm_base_url=llm_url,
            model=llm_model,
            n_variations=variations,
        )
        try:
            dataset = await gen.generate(schemas)
        except Exception as e:
            console.print(f"[yellow]LLM unavailable ({e}), using fallback[/yellow]")
            dataset = gen.generate_fallback(schemas)
        finally:
            await gen.close()

        out_path = Path(output) / "data" / "test_data.json"
        gen.save(dataset, str(out_path))
        console.print(f"Generated {len(dataset.records)} test records → {out_path}")

    asyncio.run(main())


# --- OpenHands container management ---


@app.command(name="openhands-start")
def openhands_start():
    """Start the OpenHands Agent Server container."""
    subprocess.run(["docker", "compose", "up", "-d"], check=True)
    console.print("[green]OpenHands container started on port 3005[/green]")


@app.command(name="openhands-stop")
def openhands_stop():
    """Stop the OpenHands Agent Server container."""
    subprocess.run(["docker", "compose", "down"], check=False)
    console.print("[yellow]OpenHands container stopped[/yellow]")


@app.command(name="openhands-status")
def openhands_status():
    """Check OpenHands container status."""
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        console.print(result.stdout)
    else:
        console.print("[red]OpenHands container not running[/red]")


def main():
    app()


if __name__ == "__main__":
    main()