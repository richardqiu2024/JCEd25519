#!/usr/bin/env python3
"""Benchmark JavaCard allocator strategies and generate an HTML report.

Compatible with older Python 3 runtimes commonly found in lab containers.
"""

import argparse
import csv
import datetime as dt
import html
import json
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path


STRATEGY_PARAMS = {
    "ram": "00",
    "tradeoff": "01",
    "eeprom": "02",
}

TIMING_COLUMNS = ["sign_init", "sign_nonce", "sign_update", "sign_finalize", "total"]


class CommandError(RuntimeError):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark allocator install params and generate an HTML comparison report."
    )
    parser.add_argument("--reader", required=True, help='PC/SC reader name, e.g. "ACS ... 00 01"')
    parser.add_argument("--key", required=True, help="GP card key in hex")
    parser.add_argument(
        "--package-aid",
        default="6A6365643235353139",
        help="Package AID to delete before each install",
    )
    parser.add_argument(
        "--cap-path",
        default="applet/build/javacard/jced25519.cap",
        help="Path to CAP file",
    )
    parser.add_argument(
        "--gp-command",
        default="gp",
        help='GP command, default "gp"',
    )
    parser.add_argument(
        "--gradle-command",
        default="./gradlew",
        help='Gradle launcher, default "./gradlew"',
    )
    parser.add_argument(
        "--gradle-task",
        default="applet:test",
        help='Gradle test task, default "applet:test"',
    )
    parser.add_argument(
        "--build-task",
        default="applet:buildJavaCard",
        help='Gradle build task, default "applet:buildJavaCard"',
    )
    parser.add_argument(
        "--test-selector",
        default="tests.AppletTest.keygen_and_sign",
        help="JUnit test selector",
    )
    parser.add_argument(
        "--reader-index",
        type=int,
        default=1,
        help="Reader index passed to the Gradle test JVM",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["ram", "tradeoff"],
        choices=sorted(STRATEGY_PARAMS.keys()),
        help="Allocator strategies to compare",
    )
    parser.add_argument(
        "--install-param-flag",
        default="--params",
        help='GP install data flag, default "--params"',
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark-results",
        help="Directory for logs, CSV files, and the HTML report",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of test repetitions per strategy",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip CAP build and use the existing CAP file",
    )
    parser.add_argument(
        "--strict-delete",
        action="store_true",
        help="Fail immediately if package delete fails",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue with remaining strategies even if one strategy fails",
    )
    return parser.parse_args()


def split_command(command):
    return shlex.split(command)


def format_command(cmd):
    return " ".join(shlex.quote(part) for part in cmd)


def write_text(path, content):
    with path.open("w", encoding="utf-8") as fh:
        fh.write(content)


def run_command(cmd, cwd, log_path, check):
    start = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    stdout, _ = proc.communicate()
    elapsed_s = time.time() - start

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write("$ {0}\n\n".format(format_command(cmd)))
        fh.write(stdout)

    result = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_ms": round(elapsed_s * 1000.0, 3),
        "log_path": str(log_path),
        "stdout": stdout,
    }
    if check and proc.returncode != 0:
        raise CommandError(
            "Command failed with exit code {0}: {1}\nSee {2}".format(
                proc.returncode, format_command(cmd), log_path
            )
        )
    return result


def parse_measurements(csv_path):
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            sign_init = float(raw["sign_init"])
            sign_nonce = float(raw["sign_nonce"])
            sign_update = float(raw["sign_update"])
            sign_finalize = float(raw["sign_finalize"])
            rows.append(
                {
                    "sign_init": sign_init,
                    "sign_nonce": sign_nonce,
                    "sign_update": sign_update,
                    "sign_finalize": sign_finalize,
                    "total": sign_init + sign_nonce + sign_update + sign_finalize,
                }
            )
    return rows


def mean(values):
    return float(sum(values)) / float(len(values)) if values else 0.0


