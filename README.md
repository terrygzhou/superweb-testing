# SuperWeb Testing

**AI-driven E2E web application testing pipeline.** Analyzes source code, generates realistic test data, runs browser automation, and correlates results with server logs.

## Features

- **4-phase pipeline**: Source analysis → Data generation → Browser testing → Log correlation
- **Dual execution modes**:
  - `scripted` — Deterministic Playwright-based pipeline (default)
  - `agent` — OpenHands AI agent delegation (3-conversation workflow)
- **Source-aware test data**: Extracts form schemas, endpoints, and input validation rules from source code
- **LLM-powered generation**: Uses Qwen3.6-27B or any OpenAI-compatible model
- **Structured output**: JSON results with timestamps, test data, and server log correlation

## Quick Start

```bash
# Install
pip install -e .

# Run full pipeline
superweb run --target http://localhost:8081 --source /path/to/source

# Dry run (analysis only)
superweb run --source /path/to/source --dry-run

# Source analysis only
superweb analyze --source /path/to/source

# Generate test data from existing schemas
superweb generate --schemas data/schemas.json
```

## CLI Reference

```bash
# Main pipeline
superweb run \
  --target http://localhost:8081 \
  --source /path/to/source \
  --output ./superweb_output \
  --llm-url http://172.25.0.1:8080 \
  --llm-model Qwen3.6-27B \
  --variations 3 \
  --mode scripted \
  --agent-workspace /path/to/workspace \
  --agent-timeout 600

# OpenHands container management
superweb openhands-start   # Start container on port 3005
superweb openhands-stop    # Stop container
superweb openhands-status  # Check status
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Pipeline                               │
├──────────┬──────────┬────────────┬──────────────────────────┤
│ Phase 1  │ Phase 2  │ Phase 3    │ Phase 4                  │
│ Source   │ Data     │ Browser    │ Log Correlation            │
│ Analysis │ Generation│ Testing   │ & Reporting              │
├──────────┼──────────┼────────────┼──────────────────────────┤
│          │          │            │                          │
│ • AST    │ • LLM    │ • Playwright│ • Server logs           │
│   parsing│ • Template│ • Headless │ • Error patterns        │
│ • Form   │   test data│   browser  │ • Timestamp correlation│
│   extraction│        │ • Screenshots│ • JSON reports         │
│ • Route  │          │ • Artifact  │                          │
│   mapping│          │   capture   │                          │
└──────────┴──────────┴────────────┴──────────────────────────┘
```

### Scripted Mode
Runs the 4-phase pipeline deterministically:
1. **Analyze** — Scans source code for forms, routes, and input schemas
2. **Generate** — Creates N test data variations per form via LLM
3. **Test** — Executes Playwright browser tests with generated data
4. **Correlate** — Matches server logs to test results

### Agent Mode
Delegates to OpenHands Agent Server via 3 sequential conversations:
1. **Analyze** — AI examines source code and generates schemas + test data
2. **Test** — AI writes and runs Playwright tests
3. **Report** — AI compiles structured results

## Output

```
superweb_output/
├── data/
│   ├── schemas.json          # Extracted form schemas
│   ├── test_data.json        # Generated test data
│   └── test_results.json     # Browser test results
├── logs/
│   └── correlation_report.json  # Log correlation analysis
├── artifacts/              # Screenshots, DOM snapshots
└── agent_report.json       # Agent mode final report
```

## Requirements

- **Python 3.12+**
- **Docker & Docker Compose** (for agent mode)
- **Playwright** browsers: `playwright install`
- **LLM endpoint** (OpenAI-compatible)

## Config (Optional)

Create `config.yaml` for persistent settings:

```yaml
target:
  url: "http://localhost:8081"

source:
  root: "~/workspace/projects/loop_factory"
  form_patterns: ["*.tsx", "*.py"]
  route_patterns: ["router.ts", "routes.ts"]

llm:
  base_url: "http://172.25.0.1:8080"
  model: "Qwen3.6-27B"

browser:
  headless: true
  timeout_ms: 30000
  viewport:
    width: 1280
    height: 720

logs:
  type: "docker"
  docker_container: "myapp"
  error_patterns:
    - "ERROR"
    - "Exception"

pipeline:
  data_variations: 3
```

## License

MIT