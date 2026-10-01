"""Checks for the OpenAPI to JMeter 5.6.3 generator and the YAML subset parser."""

from __future__ import annotations

import json
import random
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openapi_to_jmx import (  # noqa: E402
    NoOperationsError,
    build_jmx,
    build_operations,
    generate_from_pattern,
    generate_integer,
    generate_value,
    integer_bounds,
    load_document,
    main,
    parse_args,
)
from openapi_to_jmx import DocumentIndex  # noqa: E402
from yaml_subset import YamlError, load_yaml  # noqa: E402


PETSTORE = ROOT / "examples" / "petstore.openapi.yaml"
SWAGGER = ROOT / "tests" / "fixtures" / "swagger-items.json"
OVERLAY = ROOT / "tests" / "fixtures" / "overlay.openapi.yaml"
EMPTY = ROOT / "tests" / "fixtures" / "empty-paths.openapi.yaml"


def options(**overrides):
    base = dict(
        openapi="spec.yaml",
        output="out.jmx",
        host="localhost",
        port="8080",
        protocol="http",
        api_key="XXXXXXXXXXXX",
        base_path="auto",
        server_index=0,
        seed=7,
        required_only=False,
        skip_deprecated=False,
        methods="",
        token_mode="get_client_token",
    )
    base.update(overrides)
    return parse_args([]) if False else _namespace(base)


def _namespace(values):
    import argparse

    return argparse.Namespace(**values)


def generate(path: Path, directory: Path, **overrides):
    seed = overrides.get("seed", 7)
    document = load_document(path)
    index = DocumentIndex(path, document)
    rng = random.Random(seed)
    namespace = _namespace(
        dict(
            openapi=str(path),
            output=str(directory / "out.jmx"),
            host="localhost",
            port="8080",
            protocol="http",
            api_key="XXXXXXXXXXXX",
            base_path="auto",
            server_index=0,
            seed=seed,
            required_only=False,
            skip_deprecated=False,
            methods="",
            token_mode="get_client_token",
        )
    )
    for key, value in overrides.items():
        setattr(namespace, key, value)
    operations, files = build_operations(index, namespace, rng)
    jmx = build_jmx(index, operations, namespace, seed)
    return operations, files, jmx, ET.fromstring(jmx)


def sampler_by_id(root: ET.Element, operation_id: str) -> ET.Element:
    for element in root.iter("HTTPSamplerProxy"):
        if f"[{operation_id}]" in element.attrib.get("testname", ""):
            return element
    raise AssertionError(operation_id)


def prop_text(element: ET.Element, name: str) -> str:
    for child in element:
        if child.tag == "stringProp" and child.attrib.get("name") == name:
            return child.text or ""
    raise AssertionError(name)


def tree_after(root: ET.Element, element: ET.Element) -> ET.Element:
    parents = {child: parent for parent in root.iter() for child in list(parent)}
    parent = parents[element]
    children = list(parent)
    return children[children.index(element) + 1]


def headers_of(root: ET.Element, sampler: ET.Element) -> dict[str, str]:
    found: dict[str, str] = {}
    for manager in tree_after(root, sampler).iter("HeaderManager"):
        for header in manager.iter("elementProp"):
            if header.attrib.get("elementType") != "Header":
                continue
            name = ""
            value = ""
            for child in header:
                if child.attrib.get("name") == "Header.name":
                    name = child.text or ""
                if child.attrib.get("name") == "Header.value":
                    value = child.text or ""
            found[name] = value
    return found


def body_of(sampler: ET.Element) -> str:
    for argument in sampler.iter("elementProp"):
        if argument.attrib.get("elementType") != "HTTPArgument":
            continue
        for child in argument:
            if child.attrib.get("name") == "Argument.value" and not argument.attrib.get("name"):
                return child.text or ""
    return ""


def assert_hash_pairs(node: ET.Element) -> None:
    if node.tag == "hashTree":
        children = list(node)
        if len(children) % 2 != 0:
            raise AssertionError("hashTree has an element without its hashTree")
        for index in range(0, len(children), 2):
            if children[index].tag == "hashTree" or children[index + 1].tag != "hashTree":
                raise AssertionError(children[index].tag)
            assert_hash_pairs(children[index + 1])
        return
    for child in list(node):
        assert_hash_pairs(child)