def summarize_metric(values):
    return {
        "avg": mean(values),
        "min": min(values),
        "max": max(values),
        "stddev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def summarize_measurements(rows):
    summary = {}
    for metric in TIMING_COLUMNS:
        values = [row[metric] for row in rows]
        summary[metric] = summarize_metric(values)
    return summary


def write_summary_csv(path, strategy_results):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "strategy",
                "status",
                "install_param",
                "build_ms",
                "delete_ms",
                "install_ms",
                "test_ms",
                "sign_init_avg_ms",
                "sign_nonce_avg_ms",
                "sign_update_avg_ms",
                "sign_finalize_avg_ms",
                "total_avg_ms",
                "total_stddev_ms",
            ]
        )
        for result in strategy_results:
            timings = result.get("timings", {})
            total = timings.get("total", {})
            writer.writerow(
                [
                    result["strategy"],
                    result["status"],
                    result["install_param"],
                    result.get("build_ms", ""),
                    result.get("delete_ms", ""),
                    result.get("install_ms", ""),
                    result.get("test_ms", ""),
                    timings.get("sign_init", {}).get("avg", ""),
                    timings.get("sign_nonce", {}).get("avg", ""),
                    timings.get("sign_update", {}).get("avg", ""),
                    timings.get("sign_finalize", {}).get("avg", ""),
                    total.get("avg", ""),
                    total.get("stddev", ""),
                ]
            )


def bar_cell(value, max_value, label):
    width = 0.0 if max_value <= 0 else (value / max_value) * 100.0
    return (
        '<div class="bar-cell">'
        '<span class="bar-label">{0}</span>'
        '<div class="bar-track"><div class="bar-fill" style="width:{1:.2f}%"></div></div>'
        '<span class="bar-value">{2:.2f} ms</span>'
        "</div>"
    ).format(html.escape(label), width, value)


