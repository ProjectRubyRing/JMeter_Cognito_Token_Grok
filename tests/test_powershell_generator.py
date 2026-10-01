"""Windows PowerShell 5.1 の生成器が、Python 版と同じシナリオ契約を守るか確認する。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
PETSTORE = ROOT / "examples" / "petstore.openapi.yaml"
SWAGGER = ROOT / "tests" / "fixtures" / "swagger-items.json"
OVERLAY = ROOT / "tests" / "fixtures" / "overlay.openapi.yaml"
EMPTY = ROOT / "tests" / "fixtures" / "empty-paths.openapi.yaml"


def prop_text(element: ET.Element, name: str) -> str:
    for child in element:
        if child.tag == "stringProp" and child.attrib.get("name") == name:
            return child.text or ""
    raise AssertionError(name)


def sampler_by_id(root: ET.Element, operation_id: str) -> ET.Element:
    for element in root.iter("HTTPSamplerProxy"):
        if f"[{operation_id}]" in element.attrib.get("testname", ""):
            return element
    raise AssertionError(operation_id)


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
            name = header.attrib.get("name", "")
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
        if argument.attrib.get("name"):
            continue
        for child in argument:
            if child.attrib.get("name") == "Argument.value":
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


def load_jmx(path: Path) -> tuple[str, ET.Element]:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("jmx has a BOM")
    if b"\r\n" in data or b"\r" in data:
        raise AssertionError("jmx contains CR")
    text = data.decode("utf-8")
    return text, ET.fromstring(text)


def operation_order(root: ET.Element) -> list[str]:
    found = []
    known = ("uploadNote", "listPets", "createPet", "getPet", "deletePet", "webhookMustBeSkipped", "createItem", "createNote", "ping")
    for element in root.iter("HTTPSamplerProxy"):
        testname = element.attrib.get("testname", "")
        for operation_id in known:
            if f"[{operation_id}]" in testname:
                found.append(operation_id)
    return found


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class PowershellGeneratorTests(unittest.TestCase):
    def test_native_generator_matches_the_plan_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            broken = directory / "broken.yaml"
            broken.write_text("a:\n\tb: 1\n", encoding="utf-8", newline="\n")
            script = directory / "drive.ps1"
            script.write_bytes(b"\xef\xbb\xbf" + DRIVE_SCRIPT.encode("utf-8").replace(b"\r\n", b"\n"))
            completed = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    str(ROOT / "scripts" / "generate-jmx.ps1"),
                    str(directory),
                    str(PETSTORE),
                    str(SWAGGER),
                    str(OVERLAY),
                    str(EMPTY),
                    str(broken),
                ],
                check=False,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace")[-2000:])
            exits = dict(line.split("=", 1) for line in (directory / "exits.txt").read_text(encoding="ascii").splitlines())
            self.assertEqual(exits["pet"], "0")
            self.assertEqual(exits["pet2"], "0")
            self.assertEqual(exits["filtered"], "0")
            self.assertEqual(exits["swagger"], "0")
            self.assertEqual(exits["swagger-req"], "0")
            self.assertEqual(exits["overlay"], "0")
            self.assertEqual(exits["missing"], "3")
            self.assertEqual(exits["empty"], "4")
            self.assertEqual(exits["broken"], "3")
            self.assertEqual(exits["badmode"], "2")
            self.assertEqual(exits["help"], "0")
            self.check_pet(directory / "pet.jmx")
            self.check_pet(directory / "filtered.jmx", required_only=True, skip_deprecated=True, base_none=True)
            self.check_same_seed(directory / "pet.jmx", directory / "pet2.jmx")
            self.check_swagger(directory / "swagger.jmx", required_only=False)
            self.check_swagger(directory / "swagger-req.jmx", required_only=True)
            self.check_overlay(directory / "overlay.jmx")
            self.assertTrue((directory / "testdata" / "uploads-photo.bin").is_file())
            tags = (directory / "tags.txt").read_text(encoding="ascii").splitlines()
            self.assertIn("null", tags)
            self.assertIn("string", tags)

    def check_pet(self, path: Path, required_only: bool = False, skip_deprecated: bool = False, base_none: bool = False) -> None:
        text, root = load_jmx(path)
        assert_hash_pairs(root)
        order = operation_order(root)
        if required_only and skip_deprecated and base_none:
            self.assertEqual(order, ["uploadNote", "listPets", "createPet", "getPet"])
        else:
            self.assertEqual(order, ["uploadNote", "listPets", "createPet", "getPet", "deletePet"])
        self.assertNotIn("webhookMustBeSkipped", text)
        for needle in (
            'jmeter="5.6.3"',
            "Asserion.test_strings",
            "GetClientToken",
            "oauth2/token",
            'name="CriticalSectionController.lockName"',
            "cognito-access-token",
            "results/jmeter-results.jtl",
            "<xml>true</xml>",
            "<responseData>true</responseData>",
            "<samplerData>true</samplerData>",
            "<requestHeaders>true</requestHeaders>",
            'name="HTTPSampler.domain"></stringProp>',
            'props.put("ACCESS_TOKEN", token)',
            "prev.setQueryString(scrubbed)",
            "token_length",
            "XXXXXXXXXXXX",
            "your-perf-client-id",
            "callbacks",
            'name="LoopController.loops">1<',
            'name="HTTPSampler.domain">${API_HOST}</stringProp>',
            'name="HTTPSampler.port">${API_PORT}</stringProp>',
        ):
            self.assertIn(needle, text)
        self.assertNotIn("&amp;quot;", text)
        self.assertNotIn("log.info(token)", text)
        self.assertEqual(text.count('name="ThreadGroup.num_threads"'), 2)
        self.assertEqual(text.count('name="ThreadGroup.ramp_time"'), 2)
        conditions = [element.text or "" for element in root.iter("stringProp") if element.attrib.get("name") == "IfController.condition"]
        self.assertIn('${__groovy("get_client_token".equals(vars.get("COGNITO_TOKEN_MODE")))}', conditions)

        listed = sampler_by_id(root, "listPets")
        self.assertEqual(prop_text(listed, "HTTPSampler.method"), "GET")
        listed_path = prop_text(listed, "HTTPSampler.path")
        self.assertEqual("/pets" if base_none else "/v1/pets", listed_path if not base_none else listed_path.split("?")[0])
        headers = headers_of(root, listed)
        self.assertEqual(headers["X-API-KEY"], "${X_API_KEY}")
        self.assertEqual(headers["Authorization"], "Bearer ${__P(ACCESS_TOKEN,TOKEN_NOT_FOUND)}")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertRegex(headers["X-Request-Id"], r"^[a-z]{4}$")
        arguments = {
            item.attrib.get("name"): next(child.text or "" for child in item if child.attrib.get("name") == "Argument.value")
            for item in listed.iter("elementProp")
            if item.attrib.get("elementType") == "HTTPArgument"
        }
        self.assertGreaterEqual(int(arguments["limit"]), 1)
        self.assertLessEqual(int(arguments["limit"]), 20)
        if required_only:
            self.assertNotIn("status", arguments)
        else:
            self.assertIn(arguments["status"], {"available", "pending", "sold"})

        created = sampler_by_id(root, "createPet")
        created_path = prop_text(created, "HTTPSampler.path")
        self.assertRegex(created_path, r"^/pets\?dryRun=(true|false)$" if base_none else r"^/v1/pets\?dryRun=(true|false)$")
        created_headers = headers_of(root, created)
        self.assertEqual(created_headers["Accept"], "application/xml")
        self.assertEqual(created_headers["Content-Type"], "application/json")
        payload = json.loads(body_of(created))
        if required_only:
            self.assertEqual(set(payload), {"name", "age", "kind"})
        else:
            self.assertNotIn("photo", payload)
            self.assertIn("nick", payload)
            self.assertTrue(payload["tag"] is None or isinstance(payload["tag"], str))
        self.assertGreaterEqual(len(payload["name"]), 4)
        self.assertLessEqual(len(payload["name"]), 10)
        self.assertEqual(payload["age"] % 2, 0)
        self.assertGreaterEqual(payload["age"], 0)
        self.assertLessEqual(payload["age"], 30)
        self.assertIn(payload["kind"], {"dog", "cat"})

        fetched = sampler_by_id(root, "getPet")
        prefix = "/pets/" if base_none else "/v1/pets/"
        self.assertRegex(prop_text(fetched, "HTTPSampler.path"), rf"^{re.escape(prefix)}[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$")
        if not skip_deprecated:
            self.assertTrue(sampler_by_id(root, "deletePet").attrib["testname"].startswith("[deprecated] "))

        uploaded = sampler_by_id(root, "uploadNote")
        upload_headers = headers_of(root, uploaded)
        self.assertNotIn("Content-Type", upload_headers)
        self.assertEqual(upload_headers["Accept"], "text/plain")
        uploaded_xml = ET.tostring(uploaded, encoding="unicode")
        self.assertIn(">true<", uploaded_xml.split("DO_MULTIPART_POST")[1][:80])
        titles = [
            child.text or ""
            for item in uploaded.iter("elementProp")
            if item.attrib.get("name") == "title"
            for child in item
            if child.attrib.get("name") == "Argument.value"
        ]
        self.assertEqual(len(titles), 1)
        self.assertRegex(titles[0], r"^[A-Z]{3}[0-9]{2}$")
        photo_files = []
        for item in uploaded.iter("elementProp"):
            if item.attrib.get("elementType") != "HTTPFileArg":
                continue
            field = ""
            file_path = ""
            for child in item:
                if child.attrib.get("name") == "File.paramname":
                    field = child.text or ""
                if child.attrib.get("name") == "File.path":
                    file_path = child.text or ""
            if field == "photo":
                photo_files.append(file_path.replace("\\", "/"))
        if required_only:
            self.assertEqual(photo_files, [])
        else:
            self.assertEqual(photo_files, ["testdata/uploads-photo.bin"])

        outer = list(root)[0]
        plan_tree = list(zip(list(outer)[0::2], list(outer)[1::2]))[0][1]
        groups = [(element, tree) for element, tree in zip(list(plan_tree)[0::2], list(plan_tree)[1::2]) if element.tag == "ThreadGroup"]
        self.assertEqual(len(groups), 1)
        self.assertFalse(any(child.tag == "HeaderManager" for child in list(groups[0][1])[0::2]))

    def check_same_seed(self, first: Path, second: Path) -> None:
        normalize = lambda text: re.sub(r"生成時刻: .*", "生成時刻: FIXED", text)
        self.assertEqual(normalize(first.read_text(encoding="utf-8")), normalize(second.read_text(encoding="utf-8")))

    def check_swagger(self, path: Path, required_only: bool) -> None:
        _, root = load_jmx(path)
        item = json.loads(body_of(sampler_by_id(root, "createItem")))
        self.assertTrue(prop_text(sampler_by_id(root, "createItem"), "HTTPSampler.path").startswith("/v2/items"))
        note = body_of(sampler_by_id(root, "createNote"))
        if required_only:
            self.assertEqual(set(item), {"sku"})
            self.assertEqual(note, "")
        else:
            self.assertEqual(set(item), {"sku", "note"})
            self.assertEqual(set(json.loads(note)), {"sku", "note"})

    def check_overlay(self, path: Path) -> None:
        _, root = load_jmx(path)
        payload = json.loads(body_of(sampler_by_id(root, "ping")))
        self.assertEqual(payload["id"], 5)
        self.assertEqual(payload["extra"], "fixed-extra")


DRIVE_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$generator = $args[0]
$outDir = $args[1]
$pet = $args[2]
$swagger = $args[3]
$overlay = $args[4]
$empty = $args[5]
$broken = $args[6]
. $generator
$report = Join-Path $outDir "exits.txt"
function Save-Code($Name, $Code) {
    Add-Content -LiteralPath $report -Value ($Name + "=" + $Code) -Encoding ASCII
}
function Run-Case($Name, $ArgList) {
    $code = Invoke-JmxMain @ArgList
    Save-Code $Name $code
}
Run-Case "pet" @("-i", $pet, "-o", (Join-Path $outDir "pet.jmx"), "--seed", "11")
Run-Case "pet2" @("-i", $pet, "-o", (Join-Path $outDir "pet2.jmx"), "--seed", "11")
Run-Case "filtered" @("-i", $pet, "-o", (Join-Path $outDir "filtered.jmx"), "--seed", "4", "--required-only", "--skip-deprecated", "--base-path", "none")
Run-Case "swagger" @("-i", $swagger, "-o", (Join-Path $outDir "swagger.jmx"), "--seed", "5")
Run-Case "swagger-req" @("-i", $swagger, "-o", (Join-Path $outDir "swagger-req.jmx"), "--seed", "5", "--required-only")
Run-Case "overlay" @("-i", $overlay, "-o", (Join-Path $outDir "overlay.jmx"), "--seed", "2")
Run-Case "missing" @("-i", (Join-Path $outDir "missing.yaml"), "-o", (Join-Path $outDir "missing.jmx"))
Run-Case "empty" @("-i", $empty, "-o", (Join-Path $outDir "empty.jmx"), "--seed", "1")
Run-Case "broken" @("-i", $broken, "-o", (Join-Path $outDir "broken.jmx"))
Run-Case "badmode" @("-i", $pet, "-o", (Join-Path $outDir "bad.jmx"), "--token-mode", "basic")
Run-Case "help" @("--help")
$tagFile = Join-Path $outDir "tags.txt"
for ($seed = 1; $seed -le 40; $seed++) {
    $target = Join-Path $outDir "tag.jmx"
    $tagArgs = @("-i", $pet, "-o", $target, "--seed", ([string]$seed))
    $code = Invoke-JmxMain @tagArgs
    if ($code -ne 0) { Save-Code "tag" $code; exit 1 }
    $text = [System.IO.File]::ReadAllText($target)
    $marker = '&quot;tag&quot;: null'
    if ($text.Contains($marker)) { Add-Content -LiteralPath $tagFile -Value "null" -Encoding ASCII }
    else { Add-Content -LiteralPath $tagFile -Value "string" -Encoding ASCII }
}
exit 0
"""


if __name__ == "__main__":
    unittest.main()
