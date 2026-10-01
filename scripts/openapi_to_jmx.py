#!/usr/bin/env python3
"""Build a JMeter 5.6.3 test plan from an OpenAPI 3 or Swagger 2 document.

The plan confirms one call to every selected operation. Before those calls, a
setUp thread asks Amazon Cognito for one machine-to-machine access token and
stores it in a JMeter property. Later threads reuse that token until shortly
before it expires. API calls go to http://localhost:8080 unless overridden,
and every call sends the fixed X-API-KEY header.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import re
import sys
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from yaml_subset import YamlError, load_yaml


HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
JSON_CONTENT = "application/json"
FORM_CONTENT = "application/x-www-form-urlencoded"
MULTIPART_CONTENT = "multipart/form-data"
TEXT_CONTENT = "text/plain"
XML_CONTENT = "application/xml"
BINARY_CONTENT = "application/octet-stream"
CONTENT_PREFERENCE = (
    JSON_CONTENT,
    FORM_CONTENT,
    MULTIPART_CONTENT,
    TEXT_CONTENT,
    XML_CONTENT,
    BINARY_CONTENT,
)
ALPHANUMERIC = "abcdefghijklmnopqrstuvwxyz0123456789"
LETTER = "abcdefghijklmnopqrstuvwxyz"


class SpecError(Exception):
    """Raised when the OpenAPI document cannot be turned into requests."""


class NoOperationsError(SpecError):
    """Raised when the document has no API operation left to call."""


class UnsupportedPattern(Exception):
    """Raised when a schema pattern is outside the small generator."""


def load_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    try:
        if path.suffix.lower() == ".json" or stripped.startswith("{"):
            return json.loads(text)
        return load_yaml(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"JSONとして読めません: {exc}") from exc
    except YamlError as exc:
        raise SpecError(str(exc)) from exc


class DocumentIndex:
    """Resolve local and relative-file $ref pointers."""

    def __init__(self, path: Path, root: Any) -> None:
        self.path = path.resolve()
        self.root = root
        self.documents = {self.path: root}
        version = ""
        if isinstance(root, dict):
            version = str(root.get("openapi") or root.get("swagger") or "")
        self.version = version

    def resolve(self, base_file: Path, ref: str) -> tuple[Any, Path]:
        if ref.startswith("#"):
            return pointer_lookup(self.documents[base_file], ref), base_file
        file_part, hash_sign, fragment = ref.partition("#")
        target = (base_file.parent / file_part).resolve()
        if target not in self.documents:
            if not target.is_file():
                raise SpecError(f"$ref のファイルが見つかりません: {ref}")
            self.documents[target] = load_document(target)
        pointer = f"#{fragment}" if hash_sign else "#"
        if pointer == "#":
            return self.documents[target], target
        return pointer_lookup(self.documents[target], pointer), target


def pointer_lookup(document: Any, pointer: str) -> Any:
    if pointer in {"", "#"}:
        return document
    if not pointer.startswith("#/"):
        raise SpecError(f"対応していない $ref です: {pointer}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = urllib.parse.unquote(raw_part).replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise SpecError(f"$ref の参照先がありません: {pointer}")
    return current


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def deref_schema(schema: Any, index: DocumentIndex, base: Path, stack: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "string"}
    if "$ref" in schema:
        ref = str(schema["$ref"])
        marker = f"{base}::{ref}"
        if marker in stack:
            return {"type": "string"}
        try:
            target, new_base = index.resolve(base, ref)
        except SpecError:
            raise
        resolved = deref_schema(target, index, new_base, stack + (marker,))
        if index.version.startswith("3.1"):
            overlay = {key: value for key, value in schema.items() if key != "$ref"}
            if overlay:
                resolved = merge_schemas([resolved, deref_schema(overlay, index, base, stack + (marker,))])
        return resolved
    if isinstance(schema.get("allOf"), list) and schema["allOf"]:
        parts = [deref_schema(part, index, base, stack) for part in schema["allOf"]]
        siblings = {key: value for key, value in schema.items() if key != "allOf"}
        if siblings:
            parts.append(deref_schema(siblings, index, base, stack))
        return merge_schemas(parts)
    return schema


def merge_schemas(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_properties = part.get("properties")
        if isinstance(part_properties, dict):
            properties.update(part_properties)
        part_required = part.get("required")
        if isinstance(part_required, list):
            for name in part_required:
                if name not in required:
                    required.append(str(name))
        for key, value in part.items():
            if key in {"properties", "required", "allOf"}:
                continue
            if key not in merged:
                merged[key] = value
                continue
            if key in {"minLength", "minimum"} and is_number(value) and is_number(merged[key]):
                merged[key] = max(merged[key], value)
            elif key in {"maxLength", "maximum"} and is_number(value) and is_number(merged[key]):
                merged[key] = min(merged[key], value)
            elif key == "nullable":
                merged[key] = bool(merged[key] or value)
            elif key == "enum" and isinstance(value, list) and isinstance(merged[key], list):
                allowed = [item for item in merged[key] if item in value]
                merged[key] = allowed or value
            else:
                merged[key] = value
    if properties:
        merged["properties"] = properties
    if required:
        merged["required"] = required
    return merged


def schema_types(schema: dict[str, Any]) -> list[str]:
    raw = schema.get("type")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [raw]
    return []


def generate_value(
    schema: Any,
    index: DocumentIndex,
    base: Path,
    rng: random.Random,
    *,
    depth: int = 0,
    allow_null: bool = False,
    required_only: bool = False,
    files: list[tuple[str, bytes]] | None = None,
    file_prefix: str = "body",
) -> Any:
    schema = deref_schema(schema, index, base)
    if depth > 6:
        kind = schema_types(schema)
        if "array" in kind:
            return []
        if "object" in kind or "properties" in schema:
            return {}
        return "depth-limit"
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return rng.choice(list(schema["enum"]))
    for key in ("oneOf", "anyOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            return generate_value(
                rng.choice(options),
                index,
                base,
                rng,
                depth=depth + 1,
                allow_null=allow_null,
                required_only=required_only,
                files=files,
                file_prefix=file_prefix,
            )
    types = schema_types(schema)
    nullable = bool(schema.get("nullable")) or "null" in types
    concrete = [item for item in types if item != "null"]
    if nullable and allow_null and rng.random() < 0.15:
        return None
    if not concrete:
        if "properties" in schema or "additionalProperties" in schema:
            concrete = ["object"]
        elif "items" in schema or "prefixItems" in schema:
            concrete = ["array"]
        elif "default" in schema:
            return schema["default"]
        elif "example" in schema:
            return schema["example"]
        else:
            concrete = ["string"]
    kind = concrete[0] if len(concrete) == 1 else rng.choice(concrete)
    if kind == "object":
        return generate_object(
            schema, index, base, rng, depth, required_only, files, file_prefix
        )
    if kind == "array":
        return generate_array(
            schema, index, base, rng, depth, required_only, files, file_prefix
        )
    if kind == "integer":
        return generate_integer(schema, rng)
    if kind == "number":
        return generate_number(schema, rng)
    if kind == "boolean":
        return bool(rng.randint(0, 1))
    if kind in {"string", "file"}:
        return generate_string(schema, rng)
    return generate_string(schema, rng)


def generate_object(
    schema: dict[str, Any],
    index: DocumentIndex,
    base: Path,
    rng: random.Random,
    depth: int,
    required_only: bool,
    files: list[tuple[str, bytes]] | None,
    file_prefix: str,
) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = {str(item) for item in schema.get("required", []) if isinstance(schema.get("required"), list)}
    result: dict[str, Any] = {}
    for name, property_schema in properties.items():
        include = (not required_only) or name in required
        if not include:
            continue
        resolved = deref_schema(property_schema, index, base)
        if resolved.get("readOnly") is True:
            continue
        result[str(name)] = generate_value(
            property_schema,
            index,
            base,
            rng,
            depth=depth + 1,
            allow_null=name not in required,
            required_only=required_only,
            files=files,
            file_prefix=f"{file_prefix}-{name}",
        )
    minimum = schema.get("minProperties")
    additional = schema.get("additionalProperties")
    if is_number(minimum) and len(result) < int(minimum) and isinstance(additional, dict):
        next_index = 1
        while len(result) < int(minimum):
            key = f"extra{next_index}"
            next_index += 1
            result[key] = generate_value(
                additional,
                index,
                base,
                rng,
                depth=depth + 1,
                allow_null=False,
                required_only=required_only,
                files=files,
                file_prefix=f"{file_prefix}-{key}",
            )
    if not result and isinstance(additional, dict):
        result["extra1"] = generate_value(
            additional,
            index,
            base,
            rng,
            depth=depth + 1,
            allow_null=False,
            required_only=required_only,
            files=files,
            file_prefix=f"{file_prefix}-extra1",
        )
    return result


def generate_array(
    schema: dict[str, Any],
    index: DocumentIndex,
    base: Path,
    rng: random.Random,
    depth: int,
    required_only: bool,
    files: list[tuple[str, bytes]] | None,
    file_prefix: str,
) -> list[Any]:
    minimum = int(schema.get("minItems", 1))
    stated_max = int(schema.get("maxItems", max(minimum, 2)))
    maximum = max(minimum, min(stated_max, max(minimum, 2)))
    count = rng.randint(minimum, maximum)
    prefix = schema.get("prefixItems") if isinstance(schema.get("prefixItems"), list) else []
    item_schema = schema.get("items", {"type": "string"})
    values: list[Any] = []
    seen: set[str] = set()
    for position in range(count):
        chosen = prefix[position] if position < len(prefix) else item_schema
        for _attempt in range(8):
            value = generate_value(
                chosen,
                index,
                base,
                rng,
                depth=depth + 1,
                allow_null=False,
                required_only=required_only,
                files=files,
                file_prefix=f"{file_prefix}-{position}",
            )
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            if not schema.get("uniqueItems") or marker not in seen:
                seen.add(marker)
                values.append(value)
                break
    return values


def integer_bounds(schema: dict[str, Any]) -> tuple[int, int]:
    lower = schema.get("minimum")
    upper = schema.get("maximum")
    exclusive_lower = schema.get("exclusiveMinimum")
    exclusive_upper = schema.get("exclusiveMaximum")
    if isinstance(exclusive_lower, bool):
        if exclusive_lower and is_number(lower):
            lower = math.floor(float(lower)) + 1
        elif is_number(lower):
            lower = math.ceil(float(lower))
        else:
            lower = None
    elif is_number(exclusive_lower):
        lower = math.floor(float(exclusive_lower)) + 1
    elif is_number(lower):
        lower = math.ceil(float(lower))
    else:
        lower = None
    if isinstance(exclusive_upper, bool):
        if exclusive_upper and is_number(upper):
            upper = math.ceil(float(upper)) - 1
        elif is_number(upper):
            upper = math.floor(float(upper))
        else:
            upper = None
    elif is_number(exclusive_upper):
        upper = math.ceil(float(exclusive_upper)) - 1
    elif is_number(upper):
        upper = math.floor(float(upper))
    else:
        upper = None
    if lower is None and upper is None:
        return 0, 1000
    if lower is None:
        assert upper is not None
        lower = 0 if upper >= 0 else upper - 1000
        if lower > upper:
            lower = upper
    if upper is None:
        upper = lower + 1000
    if lower > upper:
        lower = upper
    return int(lower), int(upper)


def generate_integer(schema: dict[str, Any], rng: random.Random) -> int:
    lower, upper = integer_bounds(schema)
    step = schema.get("multipleOf")
    if is_number(step) and float(step) > 0 and float(step).is_integer():
        step_int = int(step)
        start = math.ceil(lower / step_int) * step_int
        end = math.floor(upper / step_int) * step_int
        if start > end:
            return lower
        slots = int((end - start) / step_int)
        return start + rng.randint(0, slots) * step_int
    return rng.randint(lower, upper)


def generate_number(schema: dict[str, Any], rng: random.Random) -> float:
    lower, upper = integer_bounds(schema)
    if lower == upper:
        return float(lower)
    value = rng.uniform(lower, upper)
    step = schema.get("multipleOf")
    if is_number(step) and float(step) > 0:
        step_float = float(step)
        units = round((value - lower) / step_float)
        value = lower + units * step_float
        if value < lower:
            value = lower
        if value > upper:
            value = upper
    return round(value, 4)


def generate_string(schema: dict[str, Any], rng: random.Random) -> str:
    minimum = int(schema.get("minLength", 0))
    has_maximum = "maxLength" in schema
    # A missing maxLength must not chop a uuid, date, or email down to a short token.
    # An explicit maxLength is honored, with a cap so one property cannot create a huge body.
    ceiling = int(schema["maxLength"]) if has_maximum else max(minimum, 64)
    maximum = max(minimum, min(ceiling, max(minimum, 64)))
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        generated = generate_from_pattern(rng, pattern)
        if generated is not None and minimum <= len(generated) <= maximum:
            return generated
    value = string_for_format(str(schema.get("format") or ""), rng, minimum, maximum)
    if not has_maximum:
        if len(value) < minimum:
            return value + random_text(rng, minimum - len(value))
        return value
    if len(value) < minimum:
        value = value + random_text(rng, minimum - len(value))
    if len(value) > maximum:
        value = value[:maximum]
    if value == "" and minimum == 0 and maximum == 0:
        return ""
    if value == "":
        value = random_text(rng, max(1, minimum))
    return value


def string_for_format(fmt: str, rng: random.Random, minimum: int, maximum: int) -> str:
    if fmt == "date":
        return f"{rng.randint(2020, 2030):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    if fmt in {"date-time", "datetime"}:
        return (
            f"{rng.randint(2020, 2030):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            f"T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
        )
    if fmt == "email":
        return f"user{rng.randint(1000, 999999)}@example.com"
    if fmt == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if fmt in {"uri", "url", "uri-reference"}:
        return f"https://example.com/r/{random_text(rng, 8)}"
    if fmt in {"hostname", "idn-hostname"}:
        return f"host-{random_text(rng, 6)}.example.com"
    if fmt in {"ipv4", "ip"}:
        return ".".join(str(rng.randint(1, 254)) for _ in range(4))
    if fmt == "ipv6":
        return ":".join(f"{rng.randint(0, 65535):x}" for _ in range(8))
    if fmt == "byte":
        return base64.b64encode(rng.randbytes(12)).decode("ascii")
    if fmt in {"password", "binary"}:
        return random_text(rng, max(minimum, 12))
    length = minimum if minimum > 0 else min(8, maximum)
    length = max(1, min(length, maximum if maximum > 0 else length))
    return random_text(rng, length)


def random_text(rng: random.Random, length: int) -> str:
    if length <= 0:
        return ""
    return "".join(rng.choice(ALPHANUMERIC) for _ in range(length))


def generate_from_pattern(rng: random.Random, pattern: str) -> str | None:
    if len(pattern) > 180:
        return None
    try:
        parser = _PatternParser(pattern)
        ast = parser.parse_alt()
        if parser.position != len(pattern):
            return None
        pieces: list[str] = []
        _render_pattern(ast, rng, pieces, [40])
        return "".join(pieces)
    except UnsupportedPattern:
        return None


def _render_pattern(node: Any, rng: random.Random, pieces: list[str], budget: list[int]) -> None:
    kind = node[0]
    if budget[0] <= 0:
        return
    if kind == "lit":
        pieces.append(node[1][: budget[0]])
        budget[0] -= len(node[1])
    elif kind == "class":
        pieces.append(rng.choice(node[1]))
        budget[0] -= 1
    elif kind == "cat":
        for child in node[1]:
            _render_pattern(child, rng, pieces, budget)
    elif kind == "alt":
        _render_pattern(rng.choice(node[1]), rng, pieces, budget)
    elif kind == "rep":
        lower, upper, child = node[1], node[2], node[3]
        count = rng.randint(lower, upper)
        for _ in range(count):
            if budget[0] <= 0:
                break
            _render_pattern(child, rng, pieces, budget)
    else:
        raise UnsupportedPattern(kind)


class _PatternParser:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.position = 0
        if pattern.startswith("^"):
            self.position = 1

    def parse_alt(self) -> Any:
        options = [self.parse_concat()]
        while self.peek() == "|":
            self.position += 1
            options.append(self.parse_concat())
        node: Any = options[0] if len(options) == 1 else ("alt", options)
        if self.peek() == "$" and self.position == len(self.pattern) - 1:
            self.position += 1
        return node

    def parse_concat(self) -> Any:
        parts: list[Any] = []
        while not self.done() and self.peek() not in ")|":
            if self.peek() == "$" and self.position == len(self.pattern) - 1:
                break
            parts.append(self.parse_repeat())
        if not parts:
            return ("lit", "")
        if len(parts) == 1:
            return parts[0]
        return ("cat", parts)

    def parse_repeat(self) -> Any:
        atom = self.parse_atom()
        if self.done():
            return atom
        char = self.peek()
        if char == "*":
            self.position += 1
            return ("rep", 0, 4, atom)
        if char == "+":
            self.position += 1
            return ("rep", 1, 4, atom)
        if char == "?":
            self.position += 1
            return ("rep", 0, 1, atom)
        if char == "{":
            lower, upper = self.parse_brace()
            upper = min(upper, max(lower, 12))
            return ("rep", lower, upper, atom)
        return atom

    def parse_brace(self) -> tuple[int, int]:
        self.position += 1
        start = self.position
        while not self.done() and self.peek() != "}":
            self.position += 1
        if self.done() or self.peek() != "}":
            raise UnsupportedPattern("brace")
        body = self.pattern[start:self.position]
        self.position += 1
        if not re.fullmatch(r"\d+(,\d*)?", body):
            raise UnsupportedPattern("brace")
        if "," not in body:
            value = int(body)
            return value, value
        left, right = body.split(",", 1)
        lower = int(left)
        upper = int(right) if right else lower + 4
        if upper < lower:
            raise UnsupportedPattern("brace")
        return lower, upper

    def parse_atom(self) -> Any:
        if self.done():
            raise UnsupportedPattern("end")
        char = self.peek()
        if char == "(":
            self.position += 1
            if self.pattern.startswith("?:", self.position):
                self.position += 2
            elif self.peek() == "?":
                raise UnsupportedPattern("group")
            node = self.parse_alt()
            if self.peek() != ")":
                raise UnsupportedPattern("group")
            self.position += 1
            return node
        if char == "[":
            return ("class", self.parse_class())
        if char == "\\":
            return ("class", self.parse_escape_chars())
        if char == ".":
            self.position += 1
            return ("class", list(ALPHANUMERIC))
        if char in "*+?{)|":
            raise UnsupportedPattern("quantifier")
        self.position += 1
        return ("lit", char)

    def parse_class(self) -> list[str]:
        self.position += 1
        negated = False
        if self.peek() == "^":
            negated = True
            self.position += 1
        chars: list[str] = []
        while not self.done() and self.peek() != "]":
            if self.peek() == "\\":
                chars.extend(self.parse_escape_chars())
                continue
            start = self.peek()
            self.position += 1
            if self.peek() == "-" and self.position + 1 < len(self.pattern) and self.pattern[self.position + 1] != "]":
                self.position += 1
                end = self.peek()
                self.position += 1
                if ord(start) <= ord(end):
                    chars.extend(chr(code) for code in range(ord(start), ord(end) + 1))
            else:
                chars.append(start)
        if self.peek() != "]":
            raise UnsupportedPattern("class")
        self.position += 1
        safe = [char for char in chars if char.isprintable() and char not in "\r\n\t"]
        if negated:
            universe = list(ALPHANUMERIC)
            safe = [char for char in universe if char not in set(safe)]
        if not safe:
            raise UnsupportedPattern("class")
        return safe[:80]

    def parse_escape_chars(self) -> list[str]:
        self.position += 1
        if self.done():
            raise UnsupportedPattern("escape")
        char = self.peek()
        self.position += 1
        if char == "d":
            return list("0123456789")
        if char == "w":
            return list(ALPHANUMERIC + "_")
        if char == "s":
            return [" "]
        if char in {"D", "W", "S"}:
            return list(LETTER)
        if char in {"n", "t", "r"}:
            return [" "]
        if char.isdigit():
            raise UnsupportedPattern("backref")
        return [char]

    def peek(self) -> str:
        if self.done():
            return ""
        return self.pattern[self.position]

    def done(self) -> bool:
        return self.position >= len(self.pattern)


class Operation:
    def __init__(
        self,
        method: str,
        raw_path: str,
        path: str,
        operation_id: str,
        tag: str,
        deprecated: bool,
        summary: str,
        query: list[tuple[str, str]],
        headers: list[tuple[str, str]],
        cookie_header: str,
        content_type: str,
        body: str,
        multipart_fields: list[tuple[str, str]],
        files: list[tuple[str, str, str]],
        accept: str,
    ) -> None:
        self.method = method
        self.raw_path = raw_path
        self.path = path
        self.operation_id = operation_id
        self.tag = tag
        self.deprecated = deprecated
        self.summary = summary
        self.query = query
        self.headers = headers
        self.cookie_header = cookie_header
        self.content_type = content_type
        self.body = body
        self.multipart_fields = multipart_fields
        self.files = files
        self.accept = accept

    @property
    def sampler_name(self) -> str:
        label = f"{self.method} {self.path.split('?', 1)[0]}"
        if self.operation_id:
            label = f"{label} [{self.operation_id}]"
        if self.deprecated:
            label = f"[deprecated] {label}"
        return label


def build_operations(index: DocumentIndex, options: argparse.Namespace, rng: random.Random) -> tuple[list[Operation], list[tuple[str, bytes]]]:
    root = index.root
    if not isinstance(root, dict):
        raise SpecError("OpenAPI文書の先頭はオブジェクトである必要があります。")
    paths = root.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise NoOperationsError("paths にAPIが定義されていません。")
    base_path = resolve_base_path(root, options)
    method_filter = {item.strip().lower() for item in options.methods.split(",") if item.strip()}
    files: list[tuple[str, bytes]] = []
    operations: list[Operation] = []
    for raw_path in paths:
        path_item = paths[raw_path]
        path_item = follow_ref(path_item, index, index.path)
        if not isinstance(path_item, dict) or not isinstance(raw_path, str):
            continue
        path_parameters = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
        for method in HTTP_METHODS:
            if method not in path_item:
                continue
            if method_filter and method not in method_filter:
                continue
            operation = follow_ref(path_item[method], index, index.path)
            if not isinstance(operation, dict):
                continue
            if operation.get("deprecated") is True and options.skip_deprecated:
                continue
            built = build_operation(
                index,
                raw_path,
                base_path,
                method,
                path_item,
                path_parameters,
                operation,
                rng,
                options.required_only,
                files,
            )
            operations.append(built)
    if not operations:
        raise NoOperationsError("条件に合うAPI operation がありません。")
    operations.sort(key=lambda item: (item.tag, item.raw_path, HTTP_METHODS.index(item.method.lower())))
    seen: dict[str, int] = {}
    for operation in operations:
        count = seen.get(operation.sampler_name, 0) + 1
        seen[operation.sampler_name] = count
        if count > 1:
            operation.operation_id = operation.operation_id or operation.method
            # sampler_name is derived, so disambiguate by mutating operation_id.
            operation.operation_id = f"{operation.operation_id}-{count}"
    return operations, files


def follow_ref(value: Any, index: DocumentIndex, base: Path) -> Any:
    seen: set[str] = set()
    current = value
    current_base = base
    while isinstance(current, dict) and "$ref" in current:
        ref = str(current["$ref"])
        marker = f"{current_base}::{ref}"
        if marker in seen:
            break
        seen.add(marker)
        target, current_base = index.resolve(current_base, ref)
        if index.version.startswith("3.1") and isinstance(target, dict):
            overlay = {key: item for key, item in current.items() if key != "$ref"}
            if overlay:
                merged = dict(target)
                merged.update(overlay)
                current = merged
                continue
        current = target
    return current


def resolve_base_path(root: dict[str, Any], options: argparse.Namespace) -> str:
    if options.base_path == "none":
        return ""
    if options.base_path != "auto":
        text = options.base_path.strip()
        if text and not text.startswith("/"):
            text = "/" + text
        return text.rstrip("/")
    if "swagger" in root and isinstance(root.get("basePath"), str):
        return str(root["basePath"]).rstrip("/")
    servers = root.get("servers")
    if not isinstance(servers, list) or not servers:
        return ""
    index = options.server_index
    if index < 0 or index >= len(servers) or not isinstance(servers[index], dict):
        raise SpecError(f"server-index {index} は servers の範囲外です。")
    return server_path(servers[index])


def server_path(server: dict[str, Any]) -> str:
    url = str(server.get("url") or "")
    variables = server.get("variables") if isinstance(server.get("variables"), dict) else {}
    for name, variable in variables.items():
        default = ""
        if isinstance(variable, dict):
            default = str(variable.get("default") or "")
        url = url.replace("{" + str(name) + "}", urllib.parse.quote(default, safe=""))
    if url.startswith("/"):
        path = url
    else:
        path = urllib.parse.urlsplit(url).path
    return path.rstrip("/")


def build_operation(
    index: DocumentIndex,
    raw_path: str,
    base_path: str,
    method: str,
    path_item: dict[str, Any],
    path_parameters: list[Any],
    operation: dict[str, Any],
    rng: random.Random,
    required_only: bool,
    files: list[tuple[str, bytes]],
) -> Operation:
    parameters = merge_parameters(index, path_parameters, operation.get("parameters"))
    path_values: dict[str, str] = {}
    query: list[tuple[str, str]] = []
    headers: list[tuple[str, str]] = []
    cookies: list[tuple[str, str]] = []
    form_fields: list[tuple[str, str]] = []
    swagger_body: Any = None
    swagger_body_required = False
    for parameter in parameters:
        location = str(parameter.get("in") or "")
        name = str(parameter.get("name") or "")
        if not name:
            continue
        required = bool(parameter.get("required")) or location == "path"
        if required_only and not required and location != "path":
            continue
        if location == "body":
            swagger_body = parameter.get("schema", {"type": "object"})
            swagger_body_required = required
            continue
        if location == "formData":
            value = value_for_parameter(parameter, index, rng, required_only, files, name)
            form_fields.append((name, stringify(value)))
            continue
        schema = parameter.get("schema")
        if not isinstance(schema, dict):
            schema = schema_from_swagger_parameter(parameter)
        value = generate_value(
            schema,
            index,
            index.path,
            rng,
            allow_null=not required,
            required_only=required_only,
            files=files,
            file_prefix=name,
        )
        if value is None:
            continue
        text = stringify(value)
        if location == "path":
            path_values[name] = text
        elif location == "query":
            query.append((name, text))
        elif location == "header":
            headers.append((name, text))
        elif location == "cookie":
            cookies.append((name, text))
    headers = [(name, value) for name, value in headers if name.lower() not in {"accept", "authorization", "x-api-key"}]
    rendered_path = raw_path
    for name, value in path_values.items():
        rendered_path = rendered_path.replace("{" + name + "}", urllib.parse.quote(value, safe=""))
    if re.search(r"\{[^}]+\}", rendered_path):
        missing = ", ".join(re.findall(r"\{([^}]+)\}", rendered_path))
        raise SpecError(f"{method.upper()} {raw_path} のパスパラメータが足りません: {missing}")
    full_path = join_paths(base_path, rendered_path)
    content_type, body, multipart_fields, file_args = build_body(
        index,
        operation,
        method,
        swagger_body,
        swagger_body_required,
        form_fields,
        rng,
        required_only,
        files,
        raw_path,
    )
    if query and body_uses_arguments(content_type):
        full_path = append_query(full_path, query)
        query = []
    elif query and content_type:
        full_path = append_query(full_path, query)
        query = []
    accept = accept_header(operation, index)
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies)
    tags = operation.get("tags")
    tag = str(tags[0]) if isinstance(tags, list) and tags else "default"
    summary = str(operation.get("summary") or operation.get("description") or "")
    return Operation(
        method=method.upper(),
        raw_path=raw_path,
        path=full_path,
        operation_id=str(operation.get("operationId") or ""),
        tag=tag,
        deprecated=bool(operation.get("deprecated")),
        summary=summary,
        query=query,
        headers=headers,
        cookie_header=cookie_header,
        content_type=content_type,
        body=body,
        multipart_fields=multipart_fields,
        files=file_args,
        accept=accept,
    )


def body_uses_arguments(content_type: str) -> bool:
    return content_type in {FORM_CONTENT, MULTIPART_CONTENT}


def merge_parameters(index: DocumentIndex, path_parameters: list[Any], operation_parameters: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    sources = list(path_parameters)
    if isinstance(operation_parameters, list):
        sources.extend(operation_parameters)
    for raw in sources:
        parameter = follow_ref(raw, index, index.path)
        if not isinstance(parameter, dict):
            continue
        key = (str(parameter.get("name") or ""), str(parameter.get("in") or ""))
        merged[key] = parameter
    return list(merged.values())


def schema_from_swagger_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for key in ("type", "format", "enum", "minimum", "maximum", "minLength", "maxLength", "pattern", "items", "default"):
        if key in parameter:
            schema[key] = parameter[key]
    if "type" not in schema:
        schema["type"] = "string"
    return schema


def value_for_parameter(
    parameter: dict[str, Any],
    index: DocumentIndex,
    rng: random.Random,
    required_only: bool,
    files: list[tuple[str, bytes]],
    name: str,
) -> Any:
    schema = parameter.get("schema")
    if not isinstance(schema, dict):
        schema = schema_from_swagger_parameter(parameter)
    return generate_value(
        schema,
        index,
        index.path,
        rng,
        allow_null=not bool(parameter.get("required")),
        required_only=required_only,
        files=files,
        file_prefix=name,
    )


def build_body(
    index: DocumentIndex,
    operation: dict[str, Any],
    method: str,
    swagger_body: Any,
    swagger_body_required: bool,
    form_fields: list[tuple[str, str]],
    rng: random.Random,
    required_only: bool,
    files: list[tuple[str, bytes]],
    raw_path: str,
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]:
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        request_body = follow_ref(request_body, index, index.path)
        if isinstance(request_body, dict):
            if required_only and request_body.get("required") is not True:
                return "", "", [], []
            content = request_body.get("content") if isinstance(request_body.get("content"), dict) else None
            if isinstance(content, dict) and content:
                media, media_object = choose_content(content)
                schema = {}
                if isinstance(media_object, dict) and isinstance(media_object.get("schema"), dict):
                    schema = media_object["schema"]
                return render_body(index, media, schema, rng, required_only, files, raw_path, form_fields)
    consumes = operation.get("consumes")
    if not isinstance(consumes, list):
        consumes = index.root.get("consumes") if isinstance(index.root.get("consumes"), list) else []
    if form_fields:
        bases = [media_base(str(candidate)) for candidate in consumes if isinstance(candidate, str)]
        media = MULTIPART_CONTENT if MULTIPART_CONTENT in bases else FORM_CONTENT
        return render_form(media, form_fields, [])
    if swagger_body is not None:
        if required_only and not swagger_body_required:
            return "", "", [], []
        media = JSON_CONTENT
        for candidate in consumes:
            if isinstance(candidate, str) and media_base(candidate) in CONTENT_PREFERENCE:
                media = media_base(candidate)
                break
        return render_body(index, media, swagger_body, rng, required_only, files, raw_path, [])
    return "", "", [], []


def media_base(media: str) -> str:
    return media.split(";", 1)[0].strip()


def choose_content(content: dict[str, Any]) -> tuple[str, Any]:
    normalized = [(media_base(str(media)), value) for media, value in content.items()]
    for preferred in CONTENT_PREFERENCE:
        for base, value in normalized:
            if base == preferred:
                return preferred, value
    for base, value in normalized:
        if base.endswith("+json"):
            return base, value
    return normalized[0]


def render_body(
    index: DocumentIndex,
    media: str,
    schema: dict[str, Any],
    rng: random.Random,
    required_only: bool,
    files: list[tuple[str, bytes]],
    raw_path: str,
    existing_fields: list[tuple[str, str]],
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]:
    if media == FORM_CONTENT or media == MULTIPART_CONTENT:
        value = generate_value(schema, index, index.path, rng, required_only=required_only, files=files, file_prefix=slug(raw_path))
        fields = list(existing_fields)
        file_args: list[tuple[str, str, str]] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if media == MULTIPART_CONTENT and isinstance(item, str) and looks_binary(schema, key, index):
                    relative = write_file(files, f"{slug(raw_path)}-{slug(key)}.bin", item.encode("utf-8"))
                    file_args.append((relative, key, "application/octet-stream"))
                else:
                    fields.append((key, stringify(item)))
        else:
            fields.append(("value", stringify(value)))
        return render_form(media, fields, file_args)
    value = generate_value(
        schema,
        index,
        index.path,
        rng,
        required_only=required_only,
        files=files,
        file_prefix=slug(raw_path),
    )
    if media.endswith("+json") or media == JSON_CONTENT:
        return media, json.dumps(value, ensure_ascii=False, indent=2), [], []
    if media == XML_CONTENT or media.endswith("+xml"):
        return media, dict_to_xml("request", value), [], []
    if media == BINARY_CONTENT:
        text = value if isinstance(value, str) else stringify(value)
        return media, text, [], []
    return media, stringify(value), [], []


def looks_binary(schema: dict[str, Any], key: str, index: DocumentIndex) -> bool:
    resolved = deref_schema(schema, index, index.path)
    properties = resolved.get("properties") if isinstance(resolved.get("properties"), dict) else {}
    property_schema = properties.get(key)
    if not isinstance(property_schema, dict):
        return False
    property_schema = deref_schema(property_schema, index, index.path)
    return property_schema.get("format") in {"binary", "byte"} or property_schema.get("type") == "file"


def render_form(
    media: str,
    fields: list[tuple[str, str]],
    file_args: list[tuple[str, str, str]],
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]:
    if media == MULTIPART_CONTENT:
        return media, "", fields, file_args
    return media, urllib.parse.urlencode(fields), [], []


def write_file(files: list[tuple[str, bytes]], name: str, payload: bytes) -> str:
    relative = "testdata/" + name
    files.append((relative, payload))
    return relative


def dict_to_xml(root: str, value: Any) -> str:
    return f"<{slug(root) or 'request'}>\n{xml_nodes(value, 1)}</{slug(root) or 'request'}>\n"


def xml_nodes(value: Any, indent: int) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        chunks = []
        for key, item in value.items():
            name = slug(str(key)) or "field"
            chunks.append(f"{pad}<{name}>\n{xml_nodes(item, indent + 1)}{pad}</{name}>\n")
        return "".join(chunks)
    if isinstance(value, list):
        return "".join(f"{pad}<item>\n{xml_nodes(item, indent + 1)}{pad}</item>\n" for item in value)
    return f"{pad}{xml_text(stringify(value))}\n"


def xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def accept_header(operation: dict[str, Any], index: DocumentIndex) -> str:
    responses = operation.get("responses") if isinstance(operation.get("responses"), dict) else {}
    ordered: list[str] = []
    for key in ("200", "201", "202", "203"):
        if key in responses:
            ordered.append(key)
    for key in responses:
        text = str(key)
        if text.startswith("2") and text not in ordered:
            ordered.append(text)
    if "default" in responses and "default" not in ordered:
        ordered.append("default")
    for key in ordered:
        response = responses.get(key)
        if isinstance(response, dict):
            response = follow_ref(response, index, index.path)
        if isinstance(response, dict) and isinstance(response.get("content"), dict) and response["content"]:
            return media_base(str(next(iter(response["content"].keys()))))
    for source in (operation.get("produces"), index.root.get("produces") if isinstance(index.root, dict) else None):
        if isinstance(source, list) and source:
            return media_base(str(source[0]))
    return JSON_CONTENT


def join_paths(base: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if not base:
        return path
    return base.rstrip("/") + path


def append_query(path: str, query: list[tuple[str, str]]) -> str:
    if not query:
        return path
    encoded = urllib.parse.urlencode(query, doseq=True)
    return path + ("&" if "?" in path else "?") + encoded


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:48] or "field"


def build_jmx(
    index: DocumentIndex,
    operations: list[Operation],
    options: argparse.Namespace,
    seed: int,
) -> str:
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = "OpenAPI API test"
    if isinstance(index.root, dict) and isinstance(index.root.get("info"), dict):
        title = str(index.root["info"].get("title") or title)
    comment = "\n".join(
        [
            f"{title} を1回ずつ呼ぶ確認用シナリオです。",
            "最初に setUp スレッドが Cognito からアクセストークンを1回受け取り、JMeterプロパティ ACCESS_TOKEN に保存します。",
            "後続のAPIは Authorization: Bearer とそのトークン、および X-API-KEY を付けて http://localhost:8080 へ送ります。",
            "トークンの期限が来る少し前だけ、同じ取得処理をやり直します。",
            f"生成時刻: {created}",
            f"乱数シード: {seed}",
            f"トークン方式の初期値: {options.token_mode}",
            "実行前に COGNITO_CLIENT_ID と COGNITO_CLIENT_SECRET を性能試験専用アプリクライアントの値へ変えてください。",
            "callbacks と webhooks はシナリオに含めていません。",
            "結果の jtl にはトークン応答が含まれるため、結果ファイルは秘密情報として扱ってください。jmeter.log にはトークン本文を出しません。",
            "GUIで結果を見るときは、View Results Tree のファイル名 results/jmeter-results.jtl を開き直してください。",
        ]
    )
    xml = _Xml()
    xml.line(0, '<?xml version="1.0" encoding="UTF-8"?>')
    xml.line(0, '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">')
    xml.line(1, "<hashTree>")
    test_plan_open = (
        '<TestPlan guiclass="TestPlanGui" testclass="TestPlan" '
        f'testname="{esc(title + " smoke")}" enabled="true">'
    )
    xml.block(
        2,
        test_plan_open,
        [
            prop("TestPlan.comments", comment),
            bool_prop("TestPlan.functional_mode", False),
            bool_prop("TestPlan.tearDown_on_shutdown", True),
            bool_prop("TestPlan.serialize_threadgroups", False),
            arguments_element("TestPlan.user_defined_variables", variable_definitions(options), panel=True),
            prop("TestPlan.user_define_classpath", ""),
        ],
        lambda: render_plan_children(xml, operations, options),
    )
    xml.line(1, "</hashTree>")
    xml.line(0, "</jmeterTestPlan>")
    return "\n".join(xml.parts) + "\n"


def variable_definitions(options: argparse.Namespace) -> list[tuple[str, str]]:
    values = {
        "API_PROTOCOL": options.protocol,
        "API_HOST": options.host,
        "API_PORT": str(options.port),
        "X_API_KEY": options.api_key,
        "CONNECT_TIMEOUT_MS": "10000",
        "RESPONSE_TIMEOUT_MS": "30000",
        "COGNITO_TOKEN_MODE": options.token_mode,
        "COGNITO_REGION": "ap-northeast-1",
        "COGNITO_CLIENT_ID": "your-perf-client-id",
        "COGNITO_CLIENT_SECRET": "your-perf-client-secret",
        "COGNITO_SCOPES": "",
        "COGNITO_DOMAIN": "your-domain.auth.ap-northeast-1.amazoncognito.com",
        "COGNITO_GET_CLIENT_TOKEN_PROTOCOL": "https",
        "COGNITO_GET_CLIENT_TOKEN_HOST": "",
        "COGNITO_GET_CLIENT_TOKEN_PORT": "443",
        "COGNITO_OAUTH_PROTOCOL": "https",
        "COGNITO_OAUTH_PORT": "443",
        "TOKEN_REFRESH_SKEW_SECONDS": "60",
    }
    return [(name, f"${{__P({name},{value})}}") for name, value in values.items()]


def render_plan_children(xml: "_Xml", operations: list[Operation], options: argparse.Namespace) -> None:
    render_config_elements(xml)
    render_setup(xml)
    render_main_group(xml, operations)
    render_listener(xml)


def render_config_elements(xml: "_Xml") -> None:
    xml.block(
        3,
        '<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">',
        [
            prop("TestPlan.comments", "タイムアウトを必ず持たせ、応答がない相手を待ち続けない。ホストは各リクエストで明示する。"),
            empty_arguments(),
            prop("HTTPSampler.implementation", "HttpClient4"),
            prop("HTTPSampler.connect_timeout", "${CONNECT_TIMEOUT_MS}"),
            prop("HTTPSampler.response_timeout", "${RESPONSE_TIMEOUT_MS}"),
            prop("HTTPSampler.protocol", ""),
            prop("HTTPSampler.domain", ""),
            prop("HTTPSampler.port", ""),
            prop("HTTPSampler.contentEncoding", "UTF-8"),
            prop("HTTPSampler.path", ""),
            prop("HTTPSampler.concurrentPool", "6"),
        ],
        lambda: None,
    )
    xml.block(
        3,
        '<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="HTTP Cookie Manager" enabled="true">',
        [
            '<collectionProp name="CookieManager.cookies"/>',
            bool_prop("CookieManager.clearEachIteration", False),
            bool_prop("CookieManager.controlledByThreadGroup", False),
            prop("CookieManager.policy", "standard"),
            prop("CookieManager.implementation", "org.apache.jmeter.protocol.http.control.HC4CookieHandler"),
        ],
        lambda: None,
    )
    xml.block(
        3,
        '<CacheManager guiclass="CacheManagerGui" testclass="CacheManager" testname="HTTP Cache Manager" enabled="false">',
        [
            prop("TestPlan.comments", "API確認ではキャッシュが実リクエストを隠すため無効。ブラウザ模擬のときだけ有効にする。"),
            bool_prop("clearEachIteration", True),
            bool_prop("useExpires", False),
        ],
        lambda: None,
    )
    xml.block(
        3,
        '<DNSCacheManager guiclass="DNSCachePanel" testclass="DNSCacheManager" testname="DNS Cache Manager" enabled="true">',
        [
            '<collectionProp name="DNSCacheManager.servers"/>',
            bool_prop("DNSCacheManager.clearEachIteration", False),
            bool_prop("DNSCacheManager.isCustomResolver", False),
        ],
        lambda: None,
    )


def render_setup(xml: "_Xml") -> None:
    xml.block(
        3,
        '<SetupThreadGroup guiclass="SetupThreadGroupGui" testclass="SetupThreadGroup" testname="setUp - Cognito access token" enabled="true">',
        thread_group_props("本スレッドの前に1回だけ動き、アクセストークンをプロパティへ保存する。"),
        lambda: render_token_flow(xml, include_validation=True),
    )


def render_main_group(xml: "_Xml", operations: list[Operation]) -> None:
    xml.block(
        3,
        '<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="API calls - 1 thread 1 iteration" enabled="true">',
        thread_group_props("定義済みAPIを1スレッド、1反復で順番に1回ずつ呼ぶ。"),
        lambda: render_api_flow(xml, operations),
    )


def thread_group_props(comment: str) -> list[str]:
    return [
        prop("TestPlan.comments", comment),
        prop("ThreadGroup.on_sample_error", "continue"),
        (
            '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" '
            'guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">'
        ),
        bool_prop("LoopController.continue_forever", False),
        prop("LoopController.loops", "1"),
        "</elementProp>",
        prop("ThreadGroup.num_threads", "1"),
        prop("ThreadGroup.ramp_time", "1"),
        bool_prop("ThreadGroup.scheduler", False),
        prop("ThreadGroup.duration", ""),
        prop("ThreadGroup.delay", ""),
        bool_prop("ThreadGroup.same_user_on_next_iteration", True),
    ]


def render_token_flow(xml: "_Xml", include_validation: bool) -> None:
    if include_validation:
        jsr223_sampler(xml, 4, "Validate Cognito settings", VALIDATE_SCRIPT)
    if_mode(xml, 4, "get_client_token", "GetClientToken when selected", lambda: token_request(xml, 5, "get_client_token"))
    if_mode(xml, 4, "oauth2_token", "OAuth token endpoint when selected", lambda: token_request(xml, 5, "oauth2_token"))


def render_api_flow(xml: "_Xml", operations: list[Operation]) -> None:
    jsr223_sampler(xml, 4, "Decide whether the access token needs refreshing", REFRESH_CHECK_SCRIPT)
    xml.block(
        4,
        '<IfController guiclass="IfControllerPanel" testclass="IfController" testname="Refresh the access token when it is missing or near expiry" enabled="true">',
        [
            prop("IfController.condition", "${TOKEN_NEEDS_REFRESH}"),
            bool_prop("IfController.evaluateAll", False),
            bool_prop("IfController.useExpression", True),
        ],
        lambda: render_locked_refresh(xml),
    )
    grouped: dict[str, list[Operation]] = {}
    for operation in operations:
        grouped.setdefault(operation.tag, []).append(operation)
    for tag in sorted(grouped):
        xml.block(
            4,
            f'<TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" testname="{esc(tag)}" enabled="true">',
            [
                bool_prop("TransactionController.includeTimers", False),
                bool_prop("TransactionController.parent", True),
            ],
            lambda tag=tag: render_tag(xml, grouped[tag]),
        )


def render_locked_refresh(xml: "_Xml") -> None:
    xml.block(
        5,
        '<CriticalSectionController guiclass="CriticalSectionControllerGui" testclass="CriticalSectionController" testname="Cognito token refresh lock" enabled="true">',
        [prop("CriticalSectionController.lockName", "cognito-access-token")],
        lambda: render_double_checked_refresh(xml),
    )


def render_double_checked_refresh(xml: "_Xml") -> None:
    jsr223_sampler(xml, 6, "Recheck token expiry inside the lock", REFRESH_CHECK_SCRIPT)
    xml.block(
        6,
        '<IfController guiclass="IfControllerPanel" testclass="IfController" testname="Only one thread requests a new token" enabled="true">',
        [
            prop("IfController.condition", "${TOKEN_NEEDS_REFRESH}"),
            bool_prop("IfController.evaluateAll", False),
            bool_prop("IfController.useExpression", True),
        ],
        lambda: render_token_flow(xml, include_validation=False),
    )


def render_tag(xml: "_Xml", operations: list[Operation]) -> None:
    for operation in operations:
        api_sampler(xml, 5, operation)


def if_mode(xml: "_Xml", indent: int, mode: str, title: str, child: Callable[[], None]) -> None:
    # useExpression compares the substituted text with "true". __groovy returns that word
    # and avoids nested quotes inside __jexl3. The mode token contains no comma.
    condition = '${__groovy("' + mode + '".equals(vars.get("COGNITO_TOKEN_MODE")))}'
    xml.block(
        indent,
        f'<IfController guiclass="IfControllerPanel" testclass="IfController" testname="{esc(title)}" enabled="true">',
        [
            prop("IfController.condition", condition),
            bool_prop("IfController.evaluateAll", False),
            bool_prop("IfController.useExpression", True),
        ],
        child,
    )


def token_request(xml: "_Xml", indent: int, mode: str) -> None:
    if mode == "get_client_token":
        name = "POST GetClientToken"
        protocol = "${COGNITO_GET_CLIENT_TOKEN_PROTOCOL}"
        domain = "${COGNITO_RESOLVED_HOST}"
        port = "${COGNITO_GET_CLIENT_TOKEN_PORT}"
        path = "/"
        body = "${COGNITO_TOKEN_BODY}"
        prepare = PREPARE_GET_CLIENT_TOKEN_SCRIPT
        headers = [
            ("Content-Type", "application/x-amz-json-1.1"),
            ("X-Amz-Target", "AWSCognitoIdentityProviderService.GetClientToken"),
        ]
        token_path = "$.ClientAuthenticationResult.AccessToken"
        expires_path = "$.ClientAuthenticationResult.ExpiresIn"
        comment = "ドメイン不要の GetClientToken。専用クライアントの ALLOW_CLIENT_TOKEN_AUTH だけで使う。"
    else:
        name = "POST /oauth2/token"
        protocol = "${COGNITO_OAUTH_PROTOCOL}"
        domain = "${COGNITO_DOMAIN}"
        port = "${COGNITO_OAUTH_PORT}"
        path = "/oauth2/token"
        body = "${COGNITO_OAUTH_BODY}"
        prepare = PREPARE_OAUTH_SCRIPT
        headers = [("Content-Type", FORM_CONTENT)]
        token_path = "$.access_token"
        expires_path = "$.expires_in"
        comment = "ユーザープールドメインの OAuth2 client_credentials。client_secret_post で送る。"
    xml.block(
        indent,
        f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{esc(name)}" enabled="true">',
        http_sampler_lines(
            comment=comment,
            protocol=protocol,
            domain=domain,
            port=port,
            path=path,
            method="POST",
            raw_body=body,
            arguments=[],
            files=[],
        ),
        lambda: token_children(xml, indent + 1, prepare, headers, token_path, expires_path),
    )


def token_children(
    xml: "_Xml",
    indent: int,
    prepare_script: str,
    headers: list[tuple[str, str]],
    token_path: str,
    expires_path: str,
) -> None:
    header_manager(xml, indent, "Cognito request headers", headers)
    jsr223(xml, indent, "JSR223PreProcessor", "Prepare Cognito token request", prepare_script)
    json_extractor(xml, indent, "Extract access token", "ACCESS_TOKEN", token_path, "TOKEN_NOT_FOUND")
    json_extractor(xml, indent, "Extract expires_in", "EXPIRES_IN", expires_path, "3600")
    jsr223(xml, indent, "JSR223PostProcessor", "Store access token for reuse", STORE_TOKEN_SCRIPT)
    response_assertion(xml, indent, "Cognito token endpoint returned HTTP 200", "200")


def api_sampler(xml: "_Xml", indent: int, operation: Operation) -> None:
    headers = [
        ("Authorization", "Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}"),
        ("X-API-KEY", "${X_API_KEY}"),
        ("Accept", operation_accept(operation)),
    ]
    if operation.content_type and operation.content_type != MULTIPART_CONTENT:
        headers.append(("Content-Type", operation.content_type))
    for name, value in operation.headers:
        if name.lower() not in {item[0].lower() for item in headers}:
            headers.append((name, value))
    if operation.cookie_header:
        headers.append(("Cookie", operation.cookie_header))
    raw_body = operation.body if operation.content_type and operation.content_type != MULTIPART_CONTENT else None
    arguments = operation.query
    if operation.content_type == MULTIPART_CONTENT:
        arguments = operation.multipart_fields
        raw_body = None
    path = operation.path
    if operation.content_type == MULTIPART_CONTENT and operation.query:
        path = append_query(path, operation.query)
        arguments = operation.multipart_fields
    xml.block(
        indent,
        f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{esc(operation.sampler_name)}" enabled="true">',
        http_sampler_lines(
            comment=operation.summary,
            protocol="${API_PROTOCOL}",
            domain="${API_HOST}",
            port="${API_PORT}",
            path=path,
            method=operation.method,
            raw_body=raw_body,
            arguments=arguments if raw_body is None else [],
            files=operation.files,
            multipart=operation.content_type == MULTIPART_CONTENT,
        ),
        lambda: api_children(xml, indent + 1, headers),
    )


def operation_accept(operation: Operation) -> str:
    return operation.accept or JSON_CONTENT


def api_children(xml: "_Xml", indent: int, headers: list[tuple[str, str]]) -> None:
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, value in headers:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((name, value))
    header_manager(xml, indent, "API Gateway headers", cleaned)
    jsr223(xml, indent, "JSR223Assertion", "Access token is present", TOKEN_ASSERTION_SCRIPT)


def http_sampler_lines(
    comment: str,
    protocol: str,
    domain: str,
    port: str,
    path: str,
    method: str,
    raw_body: str | None,
    arguments: list[tuple[str, str]],
    files: list[tuple[str, str, str]],
    multipart: bool = False,
) -> list[str]:
    lines = [prop("TestPlan.comments", comment)]
    if raw_body is not None:
        lines.append(bool_prop("HTTPSampler.postBodyRaw", True))
        lines.append(raw_arguments(raw_body))
    else:
        lines.append(bool_prop("HTTPSampler.postBodyRaw", False))
        lines.append(named_arguments(arguments))
    if files:
        lines.append(file_arguments(files))
    lines.extend(
        [
            prop("HTTPSampler.domain", domain),
            prop("HTTPSampler.port", port),
            prop("HTTPSampler.protocol", protocol),
            prop("HTTPSampler.contentEncoding", "UTF-8"),
            prop("HTTPSampler.path", path),
            prop("HTTPSampler.method", method),
            bool_prop("HTTPSampler.follow_redirects", True),
            bool_prop("HTTPSampler.auto_redirects", False),
            bool_prop("HTTPSampler.use_keepalive", True),
            bool_prop("HTTPSampler.DO_MULTIPART_POST", multipart or bool(files)),
            bool_prop("HTTPSampler.BROWSER_COMPATIBLE_MULTIPART", False),
            bool_prop("HTTPSampler.image_parser", False),
            bool_prop("HTTPSampler.concurrentDwn", False),
            prop("HTTPSampler.concurrentPool", "6"),
            bool_prop("HTTPSampler.md5", False),
            bool_prop("HTTPSampler.monitor", False),
            prop("HTTPSampler.embedded_url_re", ""),
            prop("HTTPSampler.connect_timeout", "${CONNECT_TIMEOUT_MS}"),
            prop("HTTPSampler.response_timeout", "${RESPONSE_TIMEOUT_MS}"),
            prop("HTTPSampler.implementation", "HttpClient4"),
        ]
    )
    return lines


def raw_arguments(body: str) -> str:
    return (
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments">'
        '<collectionProp name="Arguments.arguments">'
        '<elementProp name="" elementType="HTTPArgument">'
        + bool_prop("HTTPArgument.always_encode", False)
        + prop("Argument.value", body)
        + prop("Argument.metadata", "=")
        + "</elementProp></collectionProp></elementProp>"
    )


def named_arguments(arguments: list[tuple[str, str]]) -> str:
    chunks = [
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" '
        'guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">',
        '<collectionProp name="Arguments.arguments">',
    ]
    for name, value in arguments:
        chunks.append(f'<elementProp name="{esc(name)}" elementType="HTTPArgument">')
        chunks.append(bool_prop("HTTPArgument.always_encode", True))
        chunks.append(prop("Argument.value", value))
        chunks.append(prop("Argument.metadata", "="))
        chunks.append(bool_prop("HTTPArgument.use_equals", True))
        chunks.append(prop("Argument.name", name))
        chunks.append("</elementProp>")
    chunks.append("</collectionProp></elementProp>")
    return "".join(chunks)


def file_arguments(files: list[tuple[str, str, str]]) -> str:
    chunks = [
        '<elementProp name="HTTPsampler.Files" elementType="HTTPFileArgs">',
        '<collectionProp name="HTTPFileArgs.files">',
    ]
    for path, field, mime in files:
        chunks.append(f'<elementProp name="{esc(path)}" elementType="HTTPFileArg">')
        chunks.append(prop("File.path", path))
        chunks.append(prop("File.paramname", field))
        chunks.append(prop("File.mimetype", mime))
        chunks.append("</elementProp>")
    chunks.append("</collectionProp></elementProp>")
    return "".join(chunks)


def header_manager(xml: "_Xml", indent: int, title: str, headers: list[tuple[str, str]]) -> None:
    chunks = ['<collectionProp name="HeaderManager.headers">']
    for name, value in headers:
        chunks.append(f'<elementProp name="{esc(name)}" elementType="Header">')
        chunks.append(prop("Header.name", name))
        chunks.append(prop("Header.value", value))
        chunks.append("</elementProp>")
    chunks.append("</collectionProp>")
    xml.block(
        indent,
        f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="{esc(title)}" enabled="true">',
        chunks,
        lambda: None,
    )


def json_extractor(xml: "_Xml", indent: int, title: str, name: str, path: str, default: str) -> None:
    xml.block(
        indent,
        f'<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="{esc(title)}" enabled="true">',
        [
            prop("JSONPostProcessor.referenceNames", name),
            prop("JSONPostProcessor.jsonPathExprs", path),
            prop("JSONPostProcessor.match_numbers", "1"),
            prop("JSONPostProcessor.defaultValues", default),
        ],
        lambda: None,
    )


def response_assertion(xml: "_Xml", indent: int, title: str, expected: str) -> None:
    xml.block(
        indent,
        f'<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="{esc(title)}" enabled="true">',
        [
            '<collectionProp name="Asserion.test_strings">',
            prop("0", expected),
            "</collectionProp>",
            prop("Assertion.custom_message", title),
            prop("Assertion.test_field", "Assertion.response_code"),
            bool_prop("Assertion.assume_success", False),
            '<intProp name="Assertion.test_type">8</intProp>',
        ],
        lambda: None,
    )


def jsr223(xml: "_Xml", indent: int, testclass: str, title: str, script: str) -> None:
    xml.block(
        indent,
        f'<{testclass} guiclass="TestBeanGUI" testclass="{testclass}" testname="{esc(title)}" enabled="true">',
        jsr223_props(script),
        lambda: None,
    )


def jsr223_sampler(xml: "_Xml", indent: int, title: str, script: str) -> None:
    jsr223(xml, indent, "JSR223Sampler", title, script)


def jsr223_props(script: str) -> list[str]:
    return [
        prop("scriptLanguage", "groovy"),
        prop("parameters", ""),
        prop("filename", ""),
        prop("cacheKey", "true"),
        f"<stringProp name=\"script\">{cdata(script)}</stringProp>",
    ]


def render_listener(xml: "_Xml") -> None:
    xml.block(
        3,
        '<ResultCollector guiclass="ViewResultsFullVisualizer" testclass="ResultCollector" testname="View Results Tree" enabled="true">',
        [
            prop(
                "TestPlan.comments",
                "XML形式のjtlなので、GUIのView Results Treeでリクエストとレスポンスを開ける。大規模な負荷試験の前にはこのリスナーを無効にする。",
            ),
            bool_prop("ResultCollector.error_logging", False),
            save_config(),
            prop("filename", "results/jmeter-results.jtl"),
        ],
        lambda: None,
    )


def save_config() -> str:
    fields = {
        "time": True,
        "latency": True,
        "timestamp": True,
        "success": True,
        "label": True,
        "code": True,
        "message": True,
        "threadName": True,
        "dataType": True,
        "encoding": True,
        "assertions": True,
        "subresults": True,
        "responseData": True,
        "samplerData": True,
        "xml": True,
        "fieldNames": True,
        "responseHeaders": True,
        "requestHeaders": True,
        "responseDataOnError": False,
        "saveAssertionResultsFailureMessage": True,
        "bytes": True,
        "sentBytes": True,
        "url": True,
        "threadCounts": True,
        "idleTime": True,
        "connectTime": True,
        "sampleCount": True,
        "hostname": True,
        "fileName": False,
    }
    lines = ['<objProp>', "<name>saveConfig</name>", '<value class="SampleSaveConfiguration">']
    for name, value in fields.items():
        lines.append(f"<{name}>{str(value).lower()}</{name}>")
    lines.append("<assertionsResultsToSave>2</assertionsResultsToSave>")
    lines.append("</value></objProp>")
    return "".join(lines)


def arguments_element(name: str, variables: list[tuple[str, str]], panel: bool) -> str:
    gui = ' guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true"' if panel else ""
    chunks = [f'<elementProp name="{name}" elementType="Arguments"{gui}>', '<collectionProp name="Arguments.arguments">']
    for variable, value in variables:
        chunks.append(f'<elementProp name="{esc(variable)}" elementType="Argument">')
        chunks.append(prop("Argument.name", variable))
        chunks.append(prop("Argument.value", value))
        chunks.append(prop("Argument.metadata", "="))
        chunks.append("</elementProp>")
    chunks.append("</collectionProp></elementProp>")
    return "".join(chunks)


def empty_arguments() -> str:
    return (
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" '
        'guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
        '<collectionProp name="Arguments.arguments"/></elementProp>'
    )


class _Xml:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def line(self, indent: int, text: str) -> None:
        self.parts.append(f"{'  ' * indent}{text}")

    def block(self, indent: int, open_tag: str, inner: list[str], children: Callable[[], None]) -> None:
        self.line(indent, open_tag)
        for entry in inner:
            for line in entry.splitlines():
                self.line(indent + 1, line)
        tag_name = open_tag.split()[0][1:]
        self.line(indent, f"</{tag_name}>")
        self.line(indent, "<hashTree>")
        children()
        self.line(indent, "</hashTree>")


def prop(name: str, value: Any) -> str:
    return f'<stringProp name="{esc(name)}">{esc(value)}</stringProp>'


def bool_prop(name: str, value: bool) -> str:
    return f'<boolProp name="{esc(name)}">{str(value).lower()}</boolProp>'


def esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def cdata(script: str) -> str:
    return "<![CDATA[" + script.replace("]]>", "]]]]><![CDATA[>") + "]]>"


VALIDATE_SCRIPT = r"""
def mode = vars.get("COGNITO_TOKEN_MODE")
if (mode == null) {
    mode = ""
}
def errors = new ArrayList()
if (!"get_client_token".equals(mode) && !"oauth2_token".equals(mode)) {
    errors.add("COGNITO_TOKEN_MODE は get_client_token または oauth2_token にしてください。")
}
def clientId = vars.get("COGNITO_CLIENT_ID")
def secret = vars.get("COGNITO_CLIENT_SECRET")
if (clientId == null || clientId.trim().isEmpty() || clientId.startsWith("your-perf-")) {
    errors.add("COGNITO_CLIENT_ID に、性能試験専用アプリクライアントのIDを設定してください。")
}
if (secret == null || secret.trim().isEmpty() || secret.startsWith("your-perf-")) {
    errors.add("COGNITO_CLIENT_SECRET に、性能試験専用アプリクライアントのシークレットを設定してください。")
}
if ("oauth2_token".equals(mode)) {
    def domain = vars.get("COGNITO_DOMAIN")
    if (domain == null || domain.trim().isEmpty() || domain.startsWith("your-domain")) {
        errors.add("COGNITO_DOMAIN に、ユーザープールのドメインホストだけを設定してください。https:// は不要です。")
    }
}
if ("get_client_token".equals(mode)) {
    def region = vars.get("COGNITO_REGION")
    if (region == null || region.trim().isEmpty()) {
        errors.add("COGNITO_REGION を設定してください。例: ap-northeast-1")
    }
}
if (errors.isEmpty()) {
    SampleResult.setSuccessful(true)
    SampleResult.setResponseCodeOK()
    SampleResult.setResponseMessage("Cognito settings are present")
    SampleResult.setIgnore()
} else {
    def message = errors.join(" ")
    SampleResult.setSuccessful(false)
    SampleResult.setResponseCode("400")
    SampleResult.setResponseMessage(message)
    SampleResult.setResponseData(message, "UTF-8")
    SampleResult.setStopTest(true)
    log.error(message)
}
""".strip() + "\n"


PREPARE_GET_CLIENT_TOKEN_SCRIPT = r"""
import groovy.json.JsonOutput

