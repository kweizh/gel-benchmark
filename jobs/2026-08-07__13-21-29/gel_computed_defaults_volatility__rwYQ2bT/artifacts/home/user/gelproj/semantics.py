"""Semantics module — defines build_report() coroutine."""

import json
import gel


async def _introspect(client: gel.AsyncIOClient) -> dict:
    """Introspect schema types and pointers."""
    result = {}
    type_names = [
        "default::Sample",
        "default::Measurement",
        "default::Assay",
        "default::Calibration",
        "default::Certificate",
        "default::Batch",
    ]
    short_names = {
        "default::Sample": "Sample",
        "default::Measurement": "Measurement",
        "default::Assay": "Assay",
        "default::Calibration": "Calibration",
        "default::Certificate": "Certificate",
        "default::Batch": "Batch",
    }

    rows = await client.query_json("""
        WITH
          types := (SELECT schema::ObjectType FILTER .name IN array_unpack(<array<str>>$names)),
        SELECT types {
            name,
            abstract,
            pointers: {
                name,
                cardinality,
                required,
                computed := exists .expr,
                target_name := .target.name
            } FILTER .name NOT IN {'id', '__type__'}
        } ORDER BY .name;
    """, names=type_names)

    data = json.loads(rows)
    for t in data:
        sn = short_names[t["name"]]
        pointers = {}
        for p in t["pointers"]:
            pointers[p["name"]] = {
                "cardinality": p["cardinality"],
                "required": p["required"],
                "computed": p["computed"],
                "target": p["target_name"],
            }
        result[sn] = {
            "abstract": t["abstract"],
            "pointers": pointers,
        }

    return result


async def _introspect_link_properties(client: gel.AsyncIOClient) -> dict:
    """Introspect link properties on Batch.samples."""
    rows = await client.query_json("""
        SELECT schema::Pointer {
            name,
            cardinality,
            required,
            target_name := .target.name,
        } FILTER .name = 'position' AND .source.name = 'samples';
    """)
    data = json.loads(rows)
    lp = {}
    if data:
        item = data[0]
        lp = {
            "cardinality": item["cardinality"],
            "required": item["required"],
            "target": item["target_name"],
        }
    return {"Batch.samples": {"position": lp}}


async def _cleanup(client: gel.AsyncIOClient) -> None:
    """Delete all pre-existing objects of the declared types."""
    await client.query("""
        DELETE Calibration;
        DELETE Assay;
        DELETE Certificate;
        DELETE Batch;
        DELETE Sample;
    """)


