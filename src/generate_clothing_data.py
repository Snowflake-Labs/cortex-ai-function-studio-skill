# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Generate sample data from the Fashion Second-Hand dataset for clothing condition classification.

Downloads images from fnauman/fashion-second-hand-front-only-rgb on HuggingFace,
samples a balanced subset across 5 condition classes, resizes for manageable file
sizes, uploads to a Snowflake stage organized by class label (folder-per-class),
and creates labeled train/test tables.

Dataset: CC BY 4.0 by Faizan Nauman et al. — 31K+ expert-annotated garment images
graded on a 1-5 condition scale.

Condition labels:
    1 → unsalvageable
    2 → poor
    3 → fair
    4 → good
    5 → like_new

Example usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_clothing_data.py \\
        --connection MY_CONNECTION --database TEMP --schema PUBLIC
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_clothing_data.py \\
        --connection MY_CONNECTION --database TEMP --schema PUBLIC \\
        --per-class 50 --train-pct 60
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import snowflake.connector

logger = logging.getLogger(__name__)

DATASET_NAME = "fnauman/fashion-second-hand-front-only-rgb"
CONDITION_MAP = {1: "unsalvageable", 2: "poor", 3: "fair", 4: "good", 5: "like_new"}
MAX_PIXEL = 800
JPEG_QUALITY = 85


def download_and_sample(dest_dir: str, per_class: int, seed: int) -> Path:
    """Download dataset, sample balanced images, resize, and organize by label."""
    from datasets import load_dataset
    from PIL import Image

    logger.info(f"Loading dataset {DATASET_NAME}...")
    ds = load_dataset(DATASET_NAME, split="train")

    out_dir = Path(dest_dir) / "by_label"
    out_dir.mkdir(parents=True, exist_ok=True)

    import random

    random.seed(seed)

    indices_by_class: dict[int, list[int]] = {c: [] for c in CONDITION_MAP}
    for i, row in enumerate(ds):
        cond = row.get("condition")
        if cond in indices_by_class:
            indices_by_class[cond].append(i)

    img_idx = 0
    for cond, label in CONDITION_MAP.items():
        label_dir = out_dir / label
        label_dir.mkdir(exist_ok=True)
        pool = indices_by_class[cond]
        random.shuffle(pool)
        sample = pool[: min(per_class, len(pool))]
        logger.info(f"  {label}: sampling {len(sample)} images")

        for idx in sample:
            img: Image.Image = ds[idx]["image"]
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((MAX_PIXEL, MAX_PIXEL), Image.LANCZOS)
            img.save(label_dir / f"garment_{img_idx:04d}.jpg", "JPEG", quality=JPEG_QUALITY)
            img_idx += 1

    total = sum(len(list((out_dir / l).iterdir())) for l in CONDITION_MAP.values())
    logger.info(f"Saved {total} images to {out_dir}")
    return out_dir


def setup_stage(
    conn: snowflake.connector.SnowflakeConnection,
    database: str,
    schema: str,
) -> str:
    """Create schema (if needed) and SSE-encrypted stage for multimodal files."""
    fqn = f"{database}.{schema}"
    stage_fqn = f"{fqn}.DEMO_CLOTHING_STAGE"

    conn.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {fqn}")
    conn.cursor().execute(f"USE SCHEMA {fqn}")
    conn.cursor().execute(
        f"CREATE OR REPLACE STAGE {stage_fqn} "
        f"ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE') DIRECTORY = (ENABLE = TRUE)"
    )
    logger.info(f"Stage ready: {stage_fqn}")
    return stage_fqn


def upload_images(
    connection_name: str,
    stage_fqn: str,
    image_dir: Path,
) -> None:
    """Upload label-organized images to stage via snow CLI."""
    for label in CONDITION_MAP.values():
        label_dir = image_dir / label
        if not label_dir.exists():
            continue
        cmd = (
            f"snow sql -q \"PUT file://{label_dir}/* @{stage_fqn}/{label}/ "
            f"AUTO_COMPRESS=FALSE OVERWRITE=TRUE;\" "
            f"--connection {connection_name}"
        )
        logger.info(f"Uploading {label}...")
        subprocess.run(cmd, shell=True, check=True, capture_output=True)

    refresh_cmd = (
        f"snow sql -q \"ALTER STAGE {stage_fqn} REFRESH;\" "
        f"--connection {connection_name}"
    )
    subprocess.run(refresh_cmd, shell=True, check=True, capture_output=True)
    logger.info("Stage refreshed.")


