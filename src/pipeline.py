"""Pipeline orchestrator — runs all phases end-to-end."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from src.source_analyzer import SourceAnalyzer, FormSchema
from src.data_generator import DataGenerator, TestDataset
from src.test_runner import TestRunner, TestRunResult
from src.log_monitor import LogMonitor, TestEvent

console = Console()


class Pipeline:
    """Orchestrate the full testing pipeline: analyze → generate → run → correlate."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.base_dir = Path(config_path).parent if config_path else Path(".")
        self.artifacts_dir = self.base_dir / self.config.get("pipeline", {}).get(
            "artifacts_dir", "./artifacts"
        )
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Intermediate data
        self.schemas: list[dict] = []
        self.dataset: TestDataset | None = None

    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML."""
        path = Path(config_path)
        if path.exists():
            return yaml.safe_load(path.read_text()) or {}
        return {}

    async def run(self, source_override: str = "", target_override: str = "") -> dict:
        """Run the full pipeline."""
        start_time = time.time()

        console.print("\n[bold cyan]🚀 SuperWeb Testing Pipeline[/bold cyan]")
        console.print("=" * 50)

        # Phase 1: Source Analysis
        console.print("\n[bold green]Phase 1[/bold green]: Source Analysis...")
        schemas = await self.phase1_analyze(source_override)

        # Phase 2: Data Generation
        console.print("\n[bold green]Phase 2[/bold green]: Data Generation...")
        dataset = await self.phase2_generate(schemas)

        # Phase 3: Browser Testing
        console.print("\n[bold green]Phase 3[/bold green]: Browser Testing...")
        results = await self.phase3_test(dataset, target_override)

        # Phase 4: Log Correlation
        console.print("\n[bold green]Phase 4[/bold green]: Log Correlation...")
        report = await self.phase4_correlate(results, start_time)

        # Summary
        elapsed = time.time() - start_time
        console.print(f"\n[bold]{'=' * 50}[/bold]")
        console.print(f"[bold]Pipeline complete[/bold] — {elapsed:.1f}s")

        table = Table(title="Summary")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Forms analyzed", str(len(schemas)))
        table.add_row("Test records", str(len(dataset.records) if dataset else "0"))
        table.add_row(
            "Tests passed/total",
            f"{sum(1 for r in results if r.status == 'passed')}/{len(results)}",
        )
        table.add_row("Correlated errors", str(report.get("summary", {}).get("correlated_errors", 0)))
        table.add_row("Total time", f"{elapsed:.1f}s")
        console.print(table)

        return report

    async def phase1_analyze(self, source_override: str = "") -> list[dict]:
        """Phase 1: Analyze source code for form schemas."""
        source_root = source_override or self.config.get("source", {}).get(
            "root", "~/workspace/projects/loop_factory"
        )
        form_patterns = self.config.get("source", {}).get("form_patterns", [])
        route_patterns = self.config.get("source", {}).get("route_patterns", [])

        analyzer = SourceAnalyzer(
            source_root=source_root,
            form_patterns=form_patterns if form_patterns else None,
        )
        result = analyzer.analyze()

        # Save schemas
        schema_path = self.base_dir / "data" / "schemas.json"
        analyzer.save_results(result, str(schema_path))

        console.print(f"  Found {result.summary['forms_found']} forms, "
                      f"{result.summary['routes_found']} routes, "
                      f"{result.summary['total_fields']} fields")
        console.print(f"  Schemas saved: {schema_path}")

        self.schemas = [s.model_dump() for s in result.forms]
        return self.schemas

    async def phase2_generate(self, schemas: list[dict]) -> TestDataset:
        """Phase 2: Generate test data using LLM."""
        llm_cfg = self.config.get("llm", {})
        n_variations = self.config.get("pipeline", {}).get("data_variations", 3)

        generator = DataGenerator(
            llm_base_url=llm_cfg.get("base_url", "http://172.25.0.1:8080"),
            model=llm_cfg.get("model", "Qwen3.6-27B"),
            n_variations=n_variations,
        )

        try:
            dataset = await generator.generate(schemas)
        except Exception as e:
            console.print(f"  [yellow]LLM unavailable ({e}), using fallback[/yellow]")
            dataset = generator.generate_fallback(schemas)
        finally:
            await generator.close()

        # Save dataset
        data_path = self.base_dir / "data" / "test_data.json"
        generator.save(dataset, str(data_path))

        console.print(f"  Generated {len(dataset.records)} test records "
                      f"({dataset.metadata.get('generator', 'unknown')})")
        console.print(f"  Data saved: {data_path}")

        self.dataset = dataset
        return dataset

    async def phase3_test(
        self, dataset: TestDataset, target_override: str = ""
    ) -> list[TestRunResult]:
        """Phase 3: Run browser tests."""
        browser_cfg = self.config.get("browser", {})
        target_cfg = self.config.get("target", {})
        target_url = target_override or target_cfg.get("url", "http://localhost:8081")

        runner = TestRunner(
            target_url=target_url,
            headless=browser_cfg.get("headless", True),
            timeout_ms=browser_cfg.get("timeout_ms", 30000),
            viewport=browser_cfg.get("viewport", {"width": 1280, "height": 720}),
            storage_state=browser_cfg.get("storage_state"),
            artifacts_dir=str(self.artifacts_dir),
        )

        await runner.start()
        results: list[TestRunResult] = []

        # Group records by form name
        forms: dict[str, list] = {}
        for record in dataset.records:
            forms.setdefault(record.form_name, []).append(record)

        for form_name, records in forms.items():
            for record in records:
                console.print(f"  Testing: {form_name} (variation {record.variation})")
                result = await runner.run_form_tests(form_name, record.data, record.variation)
                results.append(result)
                status = "✅" if result.status == "passed" else "❌"
                console.print(f"    {status} {result.status} ({result.total_duration_ms}ms)")

        await runner.close()

        passed = sum(1 for r in results if r.status == "passed")
        console.print(f"\n  Total: {passed}/{len(results)} passed")

        # Save results
        results_path = self.base_dir / "data" / "test_results.json"
        results_json = json.dumps(
            [r.__dict__ for r in results], indent=2, default=str
        )
        Path(results_path).parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(results_json, encoding="utf-8")
        console.print(f"  Results saved: {results_path}")

        return results

    async def phase4_correlate(
        self, results: list[TestRunResult], pipeline_start: float
    ) -> dict:
        """Phase 4: Correlate server logs with test events."""
        logs_cfg = self.config.get("logs", {})

        monitor = LogMonitor(
            log_type=logs_cfg.get("type", "docker"),
            docker_container=logs_cfg.get("docker_container"),
            log_file=logs_cfg.get("log_file"),
            journal_unit=logs_cfg.get("journal_unit"),
            error_patterns=logs_cfg.get("error_patterns", ["ERROR", "Exception"]),
        )

        # Record test events
        for result in results:
            for step in result.steps:
                monitor.record_test_event(
                    TestEvent(
                        timestamp=step.timestamp,
                        form_name=result.form_name,
                        variation=result.variation,
                        step=step.step,
                        action=step.action,
                        status=step.status,
                    )
                )

        # Collect and correlate
        logs = monitor.collect_logs(pipeline_start, time.time())
        monitor.log_events = logs
        correlations = monitor.correlate()
        report = monitor.generate_report()

        console.print(f"  Log errors found: {len(logs)}")
        console.print(f"  Correlated with tests: {len(correlations)}")

        # Save report
        report_path = self.base_dir / "logs" / "correlation_report.json"
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        console.print(f"  Report saved: {report_path}")

        return report