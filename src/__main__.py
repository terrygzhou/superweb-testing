"""Allow running as: python -m src.pipeline"""

import asyncio
import sys
from pathlib import Path

from src.pipeline import Pipeline


def main():
    """Entry point for python -m src.pipeline"""
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    pipeline = Pipeline(config_path)
    asyncio.run(pipeline.run())


if __name__ == "__main__":
    main()