class YamlTests(unittest.TestCase):
    def test_openapi_shapes(self) -> None:
        document = load_yaml(
            """
            openapi: 3.0.3
            info:
              title: sample
              version: "1.0"
            yes: yes
            on: off
            flag: true
            empty: null
            note: >
              folded
              text
            description: |
              line one
              line two
            paths:
              /pets:
                parameters:
                  - name: limit
                    in: query
                    schema:
                      type: integer
                      enum: [1, 2, 3]
                get:
                  responses:
                    200:
                      description: ok
            anchor: &base
              kind: cat
            merged:
              <<: *base
              kind: dog
              extra: 1
            """
        )
        self.assertEqual(document["yes"], "yes")
        self.assertEqual(document["on"], "off")
        self.assertIs(document["flag"], True)
        self.assertIsNone(document["empty"])
        self.assertEqual(document["note"], "folded text")
        self.assertEqual(document["description"], "line one\nline two")
        self.assertEqual(document["paths"]["/pets"]["parameters"][0]["name"], "limit")
        self.assertEqual(document["paths"]["/pets"]["parameters"][0]["schema"]["enum"], [1, 2, 3])
        self.assertIn("200", document["paths"]["/pets"]["get"]["responses"])
        self.assertEqual(document["merged"]["kind"], "dog")
        self.assertEqual(document["merged"]["extra"], 1)
        self.assertNotIn("properties", document["merged"])

    def test_rejects_tabs_and_second_document(self) -> None:
        with self.assertRaises(YamlError):
            load_yaml("a:\n\tb: 1\n")
        with self.assertRaises(YamlError):
            load_yaml("a: 1\n---\nb: 2\n")


class PatternTests(unittest.TestCase):
    def test_digit_class_is_not_only_zero(self) -> None:
        values = {generate_from_pattern(random.Random(index), r"[\d]") for index in range(40)}
        self.assertTrue(values <= set("0123456789"))
        self.assertGreater(len(values), 1)
        code = generate_from_pattern(random.Random(3), r"[A-Z]{3}[0-9]{2}")
        self.assertIsNotNone(code)
        assert code is not None
        self.assertRegex(code, r"^[A-Z]{3}[0-9]{2}$")

    def test_integer_bounds(self) -> None:
        self.assertEqual(integer_bounds({"minimum": 1, "maximum": 20}), (1, 20))
        self.assertEqual(integer_bounds({"minimum": 0, "exclusiveMinimum": True, "maximum": 5}), (1, 5))
        self.assertEqual(integer_bounds({"exclusiveMinimum": 0, "exclusiveMaximum": 4}), (1, 3))
        for seed in range(20):
            value = generate_integer({"minimum": 0, "maximum": 30, "multipleOf": 2}, random.Random(seed))
            self.assertEqual(value % 2, 0)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 30)


