#!/usr/bin/env python3
"""
Run pytest with JUnit XML output and render a simple self-contained HTML report.

Usage:
  python scripts/run_pytest_html.py [pytest args...]

Env:
  REPORT_DIR   (default: reports)
  REPORT_NAME  (default: pytest_report.html)
  JUNIT_NAME   (default: pytest_junit.xml)
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
import xml.etree.ElementTree as ET


def _parse_junit(junit_path: str) -> dict:
    tree = ET.parse(junit_path)
    root = tree.getroot()

    suites = []
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
    elif root.tag == "testsuite":
        suites = [root]

    summary = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time": 0.0,
        "cases": [],
    }

    for suite in suites:
        summary["tests"] += int(suite.attrib.get("tests", 0))
        summary["failures"] += int(suite.attrib.get("failures", 0))
        summary["errors"] += int(suite.attrib.get("errors", 0))
        summary["skipped"] += int(suite.attrib.get("skipped", 0) or suite.attrib.get("skip", 0) or 0)
        summary["time"] += float(suite.attrib.get("time", 0.0))

        for case in suite.findall("testcase"):
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            duration = float(case.attrib.get("time", 0.0))
            status = "passed"
            message = ""

            fail = case.find("failure")
            err = case.find("error")
            skip = case.find("skipped")
            if fail is not None:
                status = "failed"
                message = fail.attrib.get("message", "") or (fail.text or "")
            elif err is not None:
                status = "error"
                message = err.attrib.get("message", "") or (err.text or "")
            elif skip is not None:
                status = "skipped"
                message = skip.attrib.get("message", "") or (skip.text or "")

            properties = {}
            props_el = case.find("properties")
            if props_el is not None:
                for prop in props_el.findall("property"):
                    pname = prop.attrib.get("name")
                    pvalue = prop.attrib.get("value", "")
                    if pname:
                        properties[pname] = pvalue

            summary["cases"].append(
                {
                    "classname": classname,
                    "name": name,
                    "status": status,
                    "time": duration,
                    "message": message.strip(),
                    "properties": properties,
                }
            )

    return summary


def _render_html(summary: dict) -> str:
    rows = []
    for case in summary["cases"]:
        props = case.get("properties") or {}
        props_html = ""
        if props:
            props_html = "<br>".join(
                f"<span><strong>{html.escape(k)}:</strong> {html.escape(v)}</span>" for k, v in props.items()
            )

        rows.append(
            "<tr class=\"status-{status}\">"
            "<td>{classname}</td>"
            "<td>{name}</td>"
            "<td>{status}</td>"
            "<td>{time:.3f}s</td>"
            "<td class=\"message\">{message}{props}</td>"
            "</tr>".format(
                classname=html.escape(case["classname"]),
                name=html.escape(case["name"]),
                status=html.escape(case["status"]),
                time=case["time"],
                message=html.escape(case["message"]),
                props=(f"<hr>{props_html}" if props_html else ""),
            )
        )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Pytest Report</title>
  <style>
    :root {{
      color-scheme: light;
    }}
    body {{
      font-family: ui-sans-serif, -apple-system, system-ui, "Segoe UI", sans-serif;
      margin: 24px;
      color: #111827;
      background: #ffffff;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 20px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, max-content));
      gap: 12px;
      margin: 12px 0 20px;
      font-size: 14px;
    }}
    .summary div {{
      padding: 8px 10px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #f9fafb;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}
    thead th {{
      text-align: left;
      padding: 10px;
      border-bottom: 2px solid #e5e7eb;
      background: #f3f4f6;
    }}
    tbody td {{
      padding: 8px 10px;
      border-bottom: 1px solid #e5e7eb;
      vertical-align: top;
    }}
    .status-passed td {{
      background: #ecfdf3;
    }}
    .status-failed td {{
      background: #fef2f2;
    }}
    .status-error td {{
      background: #fff7ed;
    }}
    .status-skipped td {{
      background: #f8fafc;
      color: #6b7280;
    }}
    .message {{
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <h1>Pytest Report</h1>
  <div class="summary">
    <div>Tests: {tests}</div>
    <div>Failures: {failures}</div>
    <div>Errors: {errors}</div>
    <div>Skipped: {skipped}</div>
    <div>Time: {time:.3f}s</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Class</th>
        <th>Test</th>
        <th>Status</th>
        <th>Time</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
""".format(
        tests=summary["tests"],
        failures=summary["failures"],
        errors=summary["errors"],
        skipped=summary["skipped"],
        time=summary["time"],
        rows="\n".join(rows),
    )


def main() -> int:
    report_dir = os.getenv("REPORT_DIR", "reports")
    report_name = os.getenv("REPORT_NAME", "pytest_report.html")
    junit_name = os.getenv("JUNIT_NAME", "pytest_junit.xml")

    os.makedirs(report_dir, exist_ok=True)

    junit_path = os.path.join(report_dir, junit_name)
    html_path = os.path.join(report_dir, report_name)

    cmd = [sys.executable, "-m", "pytest", "--junitxml", junit_path]
    cmd.extend(sys.argv[1:])
    result = subprocess.run(cmd)

    if not os.path.exists(junit_path):
        print(f"JUnit XML not found at {junit_path}", file=sys.stderr)
        return result.returncode

    try:
        summary = _parse_junit(junit_path)
    except Exception as err:
        print(f"Failed to parse JUnit XML: {err}", file=sys.stderr)
        return result.returncode

    html_report = _render_html(summary)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_report)

    print(f"HTML report written to {html_path}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
