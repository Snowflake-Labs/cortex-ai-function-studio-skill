-- Copyright (c) 2026 Snowflake Inc. All rights reserved.
-- Licensed under the Snowflake Skills License.
-- Refer to the LICENSE file in the root of this repository for full terms.

-- Custom evaluation metric for the legal document field extraction demo.
-- Scores predictions on four weighted dimensions:
--   governing_law (case-insensitive match with partial credit, weight 0.30)
--   parties (fuzzy token overlap, weight 0.30)
--   effective_date (normalized date comparison, weight 0.20)
--   expiration_date (normalized date comparison + "Perpetual", weight 0.20)
--
-- Substitution variables: {database}, {schema}

CREATE FUNCTION {database}.{schema}.DEMO_CONTRACT_EXTRACTION_METRIC(
    EXPECTED VARCHAR,
    PREDICTED VARCHAR
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'evaluate'
AS $$
import ast
import json
import re
from datetime import datetime

def _parse_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass
    return None

_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    "%B %d %Y", "%b %d %Y", "%m/%d/%y", "%Y/%m/%d",
]

def _normalize_date(text):
    if not text:
        return None
    s = text.strip()
    if s.lower() in ("n/a", "none", "null", ""):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s.lower().strip()

def _score_governing_law(expected, predicted):
    e = str(expected or "").strip().lower()
    p = str(predicted or "").strip().lower()
    if not e or not p:
        return 0.0
    if e == p:
        return 1.0
    if e in p or p in e:
        return 0.5
    return 0.0

def _tokenize(text):
    return set(re.split(r'[,;&\s]+', str(text or "").strip().lower())) - {""}

def _score_parties(expected, predicted):
    e = str(expected or "").strip().lower()
    p = str(predicted or "").strip().lower()
    if not e or not p:
        return 0.0
    if e == p:
        return 1.0
    if e in p or p in e:
        return 0.8
    exp_tokens = _tokenize(expected)
    pred_tokens = _tokenize(predicted)
    if not exp_tokens:
        return 0.0
    overlap = len(exp_tokens & pred_tokens)
    return overlap / len(exp_tokens)

def _score_date(expected, predicted):
    e_norm = _normalize_date(str(expected or ""))
    p_norm = _normalize_date(str(predicted or ""))
    if e_norm is None and p_norm is None:
        return 1.0
    if e_norm is None or p_norm is None:
        return 0.0
    return 1.0 if e_norm == p_norm else 0.0

def _score_expiration_date(expected, predicted):
    e = str(expected or "").strip().lower()
    p = str(predicted or "").strip().lower()
    if e == "perpetual" or p == "perpetual":
        return 1.0 if e == p else 0.0
    return _score_date(expected, predicted)

def evaluate(expected, predicted):
    exp = _parse_json(expected)
    pred = _parse_json(predicted)
    if exp is None or pred is None:
        return {"score": 0.0, "feedback": "Could not parse expected/predicted as JSON"}

    sub_scores = []
    feedback_parts = []

    # governing_law (weight 0.30)
    gl_score = _score_governing_law(exp.get("governing_law"), pred.get("governing_law"))
    sub_scores.append(("governing_law", gl_score, 0.30))
    feedback_parts.append(f"governing_law={gl_score:.1f}")

    # parties (weight 0.30)
    p_score = _score_parties(exp.get("parties"), pred.get("parties"))
    sub_scores.append(("parties", p_score, 0.30))
    feedback_parts.append(f"parties={p_score:.1f}")

    # effective_date (weight 0.20)
    ed_score = _score_date(exp.get("effective_date"), pred.get("effective_date"))
    sub_scores.append(("effective_date", ed_score, 0.20))
    feedback_parts.append(f"effective_date={ed_score:.1f}")

    # expiration_date (weight 0.20)
    xd_score = _score_expiration_date(exp.get("expiration_date"), pred.get("expiration_date"))
    sub_scores.append(("expiration_date", xd_score, 0.20))
    feedback_parts.append(f"expiration_date={xd_score:.1f}")

    total_weight = sum(w for _, _, w in sub_scores)
    combined = sum(s * w for _, s, w in sub_scores) / total_weight if total_weight > 0 else 0.0

    detail = " | ".join(feedback_parts)
    breakdown = ", ".join(f"{name}={s:.2f}*{w}" for name, s, w in sub_scores)
    feedback = f"{detail} [weights: {breakdown}]"

    return {"score": combined, "feedback": feedback}
$$;