def generate_html_report(report_path, args, build_result, strategy_results):
    successful = [r for r in strategy_results if r["status"] == "passed"]

    total_values = [r["timings"]["total"]["avg"] for r in successful]
    total_max = max(total_values) if total_values else 0.0

    metric_max = {}
    for metric in TIMING_COLUMNS:
        metric_values = [r["timings"][metric]["avg"] for r in successful]
        metric_max[metric] = max(metric_values) if metric_values else 0.0

    rows_html = []
    for result in strategy_results:
        if result["status"] != "passed":
            rows_html.append(
                "<tr>"
                "<td>{0}</td>"
                "<td>{1}</td>"
                "<td class='status-fail'>{2}</td>"
                "<td colspan='6'>{3}</td>"
                "</tr>".format(
                    html.escape(result["strategy"]),
                    html.escape(result["install_param"]),
                    html.escape(result["status"]),
                    html.escape(result.get("error", "failed")),
                )
            )
            continue

        timings = result["timings"]
        rows_html.append(
            "<tr>"
            "<td>{0}</td>"
            "<td>{1}</td>"
            "<td class='status-pass'>passed</td>"
            "<td>{2:.2f}</td>"
            "<td>{3:.2f}</td>"
            "<td>{4:.2f}</td>"
            "<td>{5:.2f}</td>"
            "<td>{6:.2f}</td>"
            "<td>{7:.2f}</td>"
            "</tr>".format(
                html.escape(result["strategy"]),
                html.escape(result["install_param"]),
                timings["sign_init"]["avg"],
                timings["sign_nonce"]["avg"],
                timings["sign_update"]["avg"],
                timings["sign_finalize"]["avg"],
                timings["total"]["avg"],
                timings["total"]["stddev"],
            )
        )

    comparison_sections = []
    for metric in TIMING_COLUMNS:
        metric_rows = []
        sorted_results = sorted(successful, key=lambda item: item["timings"][metric]["avg"])
        for result in sorted_results:
            metric_rows.append(
                bar_cell(
                    result["timings"][metric]["avg"],
                    metric_max[metric],
                    "{0} ({1})".format(result["strategy"], result["install_param"]),
                )
            )
        comparison_sections.append(
            "<section class='metric-card'>"
            "<h3>{0}</h3>{1}</section>".format(
                html.escape(metric.replace("_", " ").title()),
                "".join(metric_rows),
            )
        )

    raw_json = json.dumps(
        {
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
            "args": vars(args),
            "build_result": build_result,
            "strategy_results": strategy_results,
        },
        ensure_ascii=False,
        indent=2,
    )

    command_duration_rows = []
    for result in successful:
        command_duration_rows.append(
            bar_cell(result["timings"]["total"]["avg"], total_max, result["strategy"])
        )

    report_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Allocator Benchmark Report</title>
  <style>
    body {{
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      padding: 24px;
      color: #17202a;
      background: linear-gradient(180deg, #f7f3ec 0%, #eef5f0 100%);
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    h1, h2, h3 {{
      margin: 0 0 12px;
    }}
    .hero {{
      padding: 24px;
      border-radius: 18px;
      background: linear-gradient(135deg, #183a37, #315c4f);
      color: #f7f3ec;
      box-shadow: 0 18px 40px rgba(24, 58, 55, 0.2);
      margin-bottom: 20px;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .meta-card, .panel, .metric-card {{
      background: rgba(255, 255, 255, 0.9);
      border: 1px solid rgba(24, 58, 55, 0.08);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 10px 24px rgba(24, 58, 55, 0.08);
    }}
    .meta-card strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #47635c;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      background: white;
      border-radius: 12px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #e5ece7;
      text-align: left;
    }}
    th {{
      background: #edf4ef;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #47635c;
    }}
    .status-pass {{
      color: #1d6f42;
      font-weight: 700;
    }}
    .status-fail {{
      color: #a12d2f;
      font-weight: 700;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
      margin-top: 20px;
    }}
    .bar-cell {{
      display: grid;
      grid-template-columns: minmax(120px, 180px) 1fr auto;
      gap: 12px;
      align-items: center;
      margin: 10px 0;
    }}
    .bar-track {{
      height: 12px;
      background: #d8e6dd;
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      background: linear-gradient(90deg, #2d6a4f, #74a57f);
      border-radius: 999px;
    }}
    .bar-label {{
      font-weight: 600;
    }}
    .bar-value {{
      font-variant-numeric: tabular-nums;
      color: #47635c;
    }}
    details {{
      margin-top: 16px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f4f7f5;
      padding: 16px;
      border-radius: 12px;
      overflow: auto;
    }}
    .note {{
      margin-top: 14px;
      color: #47635c;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Allocator Benchmark Report</h1>
      <p>JavaCard install-parameter benchmark for allocator strategies.</p>
      <div class="meta">
        <div class="meta-card"><strong>Reader</strong>{reader}</div>
        <div class="meta-card"><strong>Strategies</strong>{strategies}</div>
        <div class="meta-card"><strong>CAP</strong>{cap}</div>
        <div class="meta-card"><strong>Test</strong>{test}</div>
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Param</th>
            <th>Status</th>
            <th>Sign Init Avg</th>
            <th>Sign Nonce Avg</th>
            <th>Sign Update Avg</th>
            <th>Sign Finalize Avg</th>
            <th>Total Avg</th>
            <th>Total Stddev</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      <p class="note">Timing data comes from the existing Gradle/JUnit test's measurement CSV and excludes the nonce hex column.</p>
    </section>

    <section class="grid">
      {comparison_sections}
    </section>

    <section class="panel" style="margin-top:20px;">
      <h2>Command Durations</h2>
      {command_duration_rows}
      <p class="note">Average total here is the sum of sign_init, sign_nonce, sign_update, and sign_finalize for each measured signing round.</p>
    </section>

    <section class="panel" style="margin-top:20px;">
      <h2>Artifacts</h2>
      <ul>
        <li>HTML report: {report_path}</li>
        <li>Summary CSV: summary.csv</li>
        <li>Raw JSON: summary.json</li>
      </ul>
    </section>

    <details>
      <summary>Raw JSON</summary>
      <pre>{raw_json}</pre>
    </details>
  </div>
</body>
</html>
""".format(
        reader=html.escape(args.reader),
        strategies=", ".join(html.escape(s) for s in args.strategies),
        cap=html.escape(args.cap_path),
        test=html.escape(args.test_selector),
        rows_html="".join(rows_html),
        comparison_sections="".join(comparison_sections),
        command_duration_rows="".join(command_duration_rows),
        report_path=html.escape(str(report_path)),
        raw_json=html.escape(raw_json),
    )

    write_text(report_path, report_html)


def benchmark_strategy(root, args, strategy, install_param, build_ms, output_dir):
    result = {
        "strategy": strategy,
        "install_param": install_param,
        "status": "pending",
        "build_ms": build_ms,
    }
    strategy_dir = output_dir / strategy
    strategy_dir.mkdir(parents=True, exist_ok=True)

    try:
        gp_base = split_command(args.gp_command)
        delete_cmd = gp_base + [
            "-r",
            args.reader,
            "--key",
            args.key,
            "--deletedeps",
            "--delete",
            args.package_aid,
        ]
        try:
            delete_run = run_command(delete_cmd, root, strategy_dir / "delete.log", check=True)
            result["delete_ms"] = delete_run["elapsed_ms"]
        except CommandError as exc:
            if args.strict_delete:
                raise
            result["delete_ms"] = None
            result["delete_warning"] = str(exc)

        install_cmd = gp_base + [
            "-r",
            args.reader,
            "--key",
            args.key,
            "--install",
            args.cap_path,
            args.install_param_flag,
            install_param,
        ]
        install_run = run_command(install_cmd, root, strategy_dir / "install.log", check=True)
        result["install_ms"] = install_run["elapsed_ms"]

        all_rows = []
        gradle_base = split_command(args.gradle_command)
        test_runs = []
        for repeat in range(1, args.repeats + 1):
            repeat_prefix = "repeat-{0:02d}".format(repeat)
            measurement_path = strategy_dir / (repeat_prefix + "-measurement.csv")
            test_cmd = gradle_base + [
                args.gradle_task,
                "--tests",
                args.test_selector,
                "-Djc.test.readerIndex={0}".format(args.reader_index),
                "-Djc.test.measurementFile={0}".format(str(measurement_path)),
                "--rerun-tasks",
            ]
            test_run = run_command(test_cmd, root, strategy_dir / (repeat_prefix + "-test.log"), check=True)
            test_runs.append(
                {
                    "repeat": repeat,
                    "elapsed_ms": test_run["elapsed_ms"],
                    "log_path": str(strategy_dir / (repeat_prefix + "-test.log")),
                    "measurement_path": str(measurement_path),
                }
            )
            all_rows.extend(parse_measurements(measurement_path))

        result["test_runs"] = test_runs
        result["test_ms"] = mean([run["elapsed_ms"] for run in test_runs])
        result["row_count"] = len(all_rows)
        result["timings"] = summarize_measurements(all_rows)
        result["status"] = "passed"
        return result
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        return result


def main():
    args = parse_args()
    root = Path.cwd()
    output_root = root / args.output_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)

    build_result = None
    if not args.skip_build:
        build_cmd = split_command(args.gradle_command) + [args.build_task, "--rerun-tasks"]
        try:
            build_result = run_command(build_cmd, root, output_root / "build.log", check=True)
        except CommandError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    strategy_results = []
    for strategy in args.strategies:
        install_param = STRATEGY_PARAMS[strategy]
        print("[benchmark] strategy={0} install_param={1}".format(strategy, install_param))
        result = benchmark_strategy(
            root=root,
            args=args,
            strategy=strategy,
            install_param=install_param,
            build_ms=build_result["elapsed_ms"] if build_result else None,
            output_dir=output_root,
        )
        strategy_results.append(result)
        if result["status"] != "passed":
            print(
                "[benchmark] {0} failed: {1}".format(
                    strategy, result.get("error", "unknown error")
                ),
                file=sys.stderr,
            )
            if not args.keep_going:
                break

    summary_json_path = output_root / "summary.json"
    summary_csv_path = output_root / "summary.csv"
    report_path = output_root / "report.html"

    write_text(
        summary_json_path,
        json.dumps(
            {
                "generated_at": dt.datetime.utcnow().isoformat() + "Z",
                "args": vars(args),
                "build_result": build_result,
                "strategy_results": strategy_results,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    write_summary_csv(summary_csv_path, strategy_results)
    generate_html_report(report_path, args, build_result, strategy_results)

    print("[benchmark] summary: {0}".format(summary_csv_path))
    print("[benchmark] report : {0}".format(report_path))

    if all(result["status"] == "passed" for result in strategy_results):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