def create_tables(
    conn: snowflake.connector.SnowflakeConnection,
    database: str,
    schema: str,
    stage_fqn: str,
    train_pct: int,
) -> None:
    """Create stratified train/test tables from the stage directory."""
    fqn = f"{database}.{schema}"
    cur = conn.cursor()

    cur.execute(
        f"CREATE OR REPLACE TABLE {fqn}.DEMO_CLOTHING_ALL AS "
        f"SELECT RELATIVE_PATH AS FILE_PATH, "
        f"SPLIT_PART(RELATIVE_PATH, '/', 1) AS EXPECTED_OUTPUT "
        f"FROM DIRECTORY(@{stage_fqn}) "
        f"WHERE ARRAY_SIZE(SPLIT(RELATIVE_PATH, '/')) = 2"
    )

    cur.execute(
        f"CREATE OR REPLACE TEMPORARY TABLE {fqn}.DEMO_CLOTHING_SPLIT_TEMP AS "
        f"SELECT *, "
        f"ROW_NUMBER() OVER (PARTITION BY EXPECTED_OUTPUT ORDER BY RANDOM(42)) AS _RN, "
        f"COUNT(*) OVER (PARTITION BY EXPECTED_OUTPUT) AS _TOTAL "
        f"FROM {fqn}.DEMO_CLOTHING_ALL"
    )

    cur.execute(
        f"CREATE OR REPLACE TABLE {fqn}.DEMO_CLOTHING_TRAIN AS "
        f"SELECT FILE_PATH, EXPECTED_OUTPUT FROM {fqn}.DEMO_CLOTHING_SPLIT_TEMP "
        f"WHERE _RN <= FLOOR(_TOTAL * {train_pct} / 100)"
    )

    cur.execute(
        f"CREATE OR REPLACE TABLE {fqn}.DEMO_CLOTHING_TEST AS "
        f"SELECT FILE_PATH, EXPECTED_OUTPUT FROM {fqn}.DEMO_CLOTHING_SPLIT_TEMP "
        f"WHERE _RN > FLOOR(_TOTAL * {train_pct} / 100)"
    )

    cur.execute(f"DROP TABLE IF EXISTS {fqn}.DEMO_CLOTHING_ALL")
    cur.execute(f"DROP TABLE IF EXISTS {fqn}.DEMO_CLOTHING_SPLIT_TEMP")

    train_count = cur.execute(
        f"SELECT COUNT(*) FROM {fqn}.DEMO_CLOTHING_TRAIN"
    ).fetchone()[0]
    test_count = cur.execute(
        f"SELECT COUNT(*) FROM {fqn}.DEMO_CLOTHING_TEST"
    ).fetchone()[0]

    logger.info(f"  Train: {fqn}.DEMO_CLOTHING_TRAIN ({train_count} rows)")
    logger.info(f"  Test:  {fqn}.DEMO_CLOTHING_TEST ({test_count} rows)")


def main(
    connection: str,
    database: str,
    schema: str,
    per_class: int = 100,
    train_pct: int = 60,
    seed: int = 42,
) -> None:
    """Download clothing images, upload to stage, and create train/test tables."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        image_dir = download_and_sample(tmp_dir, per_class, seed)

        logger.info(f"Connecting to Snowflake using connection '{connection}'...")
        conn = snowflake.connector.connect(
            connection_name=os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
        )

        try:
            stage_fqn = setup_stage(conn, database, schema)
            upload_images(connection, stage_fqn, image_dir)
            create_tables(conn, database, schema, stage_fqn, train_pct)
            logger.info("Done!")
        finally:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate clothing condition classification data from HuggingFace."
    )
    parser.add_argument("--connection", required=True, help="Snowflake connection name")
    parser.add_argument("--database", required=True, help="Target database")
    parser.add_argument("--schema", required=True, help="Target schema")
    parser.add_argument(
        "--per-class", type=int, default=100,
        help="Images per condition class (default: 100, total = 5x this)",
    )
    parser.add_argument(
        "--train-pct", type=int, default=60,
        help="Training split percentage (default: 60)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main(
        connection=args.connection,
        database=args.database,
        schema=args.schema,
        per_class=args.per_class,
        train_pct=args.train_pct,
        seed=args.seed,
    )
