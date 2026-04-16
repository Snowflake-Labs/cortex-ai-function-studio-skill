# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Build policy-routing v6 benchmark rows from ``create_support_ticket_v6_dataset.sql.j2``.

``load_policy_v6_rows()`` returns 120 unique gold dicts (no padding). Used by
``tests/benchmark/export_benchmark_fixtures.py --policy``.

Print TRAINING_DATA-style output to stdout::

    uv run python src/generate_policy_data.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _split_sql_value_tuple(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_str = False
    while i < len(inner):
        c = inner[i]
        if in_str:
            if c == "'":
                if i + 1 < len(inner) and inner[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_str = False
                buf.append(c)
                i += 1
                continue
            buf.append(c)
            i += 1
            continue
        if c == "'":
            in_str = True
            buf.append(c)
            i += 1
            continue
        if c == ",":
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _sql_string_literal(field: str) -> str:
    s = field.strip()
    if not (s.startswith("'") and s.endswith("'")):
        raise ValueError(f"expected string literal, got {s[:40]!r}")
    return s[1:-1].replace("''", "'")


def _parse_int_literal(field: str) -> int:
    return int(field.strip())


def _extract_tuples_from_values(text: str) -> list[str]:
    m = re.search(r"FROM\s+VALUES", text, re.I)
    if not m:
        raise ValueError("FROM VALUES not found")
    pos = m.end()
    tuples: list[str] = []
    while True:
        while pos < len(text) and text[pos] in " \t\n\r,":
            pos += 1
        if pos >= len(text) or text[pos] != "(":
            break
        depth = 0
        start = pos
        in_str = False
        while pos < len(text):
            c = text[pos]
            if in_str:
                if c == "'":
                    if pos + 1 < len(text) and text[pos + 1] == "'":
                        pos += 2
                        continue
                    in_str = False
                pos += 1
                continue
            if c == "'":
                in_str = True
                pos += 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    pos += 1
                    tuples.append(text[start:pos])
                    break
            pos += 1
        else:
            break
    return tuples


def _after_anchor(full: str, anchor: str) -> str:
    idx = full.find(anchor)
    if idx < 0:
        raise ValueError(f"anchor not found: {anchor!r}")
    return full[idx:]


def load_policy_v6_rows() -> list[dict[str, str]]:
    """Return the 120 unique v6 gold rows (holdout + train variants), no padding."""
    root = Path(__file__).resolve().parent.parent
    j2 = (root / "demos/policy-conditioned-routing/create_support_ticket_v6_dataset.sql.j2").read_text()

    pol_tuples = _extract_tuples_from_values(_after_anchor(j2, "DEMO_COMPANY_ROUTING_POLICY_V6 AS"))
    policy_by_company: dict[str, dict[str, str]] = {}
    for tup in pol_tuples:
        inner = tup.strip()[1:-1]
        fields = _split_sql_value_tuple(inner)
        if len(fields) != 4:
            raise ValueError(f"policy row expected 4 fields, got {len(fields)}")
        company = _sql_string_literal(fields[0])
        policy_by_company[company] = {
            "POLICY_PROFILE": _sql_string_literal(fields[1]),
            "POLICY_TEXT": _sql_string_literal(fields[2]),
            "ENTITLEMENT_TEXT": _sql_string_literal(fields[3]),
        }

    ho_tuples = _extract_tuples_from_values(_after_anchor(j2, "DEMO_TICKETS_HARD_GOLD_V6_SMALL AS"))
    holdout_rows: list[dict[str, str]] = []
    for tup in ho_tuples:
        inner = tup.strip()[1:-1]
        fields = _split_sql_value_tuple(inner)
        if len(fields) != 11:
            raise ValueError(f"holdout expected 11 fields, got {len(fields)}")
        _parse_int_literal(fields[0])
        company = _sql_string_literal(fields[5])
        pol = policy_by_company[company]
        holdout_rows.append(
            {
                "SUBJECT": _sql_string_literal(fields[2]),
                "BODY": _sql_string_literal(fields[3]),
                "CUSTOMER_TIER": _sql_string_literal(fields[4]),
                "COMPANY_NAME": company,
                "POLICY_PROFILE": pol["POLICY_PROFILE"],
                "POLICY_TEXT": pol["POLICY_TEXT"],
                "ENTITLEMENT_TEXT": pol["ENTITLEMENT_TEXT"],
                "EXPECTED_LABEL": _sql_string_literal(fields[7]),
            }
        )

    train_anchor = "DEMO_TICKETS_POLICY_TRAIN_V6_LARGE AS\nWITH base_cases AS ("
    tr_off = j2.find(train_anchor)
    if tr_off < 0:
        raise ValueError("train anchor not found")
    tr_sub = j2[tr_off + len(train_anchor) :]
    tr_tuples = _extract_tuples_from_values(tr_sub)
    train_rows: list[dict[str, str]] = []
    for tup in tr_tuples:
        inner = tup.strip()[1:-1]
        fields = _split_sql_value_tuple(inner)
        if len(fields) != 17:
            raise ValueError(f"train base expected 17 fields, got {len(fields)}")
        company = _sql_string_literal(fields[2])
        tier = _sql_string_literal(fields[3])
        gold = _sql_string_literal(fields[5])
        pol = policy_by_company[company]
        pairs = [
            (_sql_string_literal(fields[9]), _sql_string_literal(fields[10])),
            (_sql_string_literal(fields[11]), _sql_string_literal(fields[12])),
            (_sql_string_literal(fields[13]), _sql_string_literal(fields[14])),
            (_sql_string_literal(fields[15]), _sql_string_literal(fields[16])),
        ]
        for subj, body in pairs:
            train_rows.append(
                {
                    "SUBJECT": subj,
                    "BODY": body,
                    "CUSTOMER_TIER": tier,
                    "COMPANY_NAME": company,
                    "POLICY_PROFILE": pol["POLICY_PROFILE"],
                    "POLICY_TEXT": pol["POLICY_TEXT"],
                    "ENTITLEMENT_TEXT": pol["ENTITLEMENT_TEXT"],
                    "EXPECTED_LABEL": gold,
                }
            )

    return holdout_rows + train_rows


def main() -> None:
    all_rows = load_policy_v6_rows()
    print(
        f"# rows: {len(all_rows)} unique v6 gold (no padding)",
        file=sys.stderr,
    )
    print("TRAINING_DATA = [")
    for row in all_rows:
        print(f"    {repr(row)},")
    print("]")


if __name__ == "__main__":
    main()
