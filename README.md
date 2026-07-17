# SuperWeb Testing — AI-Driven E2E Web App Testing

Pipeline: **read source code** → **generate test data** → **browser automation** → **server log correlation**

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

## Quickstart

```bash
cd /home/terry/workspace/projects/superweb_testing
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml

# Run
python -m src.pipeline run --source ~/workspace/projects/loop_factory
```

## Phases

| # | Module | Input | Output |
|---|--------|-------|--------|
| 1 | `source_analyzer` | Target web app source code | Form schemas (JSON) — fields, types, validators |
| 2 | `data_generator` | Schemas + LLM | Test data (JSON) — happy path, edge cases, boundary values |
| 3 | `test_runner` | Test data + Playwright | Browser automation results — form fills, clicks, navigation, screenshots |
| 4 | `log_monitor` | Server logs + test events | Correlated error report — errors mapped to test timeline |

## Requirements

- Python 3.12+
- Chromium (Playwright)
- LLM endpoint (local vLLM or compatible)
- Target web app running and accessible