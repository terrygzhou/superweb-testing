"""Pipeline orchestrator — runs all phases end-to-end."""

from __future__ import annotations

import asyncio
import json
import os
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
            for root, dirs, files in os.walk(str(host_workspace), topdown=False):
                for f in files:
                    os.unlink(os.path.join(root, f))
                for d in dirs:
                    os.rmdir(os.path.join(root, d))
            os.rmdir(str(host_workspace))
        shutil.copytree(
            source_root, host_workspace,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv"),
        )
        # All paths must be inside the workspace working_dir so the agent can access them.
        # compose.yaml mounts ./workspace → /opt/workspace_base
        # working_dir is /opt/workspace_base/source — agent sandbox is confined here.
        container_source = "/opt/workspace_base/source"
        artifacts_dir = "/opt/workspace_base/source/artifacts"
        # Ensure the artifacts dir exists so the agent can write to it without sudo
        host_artifacts = host_workspace / "artifacts"
        host_artifacts.mkdir(parents=True, exist_ok=True)

        console.print(f"[blue]Copied source → {host_workspace}[/blue]")

        compose_path = str(Path(__file__).parent.parent / "compose.yaml")
        client = OpenHandsClient(
            base_url="http://localhost:3005",
            compose_file=compose_path,
            timeout=self.agent_timeout,
            model="openai/Qwen3.6-27B",
            base_llm_url="http://172.25.0.1:8080",
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
                f"You are a QA automation engineer. Your working directory is "
                f"{container_source}. Your output directory is {artifacts_dir}.\n\n"
                "CRITICAL: Only scan the `src/` subdirectory for frontend components. "
                "Ignore node_modules, dist, .venv, and any non-source directories.\n\n"
                "Use your tools (bash, write_file) to complete these steps:\n"
                "1. List ONLY frontend files: `find src/ -name '*.tsx' -o -name '*.ts' | sort`\n"
                "   If no src/ exists, try: `ls -la` then `find . -maxdepth 3 -name '*.tsx' -o -name '*.ts' | head -50`\n"
                "2. Read 5-10 key component files that contain forms, inputs, or API calls.\n"
                "   Focus on files matching: *Form*, *Dialog*, *Input*, *Chat*, *Settings*, *Search*, *Create*, *Upload*\n"
                f"3. Write a JSON file at {artifacts_dir}/analysis.json containing:\n"
                "   - forms: list of {{'name': ..., 'fields': [{{'field_name': ..., 'type': ..., 'required': ...}}], 'endpoint': ..., 'source_file': ...}}\n"
                "   - endpoints: list of API routes and expected methods\n"
                f"4. Generate 3 realistic test data variations per form, save to {artifacts_dir}/test_data.json\n\n"
                "TIME LIMIT: Complete within 8 minutes. If stuck on large repos, focus on 5-8 form components max.\n"
                "Use write_file or bash (echo/cat heredoc) to create the JSON files. "
                "Make sure each file is valid JSON before finishing."
            )
            conv1_id = client.create_conversation(analyze_goal, container_source)
            console.print(f"  Conversation ID: {conv1_id}")

            event_count = [0]
            def on_event(evt):
                kind = evt.get("kind", "unknown")
                code = evt.get("code")
                source = evt.get("source", "")
                event_count[0] += 1
                if code:
                    console.print(f"  [red]⚠ Event #{event_count[0]}: {kind} (code: {code})[/red]")
                elif source == "agent":
                    console.print(f"  [dim]→ {kind}[/dim]")

            result1 = client.poll_conversation_with_events(conv1_id, on_event=on_event)
            exec_status = result1.get("execution_status", "unknown")
            console.print(f"  [green]✓ Analysis complete (status: {exec_status}, events: {event_count[0]})[/green]")

            # ── Conversation 2: Test ─────────────────────────────────
            console.print("[bold blue]Conversation 2: Run tests...[/bold blue]")
            test_goal = (
                f"You are a QA automation engineer. Your working directory is "
                f"{container_source}. Your output directory is {artifacts_dir}.\n\n"
                f"Run tests against {target_url}. Generate comprehensive, traceable test results.\n\n"
                "## Steps\n"
                "1. Read analysis from " + artifacts_dir + "/analysis.json to identify forms, endpoints, and source file mappings.\n"
                "2. Read test data from " + artifacts_dir + "/test_data.json.\n"
                "3. For each test case, perform the following:\n"
                "   a. Navigate to the target page URL.\n"
                "   b. Generate a unique session_id (UUIDv4) for this test run.\n"
                "   c. Fill form fields or make API requests with the test data.\n"
                "   d. Capture the HTTP response (status code, headers, body).\n"
                "   e. Capture frontend console logs (via Playwright page.on('console') or curl --verbose).\n"
                "   f. If the target app exposes a logs endpoint (e.g. /api/logs, /logs), fetch and capture server-side log output for this request.\n"
                "   g. Identify the source code file that implements the tested form/endpoint (from analysis.json).\n"
                "4. Save results to " + artifacts_dir + "/test_results.json.\n\n"
                "## Output Schema — test_results.json\n"
                "Each test entry must contain ALL of these fields:\n"
                '{\n'
                "  \"tests\": [\n"
                "    {\n"
                "      \"test_name\": \"string (e.g. CreateProjectForm/Standard English Project)\",\n"
                '      \"status\": "passed" | "failed" | "error" | "skipped",\n'
                "      \"duration_ms\": number,\n"
                "      \"session_id\": \"UUIDv4 string\",\n"
                "      \"page_url\": \"full URL navigated to (e.g. http://host.docker.internal:19829/project/create)\",\n"
                '      \"test_data\": { field_name: value, ... },\n'
                '      \"action_performed\": "HTTP method + path or browser action description (e.g. POST /api/projects, filled and submitted CreateProjectForm)\",\n'
                '      \"source_file\": "relative path to source file that implements this form/endpoint (e.g. src/forms/create_project.tsx or backend/api/projects.py)",\n'
                '      \"http_response\": {\n'
                '        "status_code": number | null,\n'
                '        "headers": { key: value, ... } | null,\n'
                '        "body_preview": "first 500 chars of response body or null"\n'
                "      },\n"
                '      \"frontend_logs\": [\n'
                '        { "level": "log|warn|error", "message": "string" }\n'
                "      ],\n"
                '      \"server_logs\": [\n'
                '        { "timestamp": "ISO string", "level": "INFO|WARN|ERROR", "message": "string" }\n'
                "      ],\n"
                '      \"error\": {\n'
                '        "error_code": "string (HTTP status, exception type, or null if passed)",\n'
                '        "exception_description": "stack trace or error message, or null",\n'
                '        "frontend_error": "console.error output or UI error text, or null",\n'
                '        "server_error": "server-side error from logs or response, or null"\n'
                "      },\n"
                '      \"screenshot": "relative path to screenshot on failure, or null"\n'
                "    }\n"
                "  ],\n"
                "  \"summary\": { \"total\": number, \"passed\": number, \"failed\": number, \"skipped\": number }\n"
                "}\n\n"
                "IMPORTANT:\n"
                "- Every test entry MUST include session_id, page_url, test_data, action_performed, source_file, http_response, frontend_logs, server_logs, and error fields.\n"
                "- For passed tests, set error fields to null.\n"
                "- For failed tests, populate ALL error sub-fields with actual captured data.\n"
                "- Make the test_data object contain the exact values submitted (redact secrets if any).\n"
                "- Use bash/curl or write a Python test script to execute tests. Do NOT use Playwright if unavailable — curl/fetch is acceptable.\n"
                "- Verify the JSON is valid before saving."
            )
            conv2_id = client.create_conversation(test_goal, container_source)
            console.print(f"  Conversation ID: {conv2_id}")

            event_count[0] = 0
            result2 = client.poll_conversation_with_events(conv2_id, on_event=on_event)
            exec_status = result2.get("execution_status", "unknown")
            console.print(f"  [green]✓ Tests complete (status: {exec_status}, events: {event_count[0]})[/green]")

            # ── Conversation 3: Report ────────────────────────────────
            console.print("[bold blue]Conversation 3: Generate report...[/bold blue]")
            report_goal = (
                f"You are a QA automation engineer. Your working directory is "
                f"{container_source}. Your output directory is {artifacts_dir}.\n\n"
                "Generate a comprehensive, traceable test report.\n\n"
                "## Steps\n"
                "1. Read " + artifacts_dir + "/analysis.json for form/endpoint/source mapping.\n"
                "2. Read " + artifacts_dir + "/test_results.json for per-test results.\n"
                "3. Read " + artifacts_dir + "/test_data.json for test input data.\n"
                "4. Cross-reference all data sources and compile a full report.\n"
                "5. Save to " + artifacts_dir + "/report.json.\n\n"
                "## Output Schema — report.json\n"
                '{\n'
                '  "report_metadata": {\n'
                '    "generated_at": "ISO 8601 timestamp",\n'
                '    "target_url": "string",\n'
                '    "source_root": "path to analyzed source",\n'
                '    "pipeline_mode": "agent"\n'
                "  },\n"
                '  "summary": {\n'
                '    "forms_analyzed": number,\n'
                '    "test_records": number,\n'
                '    "tests_passed": number,\n'
                '    "tests_failed": number,\n'
                '    "tests_skipped": number,\n'
                '    "pass_rate": percentage (0-100),\n'
                '    "total_duration_ms": number,\n'
                '    "avg_duration_ms": number\n'
                "  },\n"
                '  "test_details": [\n'
                "    {\n"
                '      "test_name": "string",\n'
                '      "status": "passed" | "failed" | "error" | "skipped",\n'
                '      "duration_ms": number,\n'
                '      "session_id": "UUIDv4",\n'
                '      "page_url": "full URL",\n'
                '      "test_data": { field: value, ... },\n'
                '      "action_performed": "string",\n'
                '      "source_file": "relative path",\n'
                '      "http_response": {\n'
                '        "status_code": number | null,\n'
                '        "headers": { ... } | null,\n'
                '        "body_preview": "string | null"\n'
                "      },\n"
                '      "frontend_logs": [{ "level": "string", "message": "string" }],\n'
                '      "server_logs": [{ "timestamp": "string", "level": "string", "message": "string" }],\n'
                '      "error": {\n'
                '        "error_code": "string | null",\n'
                '        "exception_description": "string | null",\n'
                '        "frontend_error": "string | null",\n'
                '        "server_error": "string | null"\n'
                "      },\n"
                '      "screenshot": "path | null"\n'
                "    }\n"
                "  ],\n"
                '  "failures": [\n'
                "    {\n"
                '      "test_name": "string",\n'
                '      "error_code": "string",\n'
                '      "exception_description": "string",\n'
                '      "frontend_error": "string | null",\n'
                '      "server_error": "string | null",\n'
                '      "session_id": "UUIDv4",\n'
                '      "source_file": "string"\n'
                "    }\n"
                "  ],\n"
                '  "source_coverage": {\n'
                '    "files_tested": ["list of unique source files tested"],\n'
                '    "endpoints_tested": ["list of unique API endpoints tested"],\n'
                '    "forms_tested": ["list of unique form names tested"]\n'
                "  },\n"
                '  "narrative_summary": "string — concise summary of findings, key regressions, and recommendations"\n'
                "}\n\n"
                "## Requirements\n"
                "- ALL test entries from test_results.json must appear in test_details.\n"
                "- Cross-reference analysis.json to fill in source_file mappings where test_results.json is incomplete.\n"
                "- failures array lists only tests with status 'failed' or 'error'.\n"
                "- source_coverage aggregates unique values from all test entries.\n"
                "- narrative_summary must be 150-500 words, focusing on pass rate, failure patterns, and actionable recommendations.\n"
                "- Verify the JSON is valid before saving.\n\n"
                "## Generate Markdown Report\n"
                f"Then write a human-readable Markdown report to {artifacts_dir}/report.md.\n\n"
                "The Markdown must be easily parsable by coding agents for backlog generation AND readable by humans for action prioritization.\n\n"
                "## Markdown Structure\n\n"
                "```markdown\n"
                "# Test Report — <target_url>\n\n"
                "## Executive Summary\n\n"
                "| Metric | Value |\n"
                "| --- | --- |\n"
                "| Tests | <total> |\n"
                "| Passed | <passed> |\n"
                "| Failed | <failed> |\n"
                "| Pass Rate | <rate>% |\n"
                "| Duration | <duration_ms>ms |\n\n"
                "## Failure Triage (by root cause)\n\n"
                "Group all failures by error_code or root cause. For each group:\n\n"
                "### <Error Code> — <Exception Summary> (<count> failures)\n\n"
                "- **Severity:** CRITICAL | HIGH | MEDIUM | LOW\n"
                "- **Root Cause:** <one-sentence diagnosis>\n"
                "- **Affected Forms:** <list of test names>\n"
                "- **Source Files:** <list of source files>\n"
                "- **Action Required:** <specific remediation step>\n\n"
                "### <next group> ... (repeat for each error group)\n\n"
                "## Blocking Issues\n\n"
                "List issues that prevent other tests from passing:\n\n"
                "- **<Issue>** — <Why it blocks other tests, what must be fixed first>\n\n"
                "## Detailed Failures\n\n"
                "Each failure is a standalone task for agent-driven remediation.\n\n"
                "### <test_name>\n\n"
                "- **Session:** <session_id>\n"
                "- **Source:** <source_file>\n"
                "- **Error Code:** <error_code>\n"
                "- **Exception:** <exception_description>\n"
                "- **Frontend Error:** <frontend_error>\n"
                "- **Server Error:** <server_error>\n"
                "- **Action Performed:** <action_performed>\n\n"
                "### <next_failure> ... (repeat for each failure)\n\n"
                "## Test Details\n\n"
                "| Test | Status | Duration | Source File | Error Code |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| <test_name> | <status> | <duration_ms>ms | <source_file> | <error_code or - > |\n\n"
                "## Source Coverage\n\n"
                "| Category | Count | Details |\n"
                "| --- | --- | --- |\n"
                "| Files Tested | <count> | <comma-separated list> |\n"
                "| Endpoints Tested | <count> | <comma-separated list> |\n"
                "| Forms Tested | <count> | <comma-separated list> |\n\n"
                "## Coverage Gaps\n\n"
                "- **Untested Forms:** <list of forms from analysis.json that had no tests>\n"
                "- **Untested Endpoints:** <list of endpoints from analysis.json that had no tests>\n"
                "- **Risk Assessment:** <what's the risk of not testing these>\n\n"
                "## Recommendations\n\n"
                "1. <Highest priority action — usually the blocking issue or most impactful fix>\n"
                "2. <Second priority>\n"
                "3. <Third priority>\n\n"
                "## Narrative Summary\n\n"
                "<narrative_summary from report.json — 150-500 words>\n"
                "```"
            )
            conv3_id = client.create_conversation(report_goal, container_source)
            console.print(f"  Conversation ID: {conv3_id}")

            event_count[0] = 0
            result3 = client.poll_conversation_with_events(conv3_id, on_event=on_event)
            exec_status = result3.get("execution_status", "unknown")
            console.print(f"  [green]✓ Report complete (status: {exec_status}, events: {event_count[0]})[/green]")

            # ── Copy all artifacts from workspace to output ──────────
            self.output_dir.mkdir(parents=True, exist_ok=True)
            host_artifacts = host_workspace / "artifacts"
            if host_artifacts.exists():
                # Copy entire artifacts directory (analysis.json, test_data.json,
                # test_results.json, report.json, run_tests.py, screenshots/)
                dest = self.output_dir / "artifacts"
                if dest.exists():
                    for root, dirs, files in os.walk(str(dest), topdown=False):
                        for f in files:
                            os.unlink(os.path.join(root, f))
                        for d in dirs:
                            os.rmdir(os.path.join(root, d))
                    os.rmdir(str(dest))
                shutil.copytree(host_artifacts, dest, symlinks=False)
                console.print(f"[bold]Artifacts copied: {dest}[/bold]")
                # Also write agent_report.json as the top-level summary
                host_report = host_artifacts / "report.json"
                if host_report.exists():
                    report_data = json.loads(host_report.read_text())
                    report_path = self.output_dir / "agent_report.json"
                    report_path.write_text(
                        json.dumps(report_data, indent=2, default=str),
                        encoding="utf-8",
                    )
                    console.print(f"[bold]Agent report saved: {report_path}[/bold]")
                    return report_data
            # Fallback: combine conversation results
            combined = {"analysis": result1, "tests": result2, "report": result3}
            report_path = self.output_dir / "agent_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
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