"""Allow running as: python -m src.pipeline"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.pipeline import Pipeline


def main():
    """Entry point for python -m src.pipeline"""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    pipeline = Pipeline(
        config_path=config_path,
        output_dir="./superweb_output",
    )
    asyncio.run(pipeline.run())


if __name__ == "__main__":
    main()