"""Phase 3: Playwright test runner — browser automation, form filling, navigation, assertions."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


@dataclass
class TestStepResult:
    """Result of a single browser interaction step."""

    step: str
    action: str  # click | fill | navigate | assert | screenshot
    status: str = "pending"  # pending | passed | failed | skipped
    details: str = ""
    screenshot_path: str = ""
    duration_ms: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class TestRunResult:
    """Result of a complete test run for one form."""

    form_name: str
    variation: int
    steps: list[TestStepResult] = field(default_factory=list)
    status: str = "running"  # running | passed | failed
    total_duration_ms: int = 0
    artifacts: list[str] = field(default_factory=list)


class TestRunner:
    """Execute browser-based E2E tests using Playwright."""

    def __init__(
        self,
        target_url: str,
        headless: bool = True,
        timeout_ms: int = 30000,
        viewport: dict[str, int] | None = None,
        storage_state: str | None = None,
        artifacts_dir: str = "./artifacts",
    ):
        self.target_url = target_url
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.viewport = viewport or {"width": 1280, "height": 720}
        self.storage_state = storage_state
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self):
        """Initialize browser."""
        pw = await async_playwright().__aenter__()
        self._playwright = pw
        self._browser = await pw.chromium.launch(headless=self.headless)
        context_opts: dict[str, Any] = {
            "viewport": self.viewport,
            "ignore_https_errors": True,
        }
        if self.storage_state:
            context_opts["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**context_opts)

    async def close(self):
        """Shutdown browser."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()

    async def run_form_tests(
        self, form_name: str, test_data: dict[str, Any], variation: int
    ) -> TestRunResult:
        """Run E2E tests for a single form with given test data."""
        result = TestRunResult(form_name=form_name, variation=variation)
        page = await self._context.new_page()

        try:
            # Phase 3a: Navigate to the target page
            step = TestStepResult(step="1", action="navigate")
            try:
                start = time.time()
                await page.goto(self.target_url, timeout=self.timeout_ms)
                step.status = "passed"
                step.duration_ms = int((time.time() - start) * 1000)
            except Exception as e:
                step.status = "failed"
                step.details = str(e)
                result.steps.append(step)
                result.status = "failed"
                return result
            result.steps.append(step)

            # Phase 3b: Auto-discover and fill form fields
            step = TestStepResult(step="2", action="discover_and_fill")
            try:
                await self._fill_form(page, test_data)
                step.status = "passed"
            except Exception as e:
                step.status = "failed"
                step.details = str(e)
            result.steps.append(step)

            # Phase 3c: Submit the form
            step = TestStepResult(step="3", action="submit")
            try:
                await self._submit_form(page)
                step.status = "passed"
            except Exception as e:
                step.status = "failed"
                step.details = str(e)
            result.steps.append(step)

            # Phase 3d: Verify outcome
            step = TestStepResult(step="4", action="assert")
            try:
                await self._verify_page(page)
                step.status = "passed"
            except Exception as e:
                step.status = "failed"
                step.details = str(e)
            result.steps.append(step)

            # Phase 3e: Explore navigation — click all actionable elements
            step = TestStepResult(step="5", action="explore_navigation")
            try:
                nav_results = await self._explore_navigation(page)
                step.status = "passed"
                step.details = f"Clicked {len(nav_results)} elements, visited {len(nav_results)} pages"
            except Exception as e:
                step.status = "failed"
                step.details = str(e)
            result.steps.append(step)

            # Screenshot for artifacts
            ss_path = self.artifacts_dir / f"{form_name}_var{variation}.png"
            await page.screenshot(path=str(ss_path), full_page=True)
            step = TestStepResult(step="6", action="screenshot")
            step.status = "passed"
            step.screenshot_path = str(ss_path)
            result.steps.append(step)
            result.artifacts.append(str(ss_path))

            result.status = "passed"
            result.total_duration_ms = int(
                sum(s.duration_ms for s in result.steps)
            )

        except Exception as e:
            result.status = "failed"
            last_step = result.steps[-1] if result.steps else TestStepResult(step="0", action="error")
            last_step.status = "failed"
            last_step.details = str(e)
        finally:
            await page.close()

        return result

    async def _fill_form(self, page: Page, test_data: dict[str, Any]):
        """Auto-discover form fields and fill them with test data."""
        # Get all interactive elements
        inputs = await page.query_selector_all("input, select, textarea")

        for field_name, field_value in test_data.items():
            # Try to find matching input by various strategies
            element = await self._find_field(page, field_name)

            if element:
                tag = await element.evaluate("el => el.tagName.toLowerCase()")

                if tag == "input":
                    input_type = await element.evaluate("el => el.type || 'text'")
                    if input_type == "checkbox":
                        if field_value:
                            await element.check()
                        else:
                            await element.uncheck()
                    elif input_type == "file":
                        await element.set_input_files(str(field_value)) if field_value else None
                    else:
                        await element.fill(str(field_value))
                elif tag in ("select",):
                    await self._fill_select(element, str(field_value))
                elif tag == "textarea":
                    await element.fill(str(field_value))

    async def _find_field(self, page: Page, field_name: str) -> Any:
        """Find a form field using multiple strategies."""
        selectors = [
            f'[name="{field_name}"]',
            f'[id="{field_name}"]',
            f'[name="{field_name.lower()}"]',
            f'label:has-text("{field_name}")',
            f'//label[normalize-space(text())="{field_name}"]//following::input',
        ]

        for selector in selectors:
            try:
                if selector.startswith("//"):
                    # XPath
                    element = await page.locator(selector).first
                    if await element.count() > 0:
                        return element
                else:
                    element = await page.locator(selector).first
                    if await element.count() > 0:
                        return element
            except Exception:
                continue

        # Fallback: find by placeholder or aria-label
        try:
            element = await page.locator(f'[placeholder="{field_name}"]').first
            if await element.count() > 0:
                return element
        except Exception:
            pass

        return None

    async def _fill_select(self, element: Any, value: str):
        """Fill a select dropdown."""
        await element.select_option(label=value)

    async def _submit_form(self, page: Page):
        """Submit the form by finding and clicking submit buttons."""
        # Try multiple submit strategies
        submit_selectors = [
            'input[type="submit"]',
            'button[type="submit"]',
            'button[class*="submit"]',
            'input[class*="submit"]',
            '[type="submit"]',
        ]

        for selector in submit_selectors:
            btns = await page.locator(selector).all()
            if btns:
                await btns[0].click()
                await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                return

        # Fallback: press Enter in the last focused input
        inputs = await page.query_selector_all("input:not([type='hidden']), textarea")
        if inputs:
            await inputs[-1].press("Enter")
            await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

    async def _verify_page(self, page: Page):
        """Verify the page after form submission."""
        # Check URL changed or stayed (both are valid depending on form behavior)
        url = page.url
        title = await page.title()

        # Check for error indicators
        error_indicators = [
            'text="error"',
            'text="Error"',
            'text="ERROR"',
            '[class*="error"]',
            '[class*="alert"]',
        ]

        for selector in error_indicators:
            try:
                errors = await page.locator(selector).all()
                if errors:
                    text = await errors[0].text_content()
                    raise AssertionError(f"Error detected: {text}")
            except Exception as e:
                if "Error detected" in str(e):
                    raise
                continue

        # Success: page loaded without errors
        return {"url": url, "title": title, "status": "ok"}

    async def _explore_navigation(self, page: Page) -> list[dict]:
        """Click all actionable elements and track navigation."""
        visited = set()
        results = []
        base_url = self.target_url

        # Get all clickable elements
        clickable_selectors = "a, button, [role='button'], [onclick], summary"
        elements = await page.locator(clickable_selectors).all()

        # Filter to reasonable targets (skip external links, skip anchor-only links)
        for element in elements[:20]:  # Limit to first 20
            try:
                text = await element.text_content() or ""
                href = await element.get_attribute("href") or ""

                # Skip anchors to same page (#) or external sites
                if href and (href.startswith("#") or not href.startswith(base_url)):
                    continue
                if not text.strip():
                    continue

                action_text = text.strip()[:50] or f"[{href}]"

                await element.click(timeout=5000)
                await page.wait_for_load_state("domcontentloaded", timeout=5000)

                current_url = page.url
                title = await page.title()
                results.append({
                    "action": action_text,
                    "url": current_url,
                    "title": title,
                })
                visited.add(current_url)

            except Exception:
                # Element might have disappeared or navigation failed — continue
                continue

        return results

    async def get_console_logs(self, page: Page) -> list[str]:
        """Get console logs from the current page."""
        logs = []
        page.on("console", lambda msg: logs.append(msg.text))
        return logs

    async def get_network_requests(self, page: Page) -> list[dict]:
        """Capture network request/response info."""
        requests = []

        def on_response(response):
            requests.append({
                "url": response.url,
                "status": response.status,
                "method": response.request.method,
            })

        page.on("response", on_response)
        return requests