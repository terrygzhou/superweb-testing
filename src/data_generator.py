"""Phase 2: Test data generator — feeds form schemas to LLM → generates realistic test data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field


class TestRecord(BaseModel):
    """A single test data record for one form."""

    form_name: str
    data: dict[str, Any]
    variation: int
    notes: str = ""


class TestDataset(BaseModel):
    """Collection of test data records across all forms."""

    records: list[TestRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# LLM prompt template for test data generation
_GENERATION_PROMPT = """You are a test data generator. Given form schema definitions, generate realistic test data.

Form schemas (JSON):
{schemas_json}

Requirements:
- Generate {n_variations} test data variations per form
- Each variation should test different edge cases:
  * Variation 1: Happy path (all required fields, valid data)
  * Variation 2: Boundary values (min/max lengths, limits)
  * Variation 3: Special characters (unicode, emojis, SQL injection attempts)
- For email fields: use format "test{N}@example.com"
- For password fields: use format "SecurePass{N}!"
- For numeric fields: include 0, negative, very large numbers
- For optional fields: sometimes omit, sometimes include

Return ONLY valid JSON matching this schema:
{{
  "records": [
    {{
      "form_name": "FormName",
      "data": {{"field1": "value1", "field2": "value2"}},
      "variation": 1,
      "notes": "description of what this tests"
    }}
  ]
}}
"""


class DataGenerator:
    """Generate realistic test data from form schemas using an LLM."""

    def __init__(
        self,
        llm_base_url: str = "http://172.25.0.1:8080",
        model: str = "Qwen3.6-27B",
        n_variations: int = 3,
    ):
        self.llm_base_url = llm_base_url
        self.model = model
        self.n_variations = n_variations
        self._client = httpx.AsyncClient(base_url=llm_base_url, timeout=120.0)

    async def generate(self, schemas: list[dict]) -> TestDataset:
        """Generate test data from form schemas via LLM."""
        schemas_json = json.dumps(schemas, indent=2, ensure_ascii=False)
        prompt = _GENERATION_PROMPT.format(
            schemas_json=schemas_json,
            n_variations=self.n_variations,
        )

        response = await self._call_llm(prompt)
        records = self._parse_response(response)

        return TestDataset(
            records=records,
            metadata={
                "model": self.model,
                "n_variations": self.n_variations,
                "forms_processed": len(schemas),
                "total_records": len(records),
            },
        )

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM endpoint."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def generate_fallback(self, schemas: list[dict]) -> TestDataset:
        """Rule-based fallback when LLM is unavailable."""
        records: list[TestRecord] = []

        for schema in schemas:
            form_name = schema.get("form_name", "UnknownForm")
            fields = schema.get("fields", [])

            for var in range(1, self.n_variations + 1):
                data: dict[str, Any] = {}
                for field in fields:
                    fname = field.get("name", "field")
                    ftype = field.get("type", "text")
                    data[fname] = self._fallback_value(ftype, var)

                notes = f"Variation {var}: {'happy path' if var == 1 else 'boundary' if var == 2 else 'special chars'}"
                records.append(
                    TestRecord(
                        form_name=form_name,
                        data=data,
                        variation=var,
                        notes=notes,
                    )
                )

        return TestDataset(
            records=records,
            metadata={"generator": "fallback", "n_variations": self.n_variations},
        )

    def _fallback_value(self, field_type: str, variation: int) -> Any:
        """Generate a fallback value based on field type and variation."""
        values = {
            "text": ["Test User", "Tést Usér!", "T" * 255],
            "email": ["test@example.com", "test-{}@example.com".format(variation), "tést@example.com"],
            "password": ["SecurePass1!", "Password{}!".format("X" * 20), "p"],
            "number": [42, 999999999, -1],
            "date": ["2025-01-15", "2099-12-31", "1970-01-01"],
            "select": ["option1", "option2", ""],
            "textarea": ["A comment", "A" * 500, ""],
            "checkbox": [True, False, True],
            "file": ["test.txt", "", None],
        }
        vals = values.get(field_type, ["value", "value2", "val"])
        return vals[variation - 1] if variation <= len(vals) else vals[0]

    def _parse_response(self, response_text: str) -> list[TestRecord]:
        """Parse LLM JSON response into TestRecord objects."""
        # Try to extract JSON from response
        json_str = response_text.strip()

        # Handle markdown code blocks
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Try to find JSON-like structure
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(json_str[start:end])
            else:
                return []

        records: list[TestRecord] = []
        for record in data.get("records", []):
            try:
                records.append(TestRecord(**record))
            except (TypeError, ValueError):
                continue

        return records

    def save(self, dataset: TestDataset, output_path: str) -> str:
        """Save test dataset to JSON."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
        return str(out)

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()