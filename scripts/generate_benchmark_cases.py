"""Materialize the reviewed synthetic benchmark case catalog without overwriting edits.

Run this only when the case catalog changes. Generated fixtures and hidden
oracles are committed and pinned by the suite manifest; this script is not run
by the repair agent or by a benchmark attempt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    report: str
    probe: str
    control: str
    expected_probe: int
    expected_control: int
    buggy_rule: str
    correct_rule: str


FORM_CASES = [
    Case(
        "form-blank-002",
        "form_frontend",
        "Whitespace-only item names are accepted.",
        "   ",
        "Widget",
        422,
        200,
        "value == ''",
        "not value.strip()",
    ),
    Case(
        "form-email-003",
        "form_frontend",
        "The contact form accepts an address without @.",
        "person.example.com",
        "p@example.com",
        422,
        200,
        "not value",
        "'@' not in value",
    ),
    Case(
        "form-phone-004",
        "form_frontend",
        "A phone field accepts letters.",
        "12x45",
        "12345",
        422,
        200,
        "len(value) != 5",
        "len(value) != 5 or not value.isdecimal()",
    ),
    Case(
        "form-age-005",
        "form_frontend",
        "The age form accepts a minor.",
        "17",
        "18",
        422,
        200,
        "not value.isdecimal() or int(value) < 17",
        "not value.isdecimal() or int(value) < 18",
    ),
    Case(
        "form-quantity-006",
        "form_frontend",
        "Zero quantity is accepted.",
        "0",
        "1",
        422,
        200,
        "not value.lstrip('-').isdecimal() or int(value) < 0",
        "not value.isdecimal() or int(value) <= 0",
    ),
    Case(
        "form-consent-007",
        "form_frontend",
        "The form accepts a declined consent.",
        "no",
        "yes",
        422,
        200,
        "not value",
        "value != 'yes'",
    ),
    Case(
        "form-slug-008",
        "form_frontend",
        "Uppercase URL slugs are accepted.",
        "Bad-Slug",
        "good-slug",
        422,
        200,
        "not value",
        "not value or value != value.lower()",
    ),
    Case(
        "form-password-009",
        "form_frontend",
        "A five-character password is accepted.",
        "short",
        "long-enough",
        422,
        200,
        "len(value) < 5",
        "len(value) < 8",
    ),
    Case(
        "form-confirm-010",
        "form_frontend",
        "Mismatched confirmation is accepted.",
        "alpha|beta",
        "alpha|alpha",
        422,
        200,
        "not value",
        "value.count('|') != 1 or value.split('|')[0] != value.split('|')[1]",
    ),
]

API_CASES = [
    ("api-create-001", "Create returns HTTP 200 instead of 201.", "create", 201, "Created"),
    (
        "api-malformed-002",
        "Malformed input returns HTTP 200 instead of 400.",
        "malformed",
        400,
        "Rejected",
    ),
    (
        "api-auth-003",
        "Missing auth returns HTTP 200 instead of 401.",
        "missing-token",
        401,
        "Rejected",
    ),
    (
        "api-role-004",
        "Forbidden role returns HTTP 200 instead of 403.",
        "forbidden",
        403,
        "Rejected",
    ),
    (
        "api-missing-005",
        "Missing record returns HTTP 200 instead of 404.",
        "missing",
        404,
        "Rejected",
    ),
    (
        "api-duplicate-006",
        "Duplicate request returns HTTP 200 instead of 409.",
        "duplicate",
        409,
        "Rejected",
    ),
    (
        "api-large-007",
        "Oversize payload returns HTTP 200 instead of 413.",
        "oversize",
        413,
        "Rejected",
    ),
    (
        "api-media-008",
        "Unsupported media type returns HTTP 200 instead of 415.",
        "bad-media",
        415,
        "Rejected",
    ),
    (
        "api-invalid-009",
        "Invalid field returns HTTP 200 instead of 422.",
        "invalid",
        422,
        "Rejected",
    ),
    (
        "api-rate-010",
        "Rate limit returns HTTP 200 instead of 429.",
        "rate-exhausted",
        429,
        "Rejected",
    ),
]

CSV_CASES = [
    Case(
        "csv-quoted-001",
        "csv_timestamp",
        "Quoted commas are rejected by CSV import.",
        'A,"B,C"',
        "A,B",
        200,
        200,
        "len(value.split(',')) != 2",
        "len(next(csv.reader([value]))) != 2",
    ),
    Case(
        "csv-bom-002",
        "csv_timestamp",
        "UTF-8 BOM headers are rejected.",
        "\ufeffname,qty\nWidget,1",
        "name,qty\nWidget,1",
        200,
        200,
        "not value.startswith('name,qty\\n')",
        "not value.lstrip('\\ufeff').startswith('name,qty\\n')",
    ),
    Case(
        "csv-crlf-003",
        "csv_timestamp",
        "CRLF files are rejected.",
        "name,qty\r\nWidget,1\r\n",
        "name,qty\nWidget,1\n",
        200,
        200,
        "not value.startswith('name,qty\\n')",
        "not value.replace('\\r\\n', '\\n').startswith('name,qty\\n')",
    ),
    Case(
        "csv-header-004",
        "csv_timestamp",
        "Missing required quantity header is accepted.",
        "name\nWidget",
        "name,qty\nWidget,1",
        422,
        200,
        "False",
        "not value.startswith('name,qty\\n')",
    ),
    Case(
        "csv-duplicate-005",
        "csv_timestamp",
        "Duplicate CSV column names are accepted.",
        "name,name\nA,B",
        "name,qty\nA,1",
        422,
        200,
        "False",
        "len(value.splitlines()[0].split(',')) != len(set(value.splitlines()[0].split(',')))",
    ),
    Case(
        "csv-number-006",
        "csv_timestamp",
        "A nonnumeric quantity is accepted.",
        "name,qty\nA,NaN",
        "name,qty\nA,1",
        422,
        200,
        "False",
        "not value.splitlines()[-1].split(',')[-1].isdecimal()",
    ),
    Case(
        "csv-negative-007",
        "csv_timestamp",
        "A negative quantity is accepted.",
        "name,qty\nA,-1",
        "name,qty\nA,1",
        422,
        200,
        "not value.splitlines()[-1].split(',')[-1].lstrip('-').isdecimal()",
        "not value.splitlines()[-1].split(',')[-1].isdecimal()",
    ),
    Case(
        "csv-blank-008",
        "csv_timestamp",
        "A trailing blank row fails import.",
        "name,qty\nA,1\n\n",
        "name,qty\nA,1\n",
        200,
        200,
        "value.endswith('\\n\\n')",
        "not value.startswith('name,qty\\n')",
    ),
    Case(
        "timestamp-leap-009",
        "csv_timestamp",
        "An invalid leap date is accepted.",
        "2025-02-29",
        "2024-02-29",
        422,
        200,
        "False",
        "not valid_date(value)",
    ),
    Case(
        "timestamp-offset-010",
        "csv_timestamp",
        "A timezone-naive instant is accepted.",
        "2026-01-01T12:00:00",
        "2026-01-01T12:00:00+05:30",
        422,
        200,
        "False",
        "not valid_offset(value)",
    ),
]

NONBUG_CASES = [
    Case(
        "nonbug-blank-001",
        "non_bug",
        "A blank item was rejected; no expected behavior was supplied.",
        "",
        "Widget",
        422,
        200,
        "not value.strip()",
        "not value.strip()",
    ),
    Case(
        "nonbug-email-002",
        "non_bug",
        "An address without @ was rejected as designed.",
        "bad.example.com",
        "p@example.com",
        422,
        200,
        "'@' not in value",
        "'@' not in value",
    ),
    Case(
        "nonbug-phone-003",
        "non_bug",
        "Letters in a numeric field were rejected.",
        "12x45",
        "12345",
        422,
        200,
        "len(value) != 5 or not value.isdecimal()",
        "len(value) != 5 or not value.isdecimal()",
    ),
    Case(
        "nonbug-age-004",
        "non_bug",
        "The age gate rejected a minor.",
        "17",
        "18",
        422,
        200,
        "not value.isdecimal() or int(value) < 18",
        "not value.isdecimal() or int(value) < 18",
    ),
    Case(
        "nonbug-quantity-005",
        "non_bug",
        "Zero quantity was rejected.",
        "0",
        "1",
        422,
        200,
        "not value.isdecimal() or int(value) <= 0",
        "not value.isdecimal() or int(value) <= 0",
    ),
    Case(
        "nonbug-consent-006",
        "non_bug",
        "Declined consent was rejected.",
        "no",
        "yes",
        422,
        200,
        "value != 'yes'",
        "value != 'yes'",
    ),
    Case(
        "nonbug-slug-007",
        "non_bug",
        "Uppercase slugs were rejected by policy.",
        "Bad-Slug",
        "good-slug",
        422,
        200,
        "not value or value != value.lower()",
        "not value or value != value.lower()",
    ),
    Case(
        "nonbug-password-008",
        "non_bug",
        "A short password was rejected.",
        "short",
        "long-enough",
        422,
        200,
        "len(value) < 8",
        "len(value) < 8",
    ),
    Case(
        "nonbug-confirm-009",
        "non_bug",
        "Mismatched confirmation was rejected.",
        "alpha|beta",
        "alpha|alpha",
        422,
        200,
        "value.count('|') != 1 or value.split('|')[0] != value.split('|')[1]",
        "value.count('|') != 1 or value.split('|')[0] != value.split('|')[1]",
    ),
    Case(
        "nonbug-date-010",
        "non_bug",
        "An invalid leap date was rejected.",
        "2025-02-29",
        "2024-02-29",
        422,
        200,
        "not valid_date(value)",
        "not valid_date(value)",
    ),
]


def _write_once(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Generated asset drifted; review before changing: {path}")
        return
    path.write_text(content, encoding="utf-8")


def _server(
    rule: str,
    category: str,
    trigger: str = "",
    api_status: int = 200,
    api_message: str = "Accepted",
    *,
    reference: bool = False,
    normalize_line_endings: bool = False,
) -> str:
    if category == "api_contract":
        branch_status = api_status if reference else 200
        classifier = (
            "    if value == " + repr(trigger) + ":\n"
            f"        return {branch_status}, {api_message!r}\n"
            "    return 200, 'Accepted'\n"
        )
    else:
        classifier = f"    if {rule}:\n        return 422, 'Rejected'\n    return 200, 'Accepted'\n"
    browser_value = "document.getElementById('value').value"
    if normalize_line_endings:
        browser_value += ".replaceAll(String.fromCharCode(10), String.fromCharCode(13,10))"
    return (
        '"""Synthetic case service. The hidden oracle is outside this tree."""\n'
        "import csv\nimport os\nfrom datetime import date, datetime\n"
        "from fastapi import FastAPI\n"
        "from fastapi.responses import HTMLResponse, JSONResponse\n\n"
        "app = FastAPI()\n\n"
        "def valid_date(value: str) -> bool:\n"
        "    try:\n        date.fromisoformat(value)\n        return True\n"
        "    except ValueError:\n        return False\n\n"
        "def valid_offset(value: str) -> bool:\n"
        "    try:\n        return datetime.fromisoformat(value).tzinfo is not None\n"
        "    except ValueError:\n        return False\n\n"
        "def classify(value: str) -> tuple[int, str]:\n" + classifier + "\n"
        "@app.get('/__aip_health')\n"
        "def health():\n"
        "    return JSONResponse({'ok': True}, headers={\n"
        "        'x-aip-fixture-instance': os.environ.get('AIP_FIXTURE_INSTANCE_ID', ''),\n"
        "    })\n\n"
        "@app.get('/', response_class=HTMLResponse)\n"
        "def index():\n"
        '    return \'\'\'<!doctype html><html lang="en"><meta charset="utf-8">\n'
        "<title>Benchmark case</title><body><h1>Benchmark case</h1>\n"
        '<label for="value">Value</label><textarea id="value"></textarea>\n'
        '<button id="check">Check</button><output id="result" aria-live="polite"></output>\n'
        "<script>document.getElementById('check').addEventListener('click', async () => {\n"
        "const response = await fetch('/api/check', {method:'POST',\n"
        "headers:{'Content-Type':'application/json'},\n"
        f"body:JSON.stringify({{value:{browser_value}}})}});\n"
        "const data = await response.json();\n"
        "document.getElementById('result').textContent = response.status + ':' + data.message;\n"
        "});</script></body></html>'''\n\n"
        "@app.post('/api/check')\n"
        "def check(payload: dict):\n"
        "    value = payload.get('value', '')\n"
        "    if not isinstance(value, str) or len(value) > 10000:\n"
        "        return JSONResponse({'message': 'Rejected'}, status_code=422)\n"
        "    status, message = classify(value)\n"
        "    return JSONResponse({'message': message}, status_code=status)\n"
    )


def _case_manifest(case: Case, expected_message: str) -> str:
    expected = f"{case.expected_probe}:{expected_message}"
    data = {
        "schema_version": "1.0",
        "case_id": case.case_id,
        "fixture_revision": "1.0",
        "category": case.category,
        "language": "python",
        "report": case.report,
        "start_command": [
            "python",
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        "health_url": "http://127.0.0.1:8001/__aip_health",
        "allowed_origin": "http://127.0.0.1:8001",
        "named_tests": {"baseline": ["python", "-m", "pytest", "-q", "tests"]},
        "scenario": {
            "steps": [
                {"action": "goto", "path": "/"},
                {"action": "fill", "role": "textbox", "name": "Value", "value": case.probe},
                {"action": "click", "role": "button", "name": "Check"},
                {"action": "expect_text", "text": expected},
            ],
            "mask_selectors": [],
        },
        "expected_baseline": "success" if case.category == "non_bug" else "failure",
        "oracle_revision": "1.0",
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _oracle(case: Case, expected_message: str, control_message: str) -> str:
    return (
        '"""Independent hidden checks; never mount beside the repair source."""\n'
        "from fastapi.testclient import TestClient\n"
        "from server import app\n\n"
        "def test_reported_case():\n"
        f"    response = TestClient(app).post('/api/check', json={{'value': {case.probe!r}}})\n"
        f"    assert response.status_code == {case.expected_probe}\n"
        f"    assert response.json() == {{'message': {expected_message!r}}}\n\n"
        "def test_control_case():\n"
        f"    response = TestClient(app).post('/api/check', json={{'value': {case.control!r}}})\n"
        f"    assert response.status_code == {case.expected_control}\n"
        f"    assert response.json() == {{'message': {control_message!r}}}\n"
    )


def generate(root: Path) -> list[str]:
    cases = FORM_CASES + CSV_CASES + NONBUG_CASES
    for case_id, report, trigger, expected_status, message in API_CASES:
        cases.append(
            Case(
                case_id,
                "api_contract",
                report,
                trigger,
                "ok",
                expected_status,
                200,
                "False",
                "False",
            )
        )
    if len(cases) != 39 or len({case.case_id for case in cases}) != 39:
        raise ValueError("Case catalog must contain 39 distinct additions")
    case_ids = []
    for case in cases:
        api = next((item for item in API_CASES if item[0] == case.case_id), None)
        api_message = api[4] if api else "Rejected" if case.expected_probe != 200 else "Accepted"
        control_message = "Rejected" if case.expected_control != 200 else "Accepted"
        base = root / "benchmarks" / "fixtures" / case.case_id / "base"
        oracle = root / "benchmarks" / "oracles" / case.case_id
        reference = root / "benchmarks" / "references" / case.case_id
        _write_once(
            base / "server.py",
            _server(
                case.buggy_rule,
                case.category,
                case.probe,
                case.expected_probe,
                api_message,
                normalize_line_endings=case.case_id == "csv-crlf-003",
            ),
        )
        _write_once(
            reference / "server.py",
            _server(
                case.correct_rule,
                case.category,
                case.probe,
                case.expected_probe,
                api_message,
                reference=True,
                normalize_line_endings=case.case_id == "csv-crlf-003",
            ),
        )
        _write_once(base / "requirements.txt", "fastapi==0.142.2\nuvicorn==0.54.0\npytest==9.1.1\n")
        _write_once(
            base / "tests" / "test_health.py",
            (
                "from fastapi.testclient import TestClient\n"
                "from server import app\n\n"
                "def test_health():\n"
                "    assert TestClient(app).get('/__aip_health').status_code == 200\n"
            ),
        )
        _write_once(
            root / "benchmarks" / "fixtures" / case.case_id / "manifest.json",
            _case_manifest(case, api_message),
        )
        _write_once(oracle / "test_hidden.py", _oracle(case, api_message, control_message))
        case_ids.append(case.case_id)
    return case_ids


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    made = generate(project_root)
    print(f"Verified {len(made)} generated benchmark cases")