class PlanTests(unittest.TestCase):
    def test_petstore_plan_matches_the_smoke_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            operations, files, jmx, root = generate(PETSTORE, directory, seed=11)
            self.assertEqual([item.operation_id for item in operations], ["uploadNote", "listPets", "createPet", "getPet", "deletePet"])
            self.assertNotIn("webhookMustBeSkipped", jmx)
            assert_hash_pairs(root)
            self.assertNotIn("\r", jmx)
            self.assertFalse(jmx.startswith("\ufeff"))
            self.assertIn('jmeter="5.6.3"', jmx)
            self.assertIn("Asserion.test_strings", jmx)
            self.assertIn("GetClientToken", jmx)
            self.assertIn("oauth2/token", jmx)
            self.assertIn('name="CriticalSectionController.lockName"', jmx)
            self.assertIn("cognito-access-token", jmx)
            self.assertIn("results/jmeter-results.jtl", jmx)
            self.assertIn("<xml>true</xml>", jmx)
            self.assertIn("<responseData>true</responseData>", jmx)
            self.assertIn("<samplerData>true</samplerData>", jmx)
            self.assertIn("<requestHeaders>true</requestHeaders>", jmx)
            self.assertIn('name="HTTPSampler.domain"></stringProp>', jmx)
            conditions = [
                element.text or ""
                for element in root.iter("stringProp")
                if element.attrib.get("name") == "IfController.condition"
            ]
            self.assertIn('${__groovy("get_client_token".equals(vars.get("COGNITO_TOKEN_MODE")))}', conditions)
            self.assertNotIn("&amp;quot;", jmx)
            self.assertIn("props.put(\"ACCESS_TOKEN\", token)", jmx)
            self.assertIn("prev.setQueryString(scrubbed)", jmx)
            self.assertIn("token_length", jmx)
            self.assertNotIn("log.info(token)", jmx)
            self.assertEqual(jmx.count('name="ThreadGroup.num_threads"'), 2)
            self.assertEqual(jmx.count('name="ThreadGroup.ramp_time"'), 2)
            self.assertIn('name="LoopController.loops">1<', jmx)
            self.assertIn('name="HTTPSampler.domain">${API_HOST}</stringProp>', jmx)
            self.assertIn('name="HTTPSampler.port">${API_PORT}</stringProp>', jmx)
            self.assertIn("XXXXXXXXXXXX", jmx)
            self.assertIn("your-perf-client-id", jmx)
            self.assertIn("callbacks", jmx)

            listed = sampler_by_id(root, "listPets")
            self.assertEqual(prop_text(listed, "HTTPSampler.method"), "GET")
            self.assertEqual(prop_text(listed, "HTTPSampler.path"), "/v1/pets")
            self.assertEqual(prop_text(listed, "HTTPSampler.protocol"), "${API_PROTOCOL}")
            listed_headers = headers_of(root, listed)
            self.assertEqual(listed_headers["X-API-KEY"], "${X_API_KEY}")
            self.assertEqual(listed_headers["Authorization"], "Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}")
            self.assertEqual(listed_headers["Accept"], "application/json")
            self.assertRegex(listed_headers["X-Request-Id"], r"^[a-z]{4}$")
            arguments = {
                item.attrib.get("name"): next(child.text or "" for child in item if child.attrib.get("name") == "Argument.value")
                for item in listed.iter("elementProp")
                if item.attrib.get("elementType") == "HTTPArgument"
            }
            self.assertIn("limit", arguments)
            self.assertIn("status", arguments)
            self.assertGreaterEqual(int(arguments["limit"]), 1)
            self.assertLessEqual(int(arguments["limit"]), 20)
            self.assertIn(arguments["status"], {"available", "pending", "sold"})

            created = sampler_by_id(root, "createPet")
            self.assertEqual(prop_text(created, "HTTPSampler.method"), "POST")
            self.assertRegex(prop_text(created, "HTTPSampler.path"), r"^/v1/pets\?dryRun=(true|false)$")
            self.assertEqual(headers_of(root, created)["Accept"], "application/xml")
            self.assertEqual(headers_of(root, created)["Content-Type"], "application/json")
            payload = json.loads(body_of(created))
            self.assertGreaterEqual(len(payload["name"]), 4)
            self.assertLessEqual(len(payload["name"]), 10)
            self.assertEqual(payload["age"] % 2, 0)
            self.assertGreaterEqual(payload["age"], 0)
            self.assertLessEqual(payload["age"], 30)
            self.assertIn(payload["kind"], {"dog", "cat"})
            self.assertNotIn("photo", payload)
            self.assertIn("nick", payload)
            self.assertTrue(payload["tag"] is None or isinstance(payload["tag"], str))

            fetched = sampler_by_id(root, "getPet")
            self.assertRegex(
                prop_text(fetched, "HTTPSampler.path"),
                r"^/v1/pets/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            )
            deleted = sampler_by_id(root, "deletePet")
            self.assertTrue(deleted.attrib["testname"].startswith("[deprecated] "))

            uploaded = sampler_by_id(root, "uploadNote")
            upload_headers = headers_of(root, uploaded)
            self.assertNotIn("Content-Type", upload_headers)
            self.assertEqual(upload_headers["Accept"], "text/plain")
            self.assertIn(">true<", "".join(uploaded.itertext()) if False else ET.tostring(uploaded, encoding="unicode"))
            self.assertIn("HTTPSampler.DO_MULTIPART_POST", ET.tostring(uploaded, encoding="unicode"))
            self.assertIn(">true<", ET.tostring(uploaded, encoding="unicode").split("DO_MULTIPART_POST")[1][:80])
            titles = [
                child.text or ""
                for item in uploaded.iter("elementProp")
                if item.attrib.get("name") == "title"
                for child in item
                if child.attrib.get("name") == "Argument.value"
            ]
            self.assertEqual(len(titles), 1)
            self.assertRegex(titles[0], r"^[A-Z]{3}[0-9]{2}$")
            self.assertTrue(any(name.endswith("uploads-photo.bin") for name, _payload in files))

            outer = list(root)[0]
            _test_plan, plan_tree = list(zip(list(outer)[0::2], list(outer)[1::2]))[0]
            groups = [
                (element, tree)
                for element, tree in zip(list(plan_tree)[0::2], list(plan_tree)[1::2])
                if element.tag == "ThreadGroup"
            ]
            self.assertEqual(len(groups), 1)
            self.assertFalse(any(child.tag == "HeaderManager" for child in list(groups[0][1])[0::2]))

    def test_same_seed_repeats_the_request_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, _, first, _ = generate(PETSTORE, directory, seed=11)
            _, _, second, _ = generate(PETSTORE, directory, seed=11)
            self.assertEqual(
                re.sub(r"生成時刻: .*", "生成時刻: FIXED", first),
                re.sub(r"生成時刻: .*", "生成時刻: FIXED", second),
            )

    def test_filters_base_path_and_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            operations, files, _, root = generate(PETSTORE, directory, seed=4, required_only=True, skip_deprecated=True, base_path="none")
            self.assertEqual([item.operation_id for item in operations], ["uploadNote", "listPets", "createPet", "getPet"])
            created = sampler_by_id(root, "createPet")
            payload = json.loads(body_of(created))
            self.assertEqual(set(payload), {"name", "age", "kind"})
            self.assertTrue(prop_text(created, "HTTPSampler.path").startswith("/pets?"))
            listed = sampler_by_id(root, "listPets")
            names = [item.attrib.get("name") for item in listed.iter("elementProp") if item.attrib.get("elementType") == "HTTPArgument"]
            self.assertIn("limit", names)
            self.assertNotIn("status", names)
            uploaded = sampler_by_id(root, "uploadNote")
            self.assertTrue(any(item.attrib.get("name") == "title" for item in uploaded.iter("elementProp")))
            self.assertFalse(any(name.endswith("uploads-photo.bin") for name, _payload in files))

    def test_swagger_optional_body_keeps_optional_properties(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, _, _, root = generate(SWAGGER, directory, seed=5)
            required_body = json.loads(body_of(sampler_by_id(root, "createItem")))
            optional_body = json.loads(body_of(sampler_by_id(root, "createNote")))
            self.assertEqual(set(required_body), {"sku", "note"})
            self.assertEqual(set(optional_body), {"sku", "note"})
            self.assertTrue(prop_text(sampler_by_id(root, "createItem"), "HTTPSampler.path").startswith("/v2/items"))
            _, _, _, required_only = generate(SWAGGER, directory, seed=5, required_only=True)
            kept = json.loads(body_of(sampler_by_id(required_only, "createItem")))
            self.assertEqual(set(kept), {"sku"})
            self.assertEqual(body_of(sampler_by_id(required_only, "createNote")), "")

    def test_openapi_31_ref_keeps_sibling_properties(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, _, root = generate(OVERLAY, Path(temporary), seed=2)
            payload = json.loads(body_of(sampler_by_id(root, "ping")))
            self.assertEqual(payload["id"], 5)
            self.assertEqual(payload["extra"], "fixed-extra")

    def test_command_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            missing = directory / "missing.yaml"
            self.assertEqual(main(["-i", str(missing), "-o", str(directory / "out.jmx")]), 3)
            self.assertEqual(main(["-i", str(EMPTY), "-o", str(directory / "out.jmx")]), 4)
            broken = directory / "broken.yaml"
            broken.write_text("a:\n\tb: 1\n", encoding="utf-8")
            self.assertEqual(main(["-i", str(broken), "-o", str(directory / "out.jmx")]), 3)
            output = directory / "pet.jmx"
            self.assertEqual(main(["-i", str(PETSTORE), "-o", str(output), "--seed", "9"]), 0)
            text = output.read_bytes()
            self.assertFalse(text.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", text)
            self.assertTrue((directory / "testdata" / "uploads-photo.bin").is_file())
        with self.assertRaises(SystemExit) as caught:
            parse_args(["--token-mode", "basic"])
        self.assertEqual(caught.exception.code, 2)

    def test_nullable_string_sometimes_is_null(self) -> None:
        document = {
            "openapi": "3.0.3",
            "info": {"title": "n", "version": "1"},
            "paths": {},
            "components": {"schemas": {"Tag": {"type": "string", "nullable": True, "minLength": 1, "maxLength": 4}}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "n.json"
            path.write_text("{}", encoding="utf-8")
            index = DocumentIndex(path, document)
            values = [
                generate_value({"$ref": "#/components/schemas/Tag"}, index, path, random.Random(seed), allow_null=True)
                for seed in range(80)
            ]
        self.assertTrue(any(value is None for value in values))
        self.assertTrue(any(isinstance(value, str) for value in values))


if __name__ == "__main__":
    unittest.main()
