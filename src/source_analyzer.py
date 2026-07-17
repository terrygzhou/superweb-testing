"""Phase 1: Source code analyzer — extracts form schemas, routes, and validators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class FieldInfo(BaseModel):
    """A single form field extracted from source code."""

    name: str
    type: str = "text"  # text | email | password | number | date | select | textarea | checkbox | file
    required: bool = True
    validators: list[str] = Field(default_factory=list)
    choices: list[str] = Field(default_factory=list)
    help_text: str = ""


class FormSchema(BaseModel):
    """A form schema extracted from source code."""

    form_name: str
    route: str = ""
    method: str = "POST"
    fields: list[FieldInfo] = Field(default_factory=list)
    source_file: str = ""
    line_numbers: list[int] = Field(default_factory=list)


class RouteInfo(BaseModel):
    """An API/UI route discovered from source."""

    path: str
    methods: list[str] = Field(default_factory=lambda: ["GET"])
    description: str = ""
    source_file: str = ""


class SourceAnalysisResult(BaseModel):
    """Complete analysis result from scanning source code."""

    forms: list[FormSchema] = Field(default_factory=list)
    routes: list[RouteInfo] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


# Patterns for Python web frameworks
_FORM_PATTERNS = re.compile(
    r"""
    (?:
        (?P<form_class)class\s+(\w+)\s*\([Cc]onfirm|Form|Schema|BaseModel|Model)\b.*?:
        |
        (?P<form_field)\s+(\w+)\s*=\s*(?:Form\.)?(\w+)\(
        |
        (?P<pydantic_field>)\s+(\w+)\s*:\s*(\w+)(\s*=\s*Field\(.*?\))?
        |
        (?P<wtforms_field>)\s+(\w+)\s*=\s*(?:wtforms|webargs\.fields)\.(\w+)\(
    )
    """,
    re.VERBOSE,
)

_ROUTE_PATTERNS = re.compile(
    r"""
    (?:
        (?P<fastapi>)@\w+\.(?:get|post|put|delete|patch)\s*\(\s*["'](.*?)["']
        |
        (?P<flask>)@\w+\.(?:route|get|post|put|delete)\s*\(\s*["'](.*?)["']
        |
        (?P<tornado>)default_routes\s*=\s*\[\s*\{.*?"path":\s*["'](.*?)["']
    )
    """,
    re.VERBOSE,
)

_TYPE_MAP: dict[str, str] = {
    "emailfield": "email",
    "email": "email",
    "passwordfield": "password",
    "password": "password",
    "integerfield": "number",
    "integer": "number",
    "int": "number",
    "decimalfield": "number",
    "decimal": "number",
    "float": "number",
    "datefield": "date",
    "date": "date",
    "datetimefield": "date",
    "datetime": "date",
    "selectfield": "select",
    "select": "select",
    "textareawidget": "textarea",
    "textarea": "textarea",
    "checkboxfield": "checkbox",
    "checkbox": "checkbox",
    "filefield": "file",
    "file": "file",
    "str": "text",
    "string": "text",
    "textfield": "text",
}


class SourceAnalyzer:
    """Scan web app source code to extract form schemas, routes, and validators."""

    def __init__(self, source_root: str, form_patterns: list[str] | None = None):
        self.source_root = Path(source_root).expanduser().resolve()
        self.form_patterns = form_patterns or [
            "**/forms.py",
            "**/schemas.py",
            "**/models.py",
            "**/validators.py",
            "**/*_form.py",
            "**/schema*.py",
        ]
        self.route_patterns = [
            "**/routes.py",
            "**/api.py",
            "**/endpoints.py",
            "**/*_router.py",
        ]

    def analyze(self) -> SourceAnalysisResult:
        """Run full source analysis."""
        forms = self._extract_forms()
        routes = self._extract_routes()
        result = SourceAnalysisResult(
            forms=forms,
            routes=routes,
            summary={
                "forms_found": len(forms),
                "routes_found": len(routes),
                "total_fields": sum(len(f.fields) for f in forms),
                "files_scanned": len(set(f.source_file for f in forms) | set(r.source_file for r in routes)),
            },
        )
        return result

    def _extract_forms(self) -> list[FormSchema]:
        """Extract form schemas from source files."""
        forms: list[FormSchema] = []

        for pattern in self.form_patterns:
            for filepath in self.source_root.glob(pattern):
                forms.extend(self._parse_form_file(filepath))

        return forms

    def _parse_form_file(self, filepath: Path) -> list[FormSchema]:
        """Parse a single Python file for form definitions."""
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return []

        forms: list[FormSchema] = []
        lines = content.splitlines()

        # Detect framework and parse accordingly
        if "pydantic" in content.lower() or "BaseModel" in content:
            forms.extend(self._parse_pydantic_schemas(content, lines, str(filepath)))
        elif "wtforms" in content.lower() or "Form(" in content:
            forms.extend(self._parse_wtforms(content, lines, str(filepath)))
        elif "sqlalchemy" in content.lower():
            forms.extend(self._parse_sqlalchemy_models(content, lines, str(filepath)))
        else:
            forms.extend(self._generic_parse(content, lines, str(filepath)))

        return forms

    def _parse_pydantic_schemas(
        self, content: str, lines: list[str], filepath: str
    ) -> list[FormSchema]:
        """Parse Pydantic BaseModel/Schema definitions."""
        forms: list[FormSchema] = []

        # Find class definitions that inherit from BaseModel/Schema
        class_pattern = re.compile(
            r"class\s+(\w+)\s*\([^)]*(?:BaseModel|Schema|Model)[^)]*\)\s*:"
        )

        for match in class_pattern.finditer(content):
            class_name = match.group(1)
            start_line = content[: match.start()].count("\n") + 1

            # Extract fields within the class body
            field_lines = self._extract_class_fields(content, match.end(), lines)
            fields = self._parse_fields(field_lines)

            if fields:
                forms.append(
                    FormSchema(
                        form_name=class_name,
                        fields=fields,
                        source_file=filepath,
                        line_numbers=[start_line],
                    )
                )

        return forms

    def _parse_wtforms(self, content: str, lines: list[str], filepath: str) -> list[FormSchema]:
        """Parse WTForms form definitions."""
        forms: list[FormSchema] = []

        class_pattern = re.compile(r"class\s+(\w+)\s*\([Ff]orm\)\s*:")

        for match in class_pattern.finditer(content):
            class_name = match.group(1)
            field_lines = self._extract_class_fields(content, match.end(), lines)

            fields: list[FieldInfo] = []
            for fl in field_lines:
                field_match = re.match(r"\s+(\w+)\s*=\s*(\w+)\((.*?)\)", fl.strip())
                if field_match:
                    field_name = field_match.group(1)
                    field_type = field_match.group(2).lower()
                    field_info = FieldInfo(
                        name=field_name,
                        type=_TYPE_MAP.get(field_type, "text"),
                    )
                    fields.append(field_info)

            if fields:
                start_line = content[: match.start()].count("\n") + 1
                forms.append(
                    FormSchema(
                        form_name=class_name,
                        fields=fields,
                        source_file=filepath,
                        line_numbers=[start_line],
                    )
                )

        return forms

    def _parse_sqlalchemy_models(
        self, content: str, lines: list[str], filepath: str
    ) -> list[FormSchema]:
        """Parse SQLAlchemy model definitions."""
        forms: list[FormSchema] = []

        class_pattern = re.compile(
            r"class\s+(\w+)\s*\([^)]*(?:Base|Model|ModelBase)[^)]*\)\s*:"
        )

        for match in class_pattern.finditer(content):
            class_name = match.group(1)
            field_lines = self._extract_class_fields(content, match.end(), lines)

            fields: list[FieldInfo] = []
            for fl in field_lines:
                field_match = re.match(r"\s*(\w+)\s*=\s*Column\((.*?)(?:,|$)\)", fl.strip())
                if field_match:
                    field_name = field_match.group(1)
                    col_def = field_match.group(2)
                    field_type = "text"
                    if "Integer" in col_def:
                        field_type = "number"
                    elif "Boolean" in col_def:
                        field_type = "checkbox"
                    elif "Date" in col_def:
                        field_type = "date"
                    elif "Text" in col_def:
                        field_type = "textarea"

                    field_info = FieldInfo(name=field_name, type=field_type)
                    fields.append(field_info)

            if fields:
                start_line = content[: match.start()].count("\n") + 1
                forms.append(
                    FormSchema(
                        form_name=class_name,
                        fields=fields,
                        source_file=filepath,
                        line_numbers=[start_line],
                    )
                )

        return forms

    def _generic_parse(self, content: str, lines: list[str], filepath: str) -> list[FormSchema]:
        """Generic heuristic parsing for unknown frameworks."""
        forms: list[FormSchema] = []

        # Look for dicts that define form/config structures
        form_pattern = re.compile(r'(\w+_form|form_data|schema|payload)\s*=\s*\{')

        for match in form_pattern.finditer(content):
            form_name = match.group(1)
            start_line = content[: match.start()].count("\n") + 1

            fields: list[FieldInfo] = []
            brace_depth = 0
            in_block = False
            block_start = match.end()

            for line in lines[start_line:]:
                for char in line:
                    if char == "{":
                        brace_depth += 1
                        in_block = True
                    elif char == "}":
                        brace_depth -= 1
                        if brace_depth == 0 and in_block:
                            break

                if in_block and ":" in line:
                    field_match = re.match(r'\s*["\']?(\w+)["\']?\s*:', line)
                    if field_match:
                        fields.append(FieldInfo(name=field_match.group(1)))

            if fields:
                forms.append(
                    FormSchema(
                        form_name=form_name,
                        fields=fields,
                        source_file=filepath,
                        line_numbers=[start_line],
                    )
                )

        return forms

    def _extract_class_fields(
        self, content: str, after_pos: int, lines: list[str]
    ) -> list[str]:
        """Extract field definitions from a class body."""
        result: list[str] = []
        brace_depth = 0

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("def "):
                if result and not stripped:
                    break  # empty line after fields = end of block
                continue

            # Check for field assignments
            if re.match(r"\s+\w+\s*[=:]", stripped) or re.match(r"\s+\w+\s*:", stripped):
                result.append(stripped)
            elif stripped.startswith("class ") or (stripped.startswith("def ") and result):
                break

        return result

    def _parse_fields(self, field_lines: list[str]) -> list[FieldInfo]:
        """Parse field definitions into FieldInfo objects."""
        fields: list[FieldInfo] = []

        for line in field_lines:
            # Pattern: field_name: Type = Field(...)
            match = re.match(
                r"(\w+)\s*:\s*(\w+|List\[\w+\]|Optional\[\w+\])"
                r"(?:\s*=\s*(.*?))?$",
                line.strip(),
            )
            if match:
                field_name = match.group(1)
                field_type = match.group(2)
                default = match.group(3) or ""

                # Normalize type
                clean_type = re.sub(r"(?:Optional|List)\[|\]", "", field_type)
                py_type = _TYPE_MAP.get(clean_type.lower(), "text")

                required = "None" not in default and "Optional" not in field_type
                if "optional" in default.lower() or "None" in default:
                    required = False

                # Extract validators from Field(...)
                validators = []
                if "Field(" in default:
                    val_match = re.findall(r"(\w+)=\s*(\w+|['\"][^'\"]*['\"])", default)
                    validators = [f"{k}={v}" for k, v in val_match]

                fields.append(
                    FieldInfo(
                        name=field_name,
                        type=py_type,
                        required=required,
                        validators=validators,
                    )
                )

        return fields

    def _extract_routes(self) -> list[RouteInfo]:
        """Extract API/UI routes from source files."""
        routes: list[RouteInfo] = []

        for pattern in self.route_patterns:
            for filepath in self.source_root.glob(pattern):
                routes.extend(self._parse_routes_file(filepath))

        return routes

    def _parse_routes_file(self, filepath: Path) -> list[RouteInfo]:
        """Parse route definitions from a Python file."""
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return []

        routes: list[RouteInfo] = []

        # FastAPI/Flask style: @app.get("/path")
        route_pattern = re.compile(
            r"@[\w.]+\.(get|post|put|delete|patch|route)\s*\(\s*['\"]([^'\"]+)['\"]"
        )

        for match in route_pattern.finditer(content):
            method = match.group(1).upper()
            path = match.group(2)
            routes.append(
                RouteInfo(
                    path=path,
                    methods=[method],
                    source_file=str(filepath),
                )
            )

        return routes

    def save_results(self, result: SourceAnalysisResult, output_path: str) -> str:
        """Save analysis results to JSON."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return str(out)