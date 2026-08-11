# SuperWeb Testing — Stale Code Analysis Report

**Date:** 2026-08-10
**Analyzer:** codegraph static analysis + import cross-reference
**Project:** `/home/terry/projects/superweb-testing`
**Python files scanned:** 14 (11 in `src/`, 2 in `tests/`, 1 root script)
**Total source lines:** ~3,300

---

## Executive Summary

| Category | Files | Status |
|---|---|---|
| Confirmed stale | 4 | Dead code / unreachable / broken |
| Orphaned modules | 1 | Not wired into any pipeline |
| Unused constants | 4 | Defined but never imported |
| Broken dependencies | 1 | Import error (missing package) |

---

## 1. Confirmed Stale Code

### 1.1 `tests/conftest.py` — Broken test configuration

**Severity:** High
**Issue:** Imports `pytest_playwright` which is not in `pyproject.toml` dev dependencies. Zero actual test files exist in `tests/`.

**Evidence:**
- `tests/conftest.py:8` imports `pytest_playwright.PlaywrightBrowser`
- `pyproject.toml` dev deps: `pytest>=8.3`, `pytest-asyncio>=0.24` (no `pytest-playwright`)
- `tests/__init__.py` is empty stub (20 bytes)
- No `test_*.py` files in `tests/` directory

**Impact:** Running `pytest` will fail with `ImportError` before any fixtures are created. The entire test suite is non-functional.

**Recommendation:** Either:
- Add `pytest-playwright` to dev dependencies and write actual tests, OR
- Remove `tests/` directory entirely until test infrastructure is implemented

---

### 1.2 `src/constants.py` — Two unused constants

**Severity:** Medium
**File:** `src/constants.py` (11 lines)

| Constant | Defined | Imported? | Used? |
|---|---|---|---|
| `DEFAULT_LLM_BASE_URL` | Yes | Yes (3 modules) | Yes |
| `DEFAULT_LLM_MODEL` | Yes | Yes (3 modules) | Yes |
| `DEFAULT_OPENHANDS_LLM_MODEL` | Yes | Yes (2 modules) | Yes |
| `DEFAULT_OPENHANDS_BASE_URL` | Yes | **No** | **No** |
| `DEFAULT_OPENHANDS_TIMEOUT` | Yes | **No** | **No** |

**Evidence:**
- `DEFAULT_OPENHANDS_BASE_URL` and `DEFAULT_OPENHANDS_TIMEOUT` are defined at lines 6-7
- Grep across entire project confirms zero external references to either constant
- pipeline.py L118-120 uses hardcoded `timeout=2400` and `base_url` from config instead

**Recommendation:** Remove `DEFAULT_OPENHANDS_BASE_URL` and `DEFAULT_OPENHANDS_TIMEOUT` from `constants.py`. If needed later, add them when the corresponding feature is implemented.

---

### 1.3 `src/source_analyzer.py` — Unused module-level regex patterns

**Severity:** Low
**File:** `src/source_analyzer.py`

| Pattern | Line | Used? |
|---|---|---|
| `_FORM_PATTERNS` | 53 | No |
| `_ROUTE_PATTERNS` | 55 | No |

**Evidence:**
- `_FORM_PATTERNS` is a compiled regex for detecting Pydantic models (`class ... BaseModel|Schema|Model`)
- `_ROUTE_PATTERNS` is a compiled regex for detecting route decorators (`@...get/post|put|delete`)
- Neither is referenced in any method of `SourceAnalyzer` class
- Grep confirms only definition exists, zero usages anywhere in the project

**Recommendation:** Remove these two constants. The `SourceAnalyzer` class uses AST-based analysis (lines 58-395), so these regex patterns were likely abandoned during development.

---

### 1.4 `test_openhands_connection.py` — Orphaned root-level script

**Severity:** Low
**File:** `test_openhands_connection.py` (87 lines)

**Evidence:**
- Standalone script with `if __name__ == '__main__'` guard
- Not imported by any module
- Not referenced in `pyproject.toml` scripts
- Not wired into CI/CD or `run_test.sh`
- Tests connectivity to `localhost:3005` (OpenHands endpoint)

**Impact:** No impact — it's a standalone debugging tool that happens to be in the repo root.

**Recommendation:** Move to a `scripts/` or `tools/` directory with documentation, or remove if the connectivity testing workflow is no longer needed.

---

## 2. Orphaned Modules

### 2.1 `src/crm_test_runner.py` — 505-line module not wired into pipeline

**Severity:** Medium
**File:** `src/crm_test_runner.py`

**Evidence:**
- 505 lines of code (~20 KB) — one of the largest modules
- Has `if __name__ == '__main__'` entry point (line 504)
- **Zero external importers** — no module imports `crm_test_runner`
- Not wired into `src/pipeline.py` or `src/cli.py`
- `pipeline.py` only imports: `openhands_client`, `source_analyzer`, `data_generator`, `test_runner`, `log_monitor`
- `cli.py` only imports: `pipeline`, `data_generator`

**What it does:**
- `CRMTestRunner` class that directly orchestrates all 4 pipeline phases
- Creates source analyzer, data generator, test runner, log monitor, and report
- Supports `crm` command in CLI (`superweb crm run`)
- Has its own OpenHands client integration

**Impact:** This module duplicates the pipeline's orchestration logic. The main `Pipeline` class in `pipeline.py` already does:
1. Source analysis → data generation → browser testing → log correlation

`CRMTestRunner` does the same 4 steps but with a different report format and direct OpenHands client usage.

**Recommendation:** Either:
- Wire `crm_test_runner` into the CLI as a distinct command mode, OR
- Merge its unique functionality into the main `Pipeline` class and remove it

---

## 3. Additional Observations

### 3.1 CLI `extract_forms` command duplicates pipeline logic
- `cli.py` L179-211: `extract_forms` command creates a `DataGenerator` directly, generates data, and writes `forms_data.json`
- This bypasses the full pipeline (source analysis → data gen → test → logs)
- Could be considered a diagnostic tool, but it's exposed as a top-level command

### 3.2 config.yaml generated at runtime
- `run_test.sh` generates `config.yaml` at runtime
- `pipeline.py` has a `from_config()` class method but also a `__init__` constructor
- The CLI always uses the constructor path; config loading is only used when called from the run script

### 3.3 No async in test infrastructure
- `pyproject.toml` lists `pytest-asyncio` as dev dependency
- `conftest.py` has `@pytest.fixture(scope="session")` with sync fixtures
- No test files exist to exercise async test infrastructure

---

## Summary of Actions Required

| Priority | Action | File(s) |
|---|---|---|
| P1 | Fix test infrastructure or remove tests/ | `tests/`, `pyproject.toml` |
| P2 | Wire or remove crm_test_runner | `src/crm_test_runner.py` |
| P3 | Remove unused constants | `src/constants.py` |
| P4 | Remove unused regex patterns | `src/source_analyzer.py` |
| P5 | Relocate or remove root script | `test_openhands_connection.py` |
