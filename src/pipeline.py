"""Pipeline orchestrator — runs all phases end-to-end."""

from __future__ import annotations

import asyncio
import json
import shutil
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

    def __init__(
        self,
        config_path: str | None = None,
        output_dir: str = "./superweb_output",
        target_url: str = "",
        source_root: str = "",
        llm_url: str = "",
        llm_model: str = "",
        n_variations: int = 3,
        mode: str = "scripted",
        agent_workspace: str = "",
        agent_timeout: int = 600,
    ):
        self.config = self._load_config(config_path)
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.output_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Override config with explicit CLI args
        if target_url:
            self.config.setdefault("target", {})["url"] = target_url
        if source_root:
            self.config.setdefault("source", {})["root"] = source_root
        if llm_url:
            self.config.setdefault("llm", {})["base_url"] = llm_url
        if llm_model:
            self.config.setdefault("llm", {})["model"] = llm_model
        self.config.setdefault("pipeline", {})["data_variations"] = n_variations

        # Execution mode
        self.mode = mode
        self.agent_workspace = agent_workspace
        self.agent_timeout = agent_timeout

        # Intermediate data
        self.schemas: list[dict] = []
        self.dataset: TestDataset | None = None

    def _load_config(self, config_path: str | None) -> dict:
        """Load configuration from YAML."""
        if config_path is None:
            return {}
        path = Path(config_path)
        if path.exists():
            return yaml.safe_load(path.read_text()) or {}
        return {}

    async def run_agent_mode(
        self, source_root: str, target_url: str
    ) -> dict:
        """Run in agent mode: delegate to OpenHands Agent Server.

        Strategy: split monolithic task into 3 sequential conversations to avoid timeouts.
        Each conversation shares the same workspace directory for state handoff.
        """
        from src.openhands_client import OpenHandsClient, OpenHandsError

        # Copy source into the workspace dir that's mounted into the container
        # compose.yaml mounts ./workspace → /opt/workspace_base
        host_workspace = Path(__file__).parent.parent / "workspace" / "source"
        host_workspace.parent.mkdir(parents=True, exist_ok=True)
        if host_workspace.exists():
            shutil.rmtree(host_workspace)
        shutil.copytree(
            source_root, host_workspace,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv"),
        )
        container_source = "/opt/workspace_base/source"
        artifacts_dir = "/opt/workspace_base/artifacts"

        console.print(f"[blue]Copied source → {host_workspace}[/blue]")

        compose_path = str(Path(__file__).parent.parent / "compose.yaml")
        client = OpenHandsClient(
            base_url="http://localhost:3005",
            compose_file=compose_path,
            timeout=self.agent_timeout,
        )

        console.print("[bold blue]Agent mode: Starting OpenHands container...[/bold blue]")
        try:
            client.start_server()
        except Exception as e:
            console.print(f"[red]OpenHands startup failed: {e}[/red]")
            raise OpenHandsError(f"OpenHands startup failed: {e}")

        try:
            # ── Conversation 1: Analyze ──────────────────────────────
            console.print("[bold blue]Conversation 1: Analyze source...[/bold blue]")
            analyze_goal = (
                f"You are a QA automation engineer. Analyze the source code at {container_source}.\n"
                f"1. Find all forms, API endpoints, and input schemas.\n"
                f"2. Save a JSON file to {artifacts_dir}/analysis.json with:\n"
                f"   - forms: list of {{'name': ..., 'fields': [{'field_name': ..., 'type': ..., 'required': ...}], 'endpoint': ...}}\n"
                f"   - endpoints: list of API routes and expected methods\n"
                f"3. Generate 3 realistic test data variations per form, save to {artifacts_dir}/test_data.json\n"
            )
            conv1_id = client.create_conversation(analyze_goal, container_source)
            console.print(f"  Conversation ID: {conv1_id}")
            result1 = client.poll_conversation(conv1_id)
            console.print(f"  [green]Analysis complete (status: {result1.get('status')})[/green]")

            # ── Conversation 2: Test ─────────────────────────────────
            console.print("[bold blue]Conversation 2: Run tests...[/bold blue]")
            test_goal = (
                f"You are a QA automation engineer. Run Playwright tests against {target_url}.\n"
                f"1. Read test data from {artifacts_dir}/test_data.json\n"
                f"2. Write Playwright scripts that submit each test case to the target webapp.\n"
                f"3. Run the tests and capture screenshots on failure.\n"
                f"4. Save results to {artifacts_dir}/test_results.json with:\n"
                f"   - tests: list of {{test_name, status: 'passed'|'failed', duration_ms, screenshot: null|path}}\n"
                f"   - summary: {{total, passed, failed}}\n"
            )
            conv2_id = client.create_conversation(test_goal, container_source)
            console.print(f"  Conversation ID: {conv2_id}")
            result2 = client.poll_conversation(conv2_id)
            console.print(f"  [green]Tests complete (status: {result2.get('status')})[/green]")

            # ── Conversation 3: Report ────────────────────────────────
            console.print("[bold blue]Conversation 3: Generate report...[/bold blue]")
            report_goal = (
                f"You are a QA automation engineer. Generate a final report.\n"
                f"1. Read {artifacts_dir}/analysis.json and {artifacts_dir}/test_results.json\n"
                f"2. Compile a structured report and save to {artifacts_dir}/report.json with:\n"
                f"   - forms_analyzed: count\n"
                f"   - test_records: count\n"
                f"   - tests_passed: count\n"
                f"   - tests_failed: count\n"
                f"   - summary: narrative of findings\n"
            )
            conv3_id = client.create_conversation(report_goal, container_source)
            console.print(f"  Conversation ID: {conv3_id}")
            result3 = client.poll_conversation(conv3_id)
            console.print(f"  [green]Report complete (status: {result3.get('status')})[/green]")

            # ── Collect artifacts ────────────────────────────────────
            report_path = self.output_dir / "agent_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)

            # Try to read the report from workspace
            host_report = host_workspace.parent / "artifacts" / "report.json"
            if host_report.exists():
                report_data = json.loads(host_report.read_text())
                report_path.write_text(
                    json.dumps(report_data, indent=2, default=str), encoding="utf-8"
                )
                console.print(f"[bold]Agent report saved: {report_path}[/bold]")
                return report_data
            else:
                # Fallback: combine conversation results
                combined = {
                    "analysis": result1,
                    "tests": result2,
                    "report": result3,
                }
                report_path.write_text(
                    json.dumps(combined, indent=2, default=str), encoding="utf-8"
                )
                console.print(f"[yellow]Report fallback: {report_path}[/yellow]")
                return combined
        finally:
            console.print("[yellow]Stopping OpenHands container...[/yellow]")
            client.stop_server()
            client.close()

    async def run(self, source_override: str = "", target_override: str = "") -> dict:
        """Run the full pipeline in the configured mode."""
        if self.mode == "agent":
            source_root = source_override or self.config.get("source", {}).get(
                "root", "~/workspace/projects/loop_factory"
            )
            target_url = target_override or self.config.get("target", {}).get(
                "url", "http://localhost:8081"
            )
            return await self.run_agent_mode(source_root, target_url)

        # Scripted mode (existing pipeline)
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
        schema_path = self.output_dir / "data" / "schemas.json"
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
        data_path = self.output_dir / "data" / "test_data.json"
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
        results_path = self.output_dir / "data" / "test_results.json"
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
        report_path = self.output_dir / "logs" / "correlation_report.json"
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        console.print(f"  Report saved: {report_path}")

        return report