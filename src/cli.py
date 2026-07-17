"""CLI entry point for SuperWeb Testing."""

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console

console = Console()
app = typer.Typer(
    name="superweb",
    help="AI-driven E2E web app testing pipeline",
    add_completion=True,
)


@app.command()
def run(
    source: str = typer.Option(
        "", "--source", "-s",
        help="Path to target web app source code",
    ),
    target: str = typer.Option(
        "", "--target", "-t",
        help="URL of target web app",
    ),
    config: Path = typer.Option(
        Path("config.yaml"), "--config", "-c",
        help="Path to config.yaml",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Only analyze source, don't run browser tests",
    ),
):
    """Run the full testing pipeline."""
    async def main():
        from src.pipeline import Pipeline

        p = Pipeline(str(config))

        if dry_run:
            console.print("[yellow]Dry run: source analysis only[/yellow]")
            schemas = await p.phase1_analyze(source)
            console.print(f"Found {len(schemas)} form schemas")
            return

        report = await p.run(source_override=source or None, target_override=target or None)
        console.print(f"\n[bold]Pipeline complete.[/bold] Report in logs/correlation_report.json")

    asyncio.run(main())


@app.command()
def analyze(
    source: str = typer.Option(
        "", "--source", "-s",
        help="Path to target web app source code",
    ),
    config: Path = typer.Option(
        Path("config.yaml"), "--config", "-c",
        help="Path to config.yaml",
    ),
):
    """Phase 1 only: Analyze source code for form schemas."""
    async def main():
        from src.source_analyzer import SourceAnalyzer
        from src.pipeline import Pipeline as PipelineClass

        p = PipelineClass(str(config))
        schemas = await p.phase1_analyze(source)

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
    config: Path = typer.Option(
        Path("config.yaml"), "--config", "-c",
        help="Path to config.yaml",
    ),
):
    """Phase 2 only: Generate test data from schemas."""
    async def main():
        from src.data_generator import DataGenerator
        from src.pipeline import Pipeline as PipelineClass

        schemas = __import__("json").loads(schemas_file.read_text())
        p = PipelineClass(str(config))
        llm_cfg = p.config.get("llm", {})

        gen = DataGenerator(
            llm_base_url=llm_cfg.get("base_url", "http://172.25.0.1:8080"),
            model=llm_cfg.get("model", "Qwen3.6-27B"),
        )
        try:
            dataset = await gen.generate(schemas)
        except Exception as e:
            console.print(f"[yellow]LLM unavailable ({e}), using fallback[/yellow]")
            dataset = gen.generate_fallback(schemas)
        finally:
            await gen.close()

        gen.save(dataset, str(schemas_file.parent / "test_data.json"))
        console.print(f"Generated {len(dataset.records)} test records → {schemas_file.parent / 'test_data.json'}")

    asyncio.run(main())


@app.command()
def serve():
    """Start the web dashboard for viewing test results."""
    console.print("[yellow]Dashboard coming in next iteration[/yellow]")


def main():
    app()


if __name__ == "__main__":
    main()