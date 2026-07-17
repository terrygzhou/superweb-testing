# SuperWeb Testing — AI-Driven E2E Web App Testing

Self-contained, distributable testing pipeline that analyzes webapp source code and generates browser automation tests via Playwright. Supports **scripted mode** (deterministic 4-phase pipeline) and **agent mode** (OpenHands-powered autonomous QA).

## Quickstart

### Install

```bash
cd /home/terry/workspace/projects/superweb_testing
uv venv && source .venv/bin/activate
uv pip install -e .
playwright install chromium
```

### Run

The `superweb` CLI can be invoked from **any directory** — only the target source and output paths need to be specified (use absolute paths when running outside the project):

```bash
# Start OpenHands (for agent mode only)
superweb openhands-start

# Scripted mode (deterministic pipeline)
superweb run --target http://localhost:8081 --source ./my-app --mode scripted

# Agent mode (OpenHands autonomous QA)
superweb run --target http://localhost:8081 --source ./my-app --mode agent

# Dry run (source analysis only)
superweb run --target http://localhost:8081 --source ./my-app --dry-run

# From any other directory — use absolute paths (scripted mode)
superweb run \
  --target http://localhost:8081 \
  --source /home/terry/workspace/projects/loop_factory \
  --output /tmp/test_output

# Agent mode from any directory
superweb run \
  --target http://localhost:8081 \
  --source /home/terry/workspace/projects/loop_factory \
  --output /tmp/test_output \
  --mode agent
```

## Architecture

```
┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────────┐
│ source_analyze│───▶│ data_generator│───▶│ test_runner  │───▶│ log_monitor  │
│ (Phase 1)     │    │ (Phase 2)     │    │ (Phase 3)    │    │ (Phase 4)    │
└──────────────┘    └───────────────┘    └──────────────┘    └──────────────┘
       │                    │                    │                    │
       ▼                    ▼                    ▼                    ▼
  Form schemas        Test data records    Browser sessions     Error report
  Routes             Variations          Screenshots           Correlated
  Validators         Edge cases          Assertions           with timeline
```

Agent mode bypasses this pipeline and delegates to OpenHands:

```
┌──────────────┐     ┌─────────────────────────────────┐
│ superweb-cli │────▶│ OpenHands Agent Server (Docker)  │
│ (orchestrator) │     │ + source code analysis          │
└──────────────┘     │ + Playwright test generation     │
                     │ + automated execution             │
                     │ + server log correlation         │
                     └─────────────────────────────────┘
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `superweb run` | Full pipeline (scripted or agent mode) |
| `superweb analyze` | Phase 1 only — source analysis |
| `superweb generate` | Phase 2 only — test data generation |
| `superweb openhands-start` | Start OpenHands container |
| `superweb openhands-stop` | Stop OpenHands container |
| `superweb openhands-status` | Check container status |

### `superweb run` Options

| Flag | Default | Description |
|------|---------|------------|
| `--target`, `-t` | (required) | Target webapp URL |
| `--source`, `-s` | (required) | Local path or git URL |
| `--output`, `-o` | `./superweb_output` | Output directory |
| `--mode` | `scripted` | `scripted` or `agent` |
| `--dry-run` | `False` | Source analysis only |
| `--llm-url` | `http://172.25.0.1:8080` | LLM endpoint |
| `--llm-model` | `Qwen3.6-27B` | LLM model name |
| `--variations`, `-v` | `3` | Test data variations (1-5) |
| `--config`, `-c` | `None` | Optional config.yaml |

## Pipeline Phases

| # | Module | Input | Output |
|---|--------|-------|--------|
| 1 | `source_analyzer` | Target webapp source code | Form schemas (JSON) — fields, types, validators |
| 2 | `data_generator` | Schemas + LLM | Test data (JSON) — happy path, edge cases, boundary values |
| 3 | `test_runner` | Test data + Playwright | Browser automation results — form fills, clicks, navigation, screenshots |
| 4 | `log_monitor` | Server logs + test events | Correlated error report — errors mapped to test timeline |

## Agent Mode (OpenHands)

Agent mode delegates full QA workflow to an OpenHands Agent Server running in Docker:

1. **Source analysis** — agent reads and understands the webapp codebase
2. **Test generation** — agent creates Playwright scripts for all identified forms/endpoints
3. **Execution** — agent runs tests against the target webapp
4. **Reporting** — agent produces a structured JSON report with:
   - Forms analyzed, test records generated
   - Pass/fail counts with screenshots on failure
   - Server log correlation for error diagnosis

### OpenHands Setup

```bash
# Start the container (also via CLI: superweb openhands-start)
docker compose up -d

# Check status
docker logs openhands-server
```

The container exposes the REST API on `http://localhost:3005`. The client communicates via `/api/v1/app-conversations` endpoints.

## Requirements

- Python 3.12+
- Chromium (Playwright)
- LLM endpoint (local vLLM or compatible OpenAI API)
- Docker (for agent mode — OpenHands container)
- Target webapp running and accessible

## Project Structure

```
superweb_testing/
├── compose.yaml              # OpenHands Agent Server Docker config
├── src/
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── cli.py                # CLI commands (Typer)
│   ├── pipeline.py         # Pipeline orchestrator
│   ├── openhands_client.py # OpenHands REST client
│   ├── source_analyzer.py  # Phase 1: form schema extraction
│   ├── data_generator.py   # Phase 2: LLM-powered test data
│   ├── test_runner.py      # Phase 3: Playwright automation
│   └── log_monitor.py      # Phase 4: log correlation
├── tests/
│   └── __init__.py
└── test_openhands_connection.py  # Connectivity test script
```

## Notes

- Scripted mode runs all 4 phases sequentially in the orchestrator process
- Agent mode delegates to OpenHands — the orchestrator submits a goal and polls for results
- Source URLs (`https://` or `git@`) are auto-cloned to the output directory
- LLM is optional — data generator falls back to template-based generation if unavailable