"""Playwright configuration for SuperWeb Testing."""

import os
from pathlib import Path

from playwright.async_api import async_playwright

from pytest_playwright import PlaywrightBrowser


def pytest_configure(config):
    """Configure Playwright for testing."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


@pytest_asyncio.fixture(scope="session")
async def browser():
    """Session-scoped browser instance."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest_asyncio.fixture
async def page(browser):
    """Page fixture with default viewport."""
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        ignore_https_errors=True,
    )
    page = await context.new_page()
    yield page
    await context.close()