async def build_report() -> dict:
    """Build the complete report as a dict."""
    client = gel.create_async_client()
    try:
        await _cleanup(client)

        report = {}

        # Introspection (no data dependency)
        report["introspection"] = await _introspect(client)
        report["link_properties"] = await _introspect_link_properties(client)

        # ============================================================
        # Step 1: Create the batch of 4 samples in one statement
        # ============================================================
        batch_result = await client.query_single_json("""
            WITH
                inserted := (
                    FOR data IN {
                        (label := 'Alpha-01', grams := 1.5),
                        (label := 'Bravo-02', grams := 2.25),
                        (label := 'Charlie-03', grams := 3.0),
                        (label := 'Delta-04', grams := 4.75),
                    }
                    UNION (
                        INSERT Sample {
                            label := data.label,
                            grams := data.grams,
                        }
                    )
                ),
            SELECT {
                batch_insert_size := count(inserted),
                distinct_intake_at_in_batch := count(DISTINCT inserted.intake_at),
                distinct_intake_ref_in_batch := count(DISTINCT inserted.intake_ref),
            };
        """)
        batch_info = json.loads(batch_result)

        # ============================================================
        # Step 2: Read label_key for Delta-04 BEFORE update
        # ============================================================
        before_label_key = await client.query_single_json("""
            SELECT Sample { label_key } FILTER .label = 'Delta-04';
        """)
        before_label_key_data = json.loads(before_label_key)

        # ============================================================
        # Step 3: Read Alpha-01 computed values BEFORE measurements exist
        # ============================================================
        alpha_before = await client.query_single_json("""
            SELECT Sample {
                assay_count,
                measurement_count,
                total_value,
            } FILTER .label = 'Alpha-01';
        """)
        alpha_before_data = json.loads(alpha_before)

        # ============================================================
        # Step 4: Update Delta-04 -> Zulu-99
        # ============================================================
        # Get Delta-04's intake_at and intake_ref before update
        before_update = await client.query_single_json("""
            SELECT Sample {
                intake_at,
                intake_ref,
            } FILTER .label = 'Delta-04';
        """)
        before_data = json.loads(before_update)

        await client.query("""
            UPDATE Sample
            FILTER .label = 'Delta-04'
            SET {
                label := 'Zulu-99',
                grams := 9.0
            };
        """)

        # Get the same sample's intake_at and intake_ref after update
        after_update = await client.query_single_json("""
            SELECT Sample {
                intake_at,
                intake_ref,
            } FILTER .label = 'Zulu-99';
        """)
        after_data = json.loads(after_update)

        # Read label_key for Zulu-99 AFTER update
        after_label_key = await client.query_single_json("""
            SELECT Sample { label_key } FILTER .label = 'Zulu-99';
        """)
        after_label_key_data = json.loads(after_label_key)

        # ============================================================
        # Step 5: Try to update intake_at (read-only) and capture the error
        # ============================================================
        readonly_rejected = False
        readonly_error_class = ""
        readonly_error_message = ""
        try:
            await client.query("""
                UPDATE Sample
                FILTER .label = 'Zulu-99'
                SET {
                    intake_at := std::datetime_current()
                };
            """)
        except Exception as e:
            readonly_rejected = True
            readonly_error_class = type(e).__name__
            readonly_error_message = str(e)

        # ============================================================
        # Step 6: Create Echo-05 in a separate statement
        # ============================================================
        await client.query("""
            INSERT Sample {
                label := 'Echo-05',
                grams := 5.5
            };
        """)

        # Check Echo-05's intake_at vs batch's
        late_vs_batch = await client.query_single_json("""
            WITH
                batch_sample := (SELECT Sample FILTER .label = 'Alpha-01'),
                late_sample := (SELECT Sample FILTER .label = 'Echo-05'),
            SELECT {
                late_not_before := late_sample.intake_at >= batch_sample.intake_at,
            };
        """)
        late_info = json.loads(late_vs_batch)

        # ============================================================
        # Step 7: Create measurements (Assays and Calibrations)
        # ============================================================
        await client.query("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
                bravo := (SELECT Sample FILTER .label = 'Bravo-02'),
                charlie := (SELECT Sample FILTER .label = 'Charlie-03'),
            INSERT Assay {
                code := 'A1',
                sample := alpha,
                value := 10.0
            };
        """)
        await client.query("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
            INSERT Assay {
                code := 'A2',
                sample := alpha,
                value := 2.5
            };
        """)
        await client.query("""
            WITH
                bravo := (SELECT Sample FILTER .label = 'Bravo-02'),
            INSERT Assay {
                code := 'A3',
                sample := bravo,
                value := 7.25
            };
        """)
        await client.query("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
            INSERT Calibration {
                code := 'C1',
                sample := alpha,
                bias := 0.5
            };
        """)
        await client.query("""
            WITH
                charlie := (SELECT Sample FILTER .label = 'Charlie-03'),
            INSERT Calibration {
                code := 'C2',
                sample := charlie,
                bias := -1.25
            };
        """)

        # Read Alpha-01 computed values AFTER measurements exist
        alpha_after = await client.query_single_json("""
            SELECT Sample {
                assay_count,
                measurement_count,
                total_value,
            } FILTER .label = 'Alpha-01';
        """)
        alpha_after_data = json.loads(alpha_after)

        # ============================================================
        # Step 8: Create Certificate and Batch
        # ============================================================
        await client.query("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
            INSERT Certificate {
                serial := 'CERT-ALPHA',
                sample := alpha,
            };
        """)
        await client.query("""
            INSERT Batch {
                code := 'BATCH-1',
            };
        """)
        await client.query("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
                batch := (SELECT Batch FILTER .code = 'BATCH-1'),
            UPDATE batch
            SET {
                samples += alpha { @position := 1 }
            };
        """)
        await client.query("""
            WITH
                bravo := (SELECT Sample FILTER .label = 'Bravo-02'),
                batch := (SELECT Batch FILTER .code = 'BATCH-1'),
            UPDATE batch
            SET {
                samples += bravo { @position := 2 }
            };
        """)
        await client.query("""
            WITH
                charlie := (SELECT Sample FILTER .label = 'Charlie-03'),
                batch := (SELECT Batch FILTER .code = 'BATCH-1'),
            UPDATE batch
            SET {
                samples += charlie { @position := 3 }
            };
        """)

        # ============================================================
        # Step 9: Age checks
        # ============================================================
        age_check = await client.query_single_json("""
            SELECT {
                all_non_negative := all(
                    (SELECT Sample.age) >= <duration>'0s'
                ),
            };
        """)
        age_check_data = json.loads(age_check)

        age_match = await client.query_single_json("""
            WITH
                alpha := (SELECT Sample FILTER .label = 'Alpha-01'),
                echo := (SELECT Sample FILTER .label = 'Echo-05'),
            SELECT {
                matches := (
                    (echo.age - alpha.age) = (alpha.intake_at - echo.intake_at)
                ),
            };
        """)
        age_match_data = json.loads(age_match)

        # ============================================================
        # Step 10: Build samples and batch sections
        # ============================================================
        samples_rows = await client.query_json("""
            SELECT Sample {
                label,
                label_key,
                assay_count,
                measurement_count,
                total_value,
                has_certificate := exists(.certificate),
            } ORDER BY .label;
        """)
        samples_data = json.loads(samples_rows)

        # Get batch codes per sample
        batch_codes_rows = await client.query_json("""
            SELECT Sample {
                label,
                batch_codes := array_agg(.batches.code),
            } ORDER BY .label;
        """)
        batch_codes_data = json.loads(batch_codes_rows)
        batch_codes_map = {item["label"]: sorted(item["batch_codes"]) for item in batch_codes_data}

        for s in samples_data:
            s["batch_codes"] = batch_codes_map.get(s["label"], [])

        batch_rows = await client.query_json("""
            SELECT Batch {
                code,
                sample_count,
                members := (
                    SELECT .samples {
                        label,
                        @position,
                    } ORDER BY @position
                ),
            };
        """)
        batch_data_raw = json.loads(batch_rows)
        batch_data = batch_data_raw[0] if batch_data_raw else {}

        # ============================================================
        # Step 11: Volatility probes (must not leave residue)
        # ============================================================
        await client.query(
            'configure current database set allow_bare_ddl := "AlwaysAllow";'
        )

        # Probe 1: schema-level computed with volatile expression
        schema_rejected = False
        schema_error_class = ""
        schema_error_message = ""
        try:
            await client.query("""
                ALTER TYPE Sample {
                    CREATE PROPERTY volatile_probe := std::datetime_current();
                };
            """)
        except Exception as e:
            schema_rejected = True
            schema_error_class = type(e).__name__
            schema_error_message = str(e)

        # Probe 2: cartesian product with volatile expression
        cartesian_rejected = False
        cartesian_error_class = ""
        cartesian_error_message = ""
        try:
            await client.query("""
                SELECT (Sample, std::datetime_current());
            """)
        except Exception as e:
            cartesian_rejected = True
            cartesian_error_class = type(e).__name__
            cartesian_error_message = str(e)

        # Restore bare DDL setting
        await client.query(
            'configure current database set allow_bare_ddl := "NeverAllow";'
        )

        # Verify schema is unchanged
        type_count = await client.query_single_json("""
            SELECT count(
                schema::ObjectType FILTER .name LIKE 'default::%'
            );
        """)
        type_count_data = json.loads(type_count)

        probe_count = await client.query_single_json("""
            SELECT count(
                schema::Pointer FILTER .name = 'volatile_probe'
                AND .source.name = 'default::Sample'
            );
        """)
        probe_count_data = json.loads(probe_count)

        schema_unchanged = (type_count_data == 6 and probe_count_data == 0)

        # ============================================================
        # Assemble report
        # ============================================================
        report["defaults"] = {
            "batch_insert_size": batch_info["batch_insert_size"],
            "distinct_intake_at_in_batch": batch_info["distinct_intake_at_in_batch"],
            "distinct_intake_ref_in_batch": batch_info["distinct_intake_ref_in_batch"],
            "intake_at_unchanged_after_update": before_data["intake_at"] == after_data["intake_at"],
            "intake_ref_unchanged_after_update": before_data["intake_ref"] == after_data["intake_ref"],
            "late_intake_at_not_before_batch": late_info["late_not_before"],
            "readonly_update_rejected": {
                "rejected": readonly_rejected,
                "error_class": readonly_error_class,
                "error_message": readonly_error_message,
            },
        }

        report["computed"] = {
            "label_key_before_update": before_label_key_data["label_key"],
            "label_key_after_update": after_label_key_data["label_key"],
            "alpha_assay_count_before": alpha_before_data["assay_count"],
            "alpha_measurement_count_before": alpha_before_data["measurement_count"],
            "alpha_total_value_before": alpha_before_data["total_value"],
            "alpha_assay_count_after": alpha_after_data["assay_count"],
            "alpha_measurement_count_after": alpha_after_data["measurement_count"],
            "alpha_total_value_after": alpha_after_data["total_value"],
            "age_non_negative": age_check_data["all_non_negative"],
            "age_difference_matches_intake_difference": age_match_data["matches"],
        }

        report["volatility"] = {
            "schema_computed_volatile": {
                "rejected": schema_rejected,
                "error_class": schema_error_class,
                "error_message": schema_error_message,
            },
            "cartesian_volatile": {
                "rejected": cartesian_rejected,
                "error_class": cartesian_error_class,
                "error_message": cartesian_error_message,
            },
            "schema_unchanged_after_probe": schema_unchanged,
        }

        report["samples"] = samples_data
        report["batch"] = batch_data

        return report
    finally:
        await client.aclose()
