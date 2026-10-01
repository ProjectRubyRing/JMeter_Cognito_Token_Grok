"""A YAML 1.2 subset parser for OpenAPI documents.

The subset covers the constructs that show up in real OpenAPI files:
block mappings and sequences, nested flow collections, quoted scalars,
literal and folded block scalars, comments, anchors, aliases, and merge keys.
It intentionally follows the YAML 1.2 JSON schema for booleans, so the
unquoted word ``yes`` stays a string. That avoids the classic YAML 1.1
surprise where the country code NO becomes false.
"""

from __future__ import annotations

import copy
import re
from typing import Any


class YamlError(Exception):
    """Raised when an OpenAPI YAML document uses syntax this parser cannot read."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        if line is None:
            super().__init__(message)
        else:
            super().__init__(f"YAML line {line}: {message}")


_KEY_VALUE = re.compile(r"^(?P<key>(?:\"(?:\\.|[^\"])*\"|'(?:''|[^'])*'|[^:#'\"\[]+?)):(?P<rest>.*)$")


def load_yaml(text: str) -> Any:
    """Parse one YAML document and return Python objects."""
    text = text.lstrip("\ufeff")
    parser = _Parser(text.splitlines())
    parser.skip_preamble()
    document = parser.parse_document()
    parser.finish_document()
    return document


class _Parser:
    def __init__(self, raw_lines: list[str]) -> None:
        self.lines: list[tuple[int, str]] = []
        for index, raw in enumerate(raw_lines, start=1):
            if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
                raise YamlError("tabs cannot be used for indentation", index)
            self.lines.append((index, raw.rstrip("\n\r")))
        self.index = 0
        self.anchors: dict[str, Any] = {}

    def peek(self) -> tuple[int, str] | None:
        while self.index < len(self.lines):
            line_no, raw = self.lines[self.index]
            stripped = _strip_comment(raw).strip()
            if stripped == "":
                self.index += 1
                continue
            if _document_marker(raw):
                return None
            return line_no, raw
        return None

    def skip_preamble(self) -> None:
        while self.index < len(self.lines):
            _, raw = self.lines[self.index]
            stripped = _strip_comment(raw).strip()
            if stripped == "" or stripped.startswith("%") or _document_marker(raw):
                self.index += 1
                continue
            return

    def finish_document(self) -> None:
        while self.index < len(self.lines):
            line_no, raw = self.lines[self.index]
            stripped = _strip_comment(raw).strip()
            if stripped == "":
                self.index += 1
                continue
            if _document_marker(raw):
                self.index += 1
                break
            raise YamlError("only one YAML document is supported", line_no)
        while self.index < len(self.lines):
            line_no, raw = self.lines[self.index]
            stripped = _strip_comment(raw).strip()
            if stripped == "" or stripped.startswith("%"):
                self.index += 1
                continue
            raise YamlError("only one YAML document is supported", line_no)

    def pop(self) -> tuple[int, str]:
        item = self.peek()
        if item is None:
            raise YamlError("unexpected end of YAML")
        self.index += 1
        return item

    def parse_document(self) -> Any:
        if self.peek() is None:
            return None
        _, raw = self.peek()
        return self._parse_block(_indent_of(raw))

    def _parse_block(self, minimum_indent: int) -> Any:
        peeked = self.peek()
        if peeked is None:
            return None
        line_no, raw = peeked
        indent = _indent_of(raw)
        if indent < minimum_indent:
            return None
        content = _strip_comment(raw).strip()
        if content.startswith("-"):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while True:
            peeked = self.peek()
            if peeked is None:
                break
            line_no, raw = peeked
            if _indent_of(raw) != indent:
                break
            content = _strip_comment(raw)
            stripped = content.strip()
            if not stripped.startswith("-"):
                break
            self.pop()
            body, key_indent = _sequence_body(raw, line_no)
            anchor, body = _take_anchor(body, line_no)
            value = self._parse_sequence_value(line_no, key_indent, indent, body)
            self._store_anchor(anchor, value, line_no)
            items.append(value)
        return items

    def _parse_sequence_value(self, line_no: int, key_indent: int, dash_indent: int, body: str) -> Any:
        body = body.strip()
        if body == "":
            return self._parse_nested(dash_indent)
        if body.startswith("*") and re.fullmatch(r"\*[A-Za-z0-9_-]+", body):
            return self._resolve_alias(body[1:], line_no)
        if body.startswith("{") or body.startswith("["):
            flow_text, _ = self._collect_flow(body, line_no)
            return _parse_flow(flow_text, line_no, self.anchors)
        if re.fullmatch(r"[|>][+-]?\d*", body):
            return self._parse_block_scalar(body, dash_indent)
        if _KEY_VALUE.match(body):
            return self._parse_mapping_starting_with(line_no, key_indent, body)
        return _parse_scalar(body, line_no)

    def _parse_mapping_starting_with(self, line_no: int, key_indent: int, first_body: str) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        self._store_mapping_pair(mapping, line_no, key_indent, first_body)
        while True:
            peeked = self.peek()
            if peeked is None:
                break
            next_line_no, raw = peeked
            if _indent_of(raw) != key_indent:
                break
            stripped = _strip_comment(raw).strip()
            if stripped.startswith("-") or _KEY_VALUE.match(stripped) is None:
                break
            self.pop()
            self._store_mapping_pair(mapping, next_line_no, key_indent, stripped)
        return mapping

    def _store_mapping_pair(self, mapping: dict[str, Any], line_no: int, key_indent: int, body: str) -> None:
        match = _KEY_VALUE.match(body.strip())
        if match is None:
            raise YamlError(f"expected a key, found {body!r}", line_no)
        key = _parse_scalar(match.group("key").strip(), line_no)
        if not isinstance(key, str):
            key = str(key)
        rest = match.group("rest")
        if rest.startswith(" "):
            rest = rest[1:]
        elif rest != "":
            raise YamlError("expected a space after the mapping colon", line_no)
        anchor, rest = _take_anchor(rest, line_no)
        value = self._value_after_indicator(line_no, "", key_indent, rest, item_indent=key_indent + 2)
        self._store_anchor(anchor, value, line_no)
        if key == "<<":
            _apply_merge(mapping, value, line_no)
        else:
            mapping[key] = value

    def _parse_mapping(self, indent: int) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        while True:
            peeked = self.peek()
            if peeked is None:
                break
            line_no, raw = peeked
            if _indent_of(raw) != indent:
                break
            content = _strip_comment(raw)
            stripped = content.strip()
            if stripped.startswith("-"):
                break
            match = _KEY_VALUE.match(stripped)
            if match is None:
                raise YamlError(f"expected a key, found {stripped!r}", line_no)
            self.pop()
            key_token = match.group("key").strip()
            key = _parse_scalar(key_token, line_no)
            if not isinstance(key, str):
                key = str(key)
            rest = match.group("rest")
            if rest.startswith(" "):
                rest = rest[1:]
            elif rest != "":
                raise YamlError("expected a space after the mapping colon", line_no)
            anchor, rest = _take_anchor(rest, line_no)
            value = self._value_after_indicator(line_no, raw, indent, rest, item_indent=indent + 2)
            self._store_anchor(anchor, value, line_no)
            if key == "<<":
                _apply_merge(mapping, value, line_no)
            else:
                mapping[key] = value
        return mapping

    def _value_after_indicator(
        self,
        line_no: int,
        raw_line: str,
        parent_indent: int,
        rest: str,
        item_indent: int,
    ) -> Any:
        rest = rest.strip()
        if rest.startswith("*") and re.fullmatch(r"\*[A-Za-z0-9_-]+", rest):
            return self._resolve_alias(rest[1:], line_no)
        if rest == "|" or rest == ">" or re.fullmatch(r"[|>][+-]?\d*|\|[+-]?\d*|>[+-]?\d*", rest):
            return self._parse_block_scalar(rest, parent_indent)
        if rest.startswith("{") or rest.startswith("["):
            flow_text, inline = self._collect_flow(rest, line_no)
            value = _parse_flow(flow_text, line_no, self.anchors)
            return value
        if rest == "":
            return self._parse_nested(parent_indent)
        value = _parse_scalar(rest, line_no)
        continuation = self._plain_continuation(parent_indent)
        if continuation:
            if isinstance(value, str):
                return (value + " " + " ".join(continuation)).strip()
        return value

    def _parse_nested(self, parent_indent: int) -> Any:
        peeked = self.peek()
        if peeked is None:
            return None
        _, raw = peeked
        indent = _indent_of(raw)
        if indent <= parent_indent:
            return None
        content = _strip_comment(raw).strip()
        if content.startswith("-"):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _plain_continuation(self, parent_indent: int) -> list[str]:
        parts: list[str] = []
        while True:
            peeked = self.peek()
            if peeked is None:
                break
            _, raw = peeked
            indent = _indent_of(raw)
            if indent <= parent_indent:
                break
            stripped = _strip_comment(raw).strip()
            if stripped.startswith("-") or _KEY_VALUE.match(stripped):
                break
            self.pop()
            parts.append(stripped)
        return parts

    def _parse_block_scalar(self, header: str, parent_indent: int) -> str:
        folded = header.startswith(">")
        chomp = "clip"
        if "-" in header:
            chomp = "strip"
        elif "+" in header:
            chomp = "keep"
        number = re.search(r"\d+", header)
        explicit_indent = int(number.group(0)) if number else None
        raw_lines: list[tuple[int, str]] = []
        while self.index < len(self.lines):
            line_no, raw = self.lines[self.index]
            if raw.strip() == "":
                raw_lines.append((line_no, ""))
                self.index += 1
                continue
            indent = _indent_of(raw)
            if indent <= parent_indent:
                break
            raw_lines.append((line_no, raw))
            self.index += 1
        if not raw_lines:
            return ""
        content_indents = [_indent_of(raw) for _, raw in raw_lines if raw.strip() != ""]
        block_indent = explicit_indent + parent_indent if explicit_indent is not None else min(content_indents)
        pieces: list[str] = []
        for _, raw in raw_lines:
            if raw.strip() == "":
                pieces.append("")
            else:
                pieces.append(raw[block_indent:] if len(raw) >= block_indent else raw.lstrip(" "))
        text = _fold_lines(pieces) if folded else "\n".join(pieces)
        if chomp == "keep":
            return text if text.endswith("\n") else text + "\n"
        return text.rstrip("\n")

    def _collect_flow(self, first: str, line_no: int) -> tuple[str, bool]:
        chunks = [first]
        while not _flow_balanced("\n".join(chunks)):
            if self.index >= len(self.lines):
                raise YamlError("flow collection ran past the end of the file", line_no)
            _, raw = self.lines[self.index]
            self.index += 1
            chunks.append(_strip_comment(raw).strip())
        return "\n".join(chunks), len(chunks) == 1

    def _resolve_alias(self, name: str, line_no: int) -> Any:
        if name not in self.anchors:
            raise YamlError(f"unknown anchor '*{name}'", line_no)
        return copy.deepcopy(self.anchors[name])

    def _store_anchor(self, anchor: str | None, value: Any, line_no: int) -> None:
        if anchor is not None:
            self.anchors[anchor] = value


def _fold_lines(pieces: list[str]) -> str:
    folded: list[str] = []
    buffer: list[str] = []
    for piece in pieces:
        if piece == "":
            if buffer:
                folded.append(" ".join(buffer))
                buffer = []
            folded.append("")
        else:
            buffer.append(piece.strip())
    if buffer:
        folded.append(" ".join(buffer))
    return "\n".join(folded)


def _apply_merge(mapping: dict[str, Any], value: Any, line_no: int) -> None:
    sources: list[dict[str, Any]]
    if isinstance(value, dict):
        sources = [value]
    elif isinstance(value, list):
        sources = []
        for item in value:
            if not isinstance(item, dict):
                raise YamlError("merge key '<<' expects a mapping or a list of mappings", line_no)
            sources.append(item)
    else:
        raise YamlError("merge key '<<' expects a mapping", line_no)
    for source in sources:
        for key, item in source.items():
            if key not in mapping:
                mapping[key] = copy.deepcopy(item)


def _take_anchor(text: str, line_no: int) -> tuple[str | None, str]:
    match = re.match(r"^&([A-Za-z0-9_-]+)\s*(.*)$", text.strip())
    if not match:
        return None, text
    return match.group(1), match.group(2)


def _document_marker(raw: str) -> bool:
    if raw.startswith(" ") or raw.startswith("\t"):
        return False
    return _strip_comment(raw).strip() in {"---", "..."}


def _indent_of(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


def _sequence_body(raw: str, line_no: int) -> tuple[str, int]:
    content = _strip_comment(raw)
    leading = len(content) - len(content.lstrip(" "))
    without_indent = content[leading:]
    if not without_indent.startswith("-"):
        raise YamlError("expected a sequence dash", line_no)
    after_dash = without_indent[1:]
    if after_dash != "" and not after_dash.startswith(" "):
        raise YamlError("expected a space after the sequence dash", line_no)
    spaces = len(after_dash) - len(after_dash.lstrip(" "))
    body = after_dash[spaces:]
    key_indent = leading + 1 + spaces
    return body, key_indent


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if in_double:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
        elif in_single:
            if char == "'" and index + 1 < len(line) and line[index + 1] == "'":
                index += 1
            elif char == "'":
                in_single = False
        else:
            if char == "#":
                return line[:index]
            if char == '"':
                in_double = True
            elif char == "'":
                in_single = True
        index += 1
    return line


def _parse_scalar(token: str, line_no: int) -> Any:
    token = token.strip()
    if token == "" or token in {"null", "Null", "NULL", "~"}:
        return None
    if token in {"true", "True", "TRUE"}:
        return True
    if token in {"false", "False", "FALSE"}:
        return False
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return _unquote(token, line_no)
    if re.fullmatch(r"[+-]?\d+", token):
        try:
            return int(token)
        except ValueError:
            return token
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+", token):
        try:
            return float(token)
        except ValueError:
            return token
    return token


def _unquote(token: str, line_no: int) -> str:
    quote = token[0]
    body = token[1:-1]
    if quote == "'":
        return body.replace("''", "'")
    result: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(body):
            raise YamlError("dangling escape in a double-quoted string", line_no)
        escaped = body[index + 1]
        mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "/": "/"}
        if escaped in mapping:
            result.append(mapping[escaped])
            index += 2
            continue
        if escaped == "u" and index + 6 <= len(body):
            result.append(chr(int(body[index + 2 : index + 6], 16)))
            index += 6
            continue
        raise YamlError(f"unsupported escape \\{escaped}", line_no)
    return "".join(result)


def _flow_balanced(text: str) -> bool:
    square = 0
    curly = 0
    in_single = False
    in_double = False
    escaped = False
    for char in text:
        if in_double:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
            continue
        if in_single:
            if char == "'":
                in_single = False
            continue
        if char == '"':
            in_double = True
        elif char == "'":
            in_single = True
        elif char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        if square < 0 or curly < 0:
            return False
    return square == 0 and curly == 0 and not in_single and not in_double


def _parse_flow(text: str, line_no: int, anchors: dict[str, Any]) -> Any:
    parser = _FlowParser(text.strip(), line_no, anchors)
    value = parser.parse_value()
    parser.skip_space()
    if not parser.done():
        raise YamlError("unexpected text after a flow collection", line_no)
    return value


class _FlowParser:
    def __init__(self, text: str, line_no: int, anchors: dict[str, Any]) -> None:
        self.text = text
        self.line_no = line_no
        self.anchors = anchors
        self.position = 0

    def done(self) -> bool:
        return self.position >= len(self.text)

    def skip_space(self) -> None:
        while not self.done() and self.text[self.position] in " \t\r\n":
            self.position += 1

    def parse_value(self) -> Any:
        self.skip_space()
        if self.done():
            raise YamlError("missing value in a flow collection", self.line_no)
        char = self.text[self.position]
        if char == "[":
            return self.parse_sequence()
        if char == "{":
            return self.parse_mapping()
        if char in {"'", '"'}:
            return self.parse_quoted()
        if char == "*":
            return self.parse_alias()
        return self.parse_plain()

    def parse_sequence(self) -> list[Any]:
        self.position += 1
        items: list[Any] = []
        while True:
            self.skip_space()
            if self.done():
                raise YamlError("sequence is missing a closing bracket", self.line_no)
            if self.text[self.position] == "]":
                self.position += 1
                return items
            items.append(self.parse_value())
            self.skip_space()
            if self.done():
                raise YamlError("sequence is missing a closing bracket", self.line_no)
            if self.text[self.position] == ",":
                self.position += 1
                continue
            if self.text[self.position] == "]":
                self.position += 1
                return items
            raise YamlError("expected a comma in a flow sequence", self.line_no)

    def parse_mapping(self) -> dict[str, Any]:
        self.position += 1
        mapping: dict[str, Any] = {}
        while True:
            self.skip_space()
            if self.done():
                raise YamlError("mapping is missing a closing brace", self.line_no)
            if self.text[self.position] == "}":
                self.position += 1
                return mapping
            key = self.parse_value()
            if not isinstance(key, str):
                key = str(key)
            self.skip_space()
            if self.done() or self.text[self.position] != ":":
                raise YamlError("expected a colon in a flow mapping", self.line_no)
            self.position += 1
            value = self.parse_value()
            if key == "<<":
                _apply_merge(mapping, value, self.line_no)
            else:
                mapping[key] = value
            self.skip_space()
            if self.done():
                raise YamlError("mapping is missing a closing brace", self.line_no)
            if self.text[self.position] == ",":
                self.position += 1
                continue
            if self.text[self.position] == "}":
                self.position += 1
                return mapping
            raise YamlError("expected a comma in a flow mapping", self.line_no)

    def parse_quoted(self) -> str:
        quote = self.text[self.position]
        end = self.position + 1
        if quote == "'":
            chars: list[str] = []
            while end < len(self.text):
                if self.text[end] == "'":
                    if end + 1 < len(self.text) and self.text[end + 1] == "'":
                        chars.append("'")
                        end += 2
                        continue
                    self.position = end + 1
                    return "".join(chars)
                chars.append(self.text[end])
                end += 1
            raise YamlError("unterminated single-quoted string", self.line_no)
        escaped = False
        chars = []
        while end < len(self.text):
            char = self.text[end]
            if escaped:
                mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "/": "/"}
                if char in mapping:
                    chars.append(mapping[char])
                elif char == "u" and end + 5 <= len(self.text):
                    chars.append(chr(int(self.text[end + 1 : end + 5], 16)))
                    end += 4
                else:
                    raise YamlError(f"unsupported escape \\{char}", self.line_no)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                self.position = end + 1
                return "".join(chars)
            else:
                chars.append(char)
            end += 1
        raise YamlError("unterminated double-quoted string", self.line_no)

    def parse_alias(self) -> Any:
        match = re.match(r"\*([A-Za-z0-9_-]+)", self.text[self.position :])
        if match is None:
            raise YamlError("invalid alias", self.line_no)
        name = match.group(1)
        self.position += match.end()
        if name not in self.anchors:
            raise YamlError(f"unknown anchor '*{name}'", self.line_no)
        return copy.deepcopy(self.anchors[name])

    def parse_plain(self) -> Any:
        start = self.position
        while self.position < len(self.text) and self.text[self.position] not in ",[]{}:#\n":
            self.position += 1
        token = self.text[start:self.position].strip()
        if token == "":
            raise YamlError("missing plain scalar in a flow collection", self.line_no)
        return _parse_scalar(token, self.line_no)
