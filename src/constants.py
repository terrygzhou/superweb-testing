"""Shared defaults and constants."""

from __future__ import annotations

# LLM endpoint — default to localhost; override via --llm-url or config.yaml
DEFAULT_LLM_BASE_URL: str = "http://localhost:8080"
DEFAULT_LLM_MODEL: str = "Qwen3.6-27B"
DEFAULT_OPENHANDS_LLM_MODEL: str = "openai/Qwen3.6-27B"

# OpenHands agent server
DEFAULT_OPENHANDS_BASE_URL: str = "http://localhost:3000"
DEFAULT_OPENHANDS_TIMEOUT: int = 600