def overrideHost = vars.get("COGNITO_GET_CLIENT_TOKEN_HOST")
if (overrideHost != null && overrideHost.trim().length() > 0) {
    vars.put("COGNITO_RESOLVED_HOST", overrideHost.trim())
} else {
    def region = vars.get("COGNITO_REGION")
    if (region == null || region.trim().isEmpty()) {
        region = "ap-northeast-1"
    }
    vars.put("COGNITO_RESOLVED_HOST", "cognito-idp." + region.trim() + ".amazonaws.com")
}
def body = new LinkedHashMap()
body.put("ClientId", vars.get("COGNITO_CLIENT_ID") == null ? "" : vars.get("COGNITO_CLIENT_ID"))
body.put("Secret", vars.get("COGNITO_CLIENT_SECRET") == null ? "" : vars.get("COGNITO_CLIENT_SECRET"))
def scopesRaw = vars.get("COGNITO_SCOPES")
if (scopesRaw != null && scopesRaw.trim().length() > 0) {
    def scopes = new ArrayList()
    for (String part : scopesRaw.split("[\\s,]+")) {
        if (part != null && part.trim().length() > 0) {
            scopes.add(part.trim())
        }
    }
    if (!scopes.isEmpty()) {
        body.put("Scopes", scopes)
    }
}
vars.put("COGNITO_TOKEN_BODY", JsonOutput.toJson(body))
log.info("Prepared GetClientToken request. host=" + vars.get("COGNITO_RESOLVED_HOST"))
""".strip() + "\n"


PREPARE_OAUTH_SCRIPT = r"""
def encode = { String value ->
    if (value == null) {
        return ""
    }
    return java.net.URLEncoder.encode(value, "UTF-8")
}
def parts = new ArrayList()
parts.add("grant_type=client_credentials")
parts.add("client_id=" + encode(vars.get("COGNITO_CLIENT_ID")))
parts.add("client_secret=" + encode(vars.get("COGNITO_CLIENT_SECRET")))
def scopes = vars.get("COGNITO_SCOPES")
if (scopes != null && scopes.trim().length() > 0) {
    parts.add("scope=" + encode(scopes.trim()))
}
vars.put("COGNITO_OAUTH_BODY", parts.join("&"))
log.info("Prepared OAuth token request. host=" + vars.get("COGNITO_DOMAIN"))
""".strip() + "\n"


STORE_TOKEN_SCRIPT = r"""
def token = vars.get("ACCESS_TOKEN")
def expiresRaw = vars.get("EXPIRES_IN")
def secret = vars.get("COGNITO_CLIENT_SECRET")
def recorded = null
try {
    recorded = prev.getQueryString()
} catch (MissingMethodException ignored) {
    recorded = prev.getSamplerData()
}
if (recorded != null && secret != null && secret.length() > 0) {
    def scrubbed = recorded.replace(secret, "***")
    def encoded = java.net.URLEncoder.encode(secret, "UTF-8")
    if (!encoded.equals(secret)) {
        scrubbed = scrubbed.replace(encoded, "***")
    }
    try {
        prev.setQueryString(scrubbed)
    } catch (MissingMethodException ignored) {
        prev.setSamplerData(scrubbed)
    }
}
if (token == null || token.trim().isEmpty() || "TOKEN_NOT_FOUND".equals(token)) {
    props.put("ACCESS_TOKEN", "TOKEN_NOT_FOUND")
    props.put("TOKEN_EXPIRES_AT_MS", "0")
    prev.setSuccessful(false)
    prev.setResponseMessage("Cognitoのアクセストークンを応答から取り出せませんでした。クライアントID、シークレット、スコープ、トークン方式を確認してください。")
    prev.setStopTest(true)
    log.error("Cognito access token was not extracted. mode=" + vars.get("COGNITO_TOKEN_MODE"))
    return
}
long ttl = 3600L
try {
    if (expiresRaw != null && expiresRaw.trim().length() > 0) {
        ttl = Long.parseLong(expiresRaw.trim())
    }
} catch (NumberFormatException ignored) {
    ttl = 3600L
}
long skew = 60L
try {
    def skewRaw = vars.get("TOKEN_REFRESH_SKEW_SECONDS")
    if (skewRaw != null && skewRaw.trim().length() > 0) {
        skew = Long.parseLong(skewRaw.trim())
    }
} catch (NumberFormatException ignored) {
    skew = 60L
}
if (skew < 0L) {
    skew = 0L
}
if (ttl > 1L && skew >= ttl) {
    skew = ttl - 1L
}
long expiresAt = System.currentTimeMillis() + Math.max(1L, ttl - skew) * 1000L
synchronized (props) {
    props.put("ACCESS_TOKEN", token)
    props.put("TOKEN_EXPIRES_AT_MS", Long.toString(expiresAt))
}
def clientId = vars.get("COGNITO_CLIENT_ID")
def suffix = ""
if (clientId != null && clientId.length() > 0) {
    suffix = clientId.length() <= 4 ? clientId : clientId.substring(clientId.length() - 4)
}
log.info("Stored Cognito access token for reuse. mode=" + vars.get("COGNITO_TOKEN_MODE") + " expires_in_seconds=" + ttl + " refresh_skew_seconds=" + skew + " token_length=" + token.length() + " client_id_suffix=" + suffix)
""".strip() + "\n"


REFRESH_CHECK_SCRIPT = r"""
def token = props.get("ACCESS_TOKEN")
def expiresRaw = props.get("TOKEN_EXPIRES_AT_MS")
boolean needs = true
if (token != null) {
    def tokenText = token.toString()
    if (tokenText.trim().length() > 0 && !"TOKEN_NOT_FOUND".equals(tokenText) && expiresRaw != null) {
        try {
            needs = System.currentTimeMillis() >= Long.parseLong(expiresRaw.toString())
        } catch (NumberFormatException ignored) {
            needs = true
        }
    }
}
vars.put("TOKEN_NEEDS_REFRESH", needs ? "true" : "false")
SampleResult.setIgnore()
""".strip() + "\n"


TOKEN_ASSERTION_SCRIPT = r"""
def token = props.get("ACCESS_TOKEN")
if (token == null || token.toString().trim().isEmpty() || "TOKEN_NOT_FOUND".equals(token.toString())) {
    AssertionResult.setFailure(true)
    AssertionResult.setFailureMessage("Cognitoのアクセストークンが無いため、API Gatewayへ認可情報を渡せません。")
}
""".strip() + "\n"


def write_plan(output: Path, jmx: str, files: list[tuple[str, bytes]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(jmx)
    for relative, payload in files:
        destination = output.parent / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenAPI定義から、Cognitoアクセストークン付きの JMeter 5.6.3 シナリオを生成します。"
    )
    parser.add_argument("-i", "--openapi", required=True, help="OpenAPI 3 または Swagger 2 のYAML/JSON")
    parser.add_argument("-o", "--output", required=True, help="出力する .jmx ファイル")
    parser.add_argument("--host", default="localhost", help="APIの初期ホスト。既定は localhost")
    parser.add_argument("--port", default="8080", help="APIの初期ポート。既定は 8080")
    parser.add_argument("--protocol", default="http", choices=["http", "https"], help="APIの初期プロトコル")
    parser.add_argument("--api-key", default="XXXXXXXXXXXX", help="X-API-KEY の初期値")
    parser.add_argument("--base-path", default="auto", help="auto、none、または /v1 のようなパス")
    parser.add_argument("--server-index", type=int, default=0, help="base path に使う servers の番号")
    parser.add_argument("--seed", type=int, default=None, help="テストデータを再現する乱数シード")
    parser.add_argument("--required-only", action="store_true", help="必須の項目だけを送る")
    parser.add_argument("--skip-deprecated", action="store_true", help="deprecated な operation を含めない")
    parser.add_argument("--methods", default="", help="含めるメソッド。例: get,post")
    parser.add_argument(
        "--token-mode",
        default="get_client_token",
        choices=["get_client_token", "oauth2_token"],
        help="初期のトークン取得方式。既定はドメイン不要の GetClientToken",
    )
    return parser.parse_args(argv)


def emit(message: str, stream: Any = None) -> None:
    target = sys.stdout if stream is None else stream
    encoding = getattr(target, "encoding", None) or "utf-8"
    payload = (message + "\n").encode(encoding, errors="replace")
    buffer = getattr(target, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
        return
    target.write(message + "\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    options = parse_args(sys.argv[1:] if argv is None else argv)
    source = Path(options.openapi)
    output = Path(options.output)
    if not source.is_file():
        emit(f"OpenAPIファイルがありません: {source}", sys.stderr)
        return 3
    seed = options.seed if options.seed is not None else random.SystemRandom().randint(1, 2_147_483_646)
    rng = random.Random(seed)
    try:
        document = load_document(source)
        index = DocumentIndex(source, document)
        operations, files = build_operations(index, options, rng)
        jmx = build_jmx(index, operations, options, seed)
        write_plan(output, jmx, files)
    except NoOperationsError as exc:
        emit(str(exc), sys.stderr)
        return 4
    except (OSError, SpecError, YamlError) as exc:
        emit(str(exc), sys.stderr)
        return 3
    emit(f"JMeterシナリオを書きました: {output}")
    emit(f"API数: {len(operations)}")
    emit(f"呼び出し先の初期値: {options.protocol}://{options.host}:{options.port}")
    emit(f"X-API-KEYの初期値: {options.api_key}")
    emit(f"トークン方式の初期値: {options.token_mode}")
    emit(f"テストデータのシード: {seed}")
    emit("結果ファイル: results/jmeter-results.jtl （テスト計画のあるディレクトリ基準）")
    emit("ログファイル: 実行スクリプトが results/jmeter.log を -j で指定します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
