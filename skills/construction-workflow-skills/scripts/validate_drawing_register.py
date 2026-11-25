#!/usr/bin/env python3
"""Validate a construction drawing register CSV for the construction-workflow-skills skill.

Usage:
    python validate_drawing_register.py <drawing_register.csv> [--json]

Checks required columns, missing fields, duplicate drawing numbers, conflicting
active versions, invalid dates, invalid sheet counts, invalid status values and
broken supersession references. Exits with code 1 when errors are found.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REQUIRED_COLUMNS = [
    "drawing_no", "title", "discipline", "scale", "version",
    "issue_date", "sheets", "status", "supersedes", "superseded_by", "remarks",
]

REQUIRED_FIELDS = [
    "drawing_no", "title", "discipline", "version",
    "issue_date", "sheets", "status",
]

VALID_STATUS = {"有效", "作废", "待补", "待核"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = [dict(row) for row in reader]
    return columns, rows


def validate(columns, rows):
    errors = []
    warnings = []

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing_columns:
        errors.append("缺少必需列: " + ", ".join(missing_columns))
        return errors, warnings

    if not rows:
        errors.append("台账为空，仅含表头")
        return errors, warnings

    def val(row, key):
        return (row.get(key) or "").strip()

    for index, row in enumerate(rows, start=2):
        label = val(row, "drawing_no") or "第%d行" % index

        for field in REQUIRED_FIELDS:
            if not val(row, field):
                errors.append("第%d行(%s): 必填字段 %s 为空" % (index, label, field))

        status = val(row, "status")
        if status and status not in VALID_STATUS:
            errors.append("第%d行(%s): status 非法值 %s，允许: %s"
                          % (index, label, status, "/".join(sorted(VALID_STATUS))))

        issue_date = val(row, "issue_date")
        if issue_date:
            if not DATE_PATTERN.match(issue_date):
                errors.append("第%d行(%s): issue_date 格式应为 YYYY-MM-DD，实际 %s"
                              % (index, label, issue_date))
            else:
                try:
                    datetime.strptime(issue_date, "%Y-%m-%d")
                except ValueError:
                    errors.append("第%d行(%s): issue_date 不是有效日期 %s"
                                  % (index, label, issue_date))

        sheets = val(row, "sheets")
        if sheets:
            try:
                count = int(sheets)
                if count <= 0:
                    errors.append("第%d行(%s): sheets 必须为正整数，实际 %s"
                                  % (index, label, sheets))
            except ValueError:
                errors.append("第%d行(%s): sheets 不是整数，实际 %s"
                              % (index, label, sheets))

        if status == "作废" and not val(row, "superseded_by"):
            warnings.append("第%d行(%s): 作废图未填写 superseded_by 替代图号"
                            % (index, label))

    numbers = [val(row, "drawing_no") for row in rows if val(row, "drawing_no")]
    for number, count in Counter(numbers).items():
        if count > 1:
            versions = sorted({val(r, "version") for r in rows
                               if val(r, "drawing_no") == number})
            if len(versions) > 1:
                errors.append("图号 %s 重复出现 %d 次且版本不一致: %s"
                              % (number, count, ", ".join(versions)))
            else:
                warnings.append("图号 %s 重复出现 %d 次，请确认是否为多张同号图"
                                % (number, count))

    active_by_number = defaultdict(set)
    for row in rows:
        if val(row, "status") == "有效":
            active_by_number[val(row, "drawing_no")].add(val(row, "version"))
    for number, versions in active_by_number.items():
        if len(versions) > 1:
            errors.append("图号 %s 存在多个同时有效的版本: %s"
                          % (number, ", ".join(sorted(versions))))

    known_numbers = set(numbers)
    for row in rows:
        target = val(row, "superseded_by")
        if target and target not in known_numbers:
            warnings.append("图号 %s 的 superseded_by 指向 %s，该图号不在台账中"
                            % (val(row, "drawing_no"), target))

    pending = [(val(r, "drawing_no"), val(r, "status")) for r in rows
               if val(r, "status") in ("待补", "待核")]
    for number, status in pending:
        warnings.append("图号 %s 状态为 %s，进入 S2/S3 前需闭环" % (number, status))

    active_count = sum(1 for r in rows if val(r, "status") == "有效")
    return errors, warnings, {
        "total_rows": len(rows),
        "active_drawings": active_count,
        "obsolete_drawings": sum(1 for r in rows if val(r, "status") == "作废"),
    }


def main():
    parser = argparse.ArgumentParser(
        description="校验土建施工图图纸台账 CSV 的完整性与一致性")
    parser.add_argument("csv_path", help="图纸台账 CSV 路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出校验结果")
    args = parser.parse_args()

    path = Path(args.csv_path)
    if not path.is_file():
        message = {"error": "文件不存在: %s" % path}
        print(json.dumps(message, ensure_ascii=False) if args.json
              else "错误: 文件不存在: %s" % path)
        return 2

    try:
        columns, rows = load_rows(path)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        message = {"error": "读取失败: %s" % exc}
        print(json.dumps(message, ensure_ascii=False) if args.json
              else "错误: 读取失败: %s" % exc)
        return 2

    result = validate(columns, rows)
    errors, warnings = result[0], result[1]
    stats = result[2] if len(result) > 2 else {}

    if args.json:
        print(json.dumps({
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "stats": stats,
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 56)
        print("图纸台账校验报告: %s" % path)
        print("=" * 56)
        if stats:
            print("记录数: %d | 有效图: %d | 作废图: %d"
                  % (stats["total_rows"], stats["active_drawings"],
                     stats["obsolete_drawings"]))
        print("错误: %d 项 | 警告: %d 项" % (len(errors), len(warnings)))
        for item in errors:
            print("  [错误] " + item)
        for item in warnings:
            print("  [警告] " + item)
        print("-" * 56)
        print("结论: " + ("通过，可进入下一阶段" if not errors
                          else "未通过，请修复错误后再进入 S2/S3"))

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
