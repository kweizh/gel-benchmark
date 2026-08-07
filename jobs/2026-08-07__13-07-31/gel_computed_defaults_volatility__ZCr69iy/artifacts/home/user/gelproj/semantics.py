import asyncio
import datetime
import gel

async def build_report() -> dict:
    client = gel.create_async_client()
    try:
        # 1. Delete pre-existing objects of these types first
        # Batch, Certificate, Measurement (includes Assay, Calibration), Sample
        await client.execute("delete Batch;")
        await client.execute("delete Certificate;")
        await client.execute("delete Measurement;")
        await client.execute("delete Sample;")

        # 2. Four Samples created by one single EdgeQL statement
        insert_batch_query = """
        for data in {
            (l := 'Alpha-01', g := 1.5),
            (l := 'Bravo-02', g := 2.25),
            (l := 'Charlie-03', g := 3.0),
            (l := 'Delta-04', g := 4.75)
        } union (
            insert Sample {
                label := data.l,
                grams := data.g
            }
        );
        """
        await client.execute(insert_batch_query)

        # Query the inserted batch samples to record defaults metrics
        samples_batch = await client.query("""
            select Sample {
                label,
                intake_at,
                intake_ref
            } filter .label in {'Alpha-01', 'Bravo-02', 'Charlie-03', 'Delta-04'};
        """)
        
        batch_insert_size = len(samples_batch)
        distinct_intake_at_in_batch = len(set(s.intake_at for s in samples_batch))
        distinct_intake_ref_in_batch = len(set(s.intake_ref for s in samples_batch))

        # Query Alpha-01 before any measurements exist
        alpha_before = await client.query_required_single("""
            select Sample {
                assay_count,
                measurement_count,
                total_value
            } filter .label = 'Alpha-01';
        """)
        alpha_assay_count_before = int(alpha_before.assay_count)
        alpha_measurement_count_before = int(alpha_before.measurement_count)
        alpha_total_value_before = float(alpha_before.total_value)

        # 3. One more Sample created by a later, separate statement
        await asyncio.sleep(0.1)
        await client.execute("insert Sample { label := 'Echo-05', grams := 5.5 };")

        # Query Echo-05 and Alpha-01 to check late_intake_at_not_before_batch
        echo_sample = await client.query_required_single("""
            select Sample { intake_at } filter .label = 'Echo-05';
        """)
        alpha_sample = await client.query_required_single("""
            select Sample { intake_at } filter .label = 'Alpha-01';
        """)
        late_intake_at_not_before_batch = bool(echo_sample.intake_at >= alpha_sample.intake_at)

        # 4. Populate assays
        await client.execute("insert Assay { code := 'A1', sample := (select Sample filter .label = 'Alpha-01'), value := 10.0 };")
        await client.execute("insert Assay { code := 'A2', sample := (select Sample filter .label = 'Alpha-01'), value := 2.5 };")
        await client.execute("insert Assay { code := 'A3', sample := (select Sample filter .label = 'Bravo-02'), value := 7.25 };")

        # 5. Populate calibrations
        await client.execute("insert Calibration { code := 'C1', sample := (select Sample filter .label = 'Alpha-01'), bias := 0.5 };")
        await client.execute("insert Calibration { code := 'C2', sample := (select Sample filter .label = 'Charlie-03'), bias := -1.25 };")

        # 6. Populate certificate
        await client.execute("insert Certificate { serial := 'CERT-ALPHA', sample := (select Sample filter .label = 'Alpha-01') };")

        # 7. Populate batch
        await client.execute("""
            insert Batch {
                code := 'BATCH-1',
                samples := (
                    select Sample {
                        @position := 1 if .label = 'Alpha-01' else
                                     2 if .label = 'Bravo-02' else
                                     3 if .label = 'Charlie-03' else
                                     <int64>{}
                    } filter .label in {'Alpha-01', 'Bravo-02', 'Charlie-03'}
                )
            };
        """)

        # Query Delta-04 before update
        delta_before = await client.query_required_single("""
            select Sample {
                label_key,
                intake_at,
                intake_ref
            } filter .label = 'Delta-04';
        """)
        label_key_before_update = delta_before.label_key
        intake_at_before = delta_before.intake_at
        intake_ref_before = delta_before.intake_ref

        # Update Delta-04 to Zulu-99 and grams 9.0
        await client.execute("update Sample filter .label = 'Delta-04' set { label := 'Zulu-99', grams := 9.0 };")

        # Query Zulu-99 after update
        zulu_after = await client.query_required_single("""
            select Sample {
                label_key,
                intake_at,
                intake_ref
            } filter .label = 'Zulu-99';
        """)
        label_key_after_update = zulu_after.label_key
        intake_at_after = zulu_after.intake_at
        intake_ref_after = zulu_after.intake_ref

        intake_at_unchanged_after_update = bool(intake_at_before == intake_at_after)
        intake_ref_unchanged_after_update = bool(intake_ref_before == intake_ref_after)

        # Read Alpha-01 after measurements exist
        alpha_after = await client.query_required_single("""
            select Sample {
                assay_count,
                measurement_count,
                total_value
            } filter .label = 'Alpha-01';
        """)
        alpha_assay_count_after = int(alpha_after.assay_count)
        alpha_measurement_count_after = int(alpha_after.measurement_count)
        alpha_total_value_after = float(alpha_after.total_value)

        # Try to assign a new value to intake_at of an existing sample
        readonly_update_rejected = {"rejected": False, "error_class": "", "error_message": ""}
        try:
            await client.execute("update Sample filter .label = 'Alpha-01' set { intake_at := datetime_of_statement() };")
        except Exception as e:
            readonly_update_rejected["rejected"] = True
            readonly_update_rejected["error_class"] = e.__class__.__name__
            readonly_update_rejected["error_message"] = str(e)

        # Check if age of samples is non-negative
        all_samples_age = await client.query("select Sample { label, age };")
        age_non_negative = all_samples_age and all(s.age >= datetime.timedelta(0) for s in all_samples_age)

        # age_difference_matches_intake_difference:
        # Read within one single statement the derived age of Alpha-01 and of Echo-05
        stmt_samples = await client.query("""
            select Sample {
                label,
                age,
                intake_at
            } filter .label in {'Alpha-01', 'Echo-05'};
        """)
        alpha_stmt = next(s for s in stmt_samples if s.label == 'Alpha-01')
        echo_stmt = next(s for s in stmt_samples if s.label == 'Echo-05')
        age_diff = alpha_stmt.age - echo_stmt.age
        intake_diff = echo_stmt.intake_at - alpha_stmt.intake_at
        age_difference_matches_intake_difference = bool(age_diff == intake_diff)

        # Volatility probes
        # 1. schema_computed_volatile
        schema_computed_volatile = {"rejected": False, "error_class": "", "error_message": ""}
        try:
            # Enable bare ddl temporarily
            await client.execute("CONFIGURE CURRENT BRANCH SET allow_bare_ddl := 'AlwaysAllow';")
            try:
                await client.execute("CREATE TYPE default::TestVolatile { CREATE PROPERTY rand_val := (std::uuid_generate_v4()) };")
            except Exception as e:
                schema_computed_volatile["rejected"] = True
                schema_computed_volatile["error_class"] = e.__class__.__name__
                schema_computed_volatile["error_message"] = str(e)
            finally:
                await client.execute("CONFIGURE CURRENT BRANCH SET allow_bare_ddl := 'NeverAllow';")
        except Exception as e:
            schema_computed_volatile["rejected"] = True
            schema_computed_volatile["error_class"] = e.__class__.__name__
            schema_computed_volatile["error_message"] = str(e)

        # 2. cartesian_volatile
        cartesian_volatile = {"rejected": False, "error_class": "", "error_message": ""}
        try:
            await client.query("SELECT {1, 2} + random();")
        except Exception as e:
            cartesian_volatile["rejected"] = True
            cartesian_volatile["error_class"] = e.__class__.__name__
            cartesian_volatile["error_message"] = str(e)

        # Check if schema is unchanged after probes
        types_res = await client.query("select schema::ObjectType { name } filter .name like 'default::%';")
        types_names = {t.name for t in types_res}
        expected_types = {
            'default::Sample',
            'default::Measurement',
            'default::Assay',
            'default::Calibration',
            'default::Certificate',
            'default::Batch'
        }
        schema_unchanged_after_probe = bool(types_names == expected_types)

        # Introspection and link_properties
        schema_info = await client.query("""
            select schema::ObjectType {
                name,
                is_abstract,
                pointers: {
                    name,
                    cardinality,
                    required,
                    computed := exists .expr,
                    target: { name }
                },
                links: {
                    name,
                    properties: {
                        name,
                        cardinality,
                        required,
                        target: { name }
                    }
                }
            } filter .name in {
                'default::Sample',
                'default::Measurement',
                'default::Assay',
                'default::Calibration',
                'default::Certificate',
                'default::Batch'
            };
        """)

        def format_cardinality(card) -> str:
            c = str(card).lower()
            if 'many' in c:
                return 'Many'
            return 'One'

        introspection = {}
        link_properties = {}

        for obj in schema_info:
            short_name = obj.name.split("::")[-1]
            pointers_dict = {}
            for p in obj.pointers:
                if p.name in ("id", "__type__"):
                    continue
                pointers_dict[p.name] = {
                    "cardinality": format_cardinality(p.cardinality),
                    "required": p.required,
                    "computed": p.computed,
                    "target": p.target.name if p.target else ""
                }
            introspection[short_name] = {
                "abstract": obj.is_abstract,
                "pointers": pointers_dict
            }

            if obj.name == 'default::Batch':
                for link in obj.links:
                    if link.name == 'samples':
                        props = {}
                        for prop in link.properties:
                            if prop.name not in ("source", "target"):
                                props[prop.name] = {
                                    "cardinality": format_cardinality(prop.cardinality),
                                    "required": prop.required,
                                    "target": prop.target.name if prop.target else ""
                                }
                        link_properties["Batch.samples"] = props

        # Samples list
        samples_data = await client.query("""
            select Sample {
                label,
                label_key,
                assay_count,
                measurement_count,
                total_value,
                has_certificate := exists .certificate,
                batch_codes := (select .batches.code)
            } order by .label asc;
        """)

        samples_list = []
        for s in samples_data:
            samples_list.append({
                "label": s.label,
                "label_key": s.label_key,
                "assay_count": int(s.assay_count),
                "measurement_count": int(s.measurement_count),
                "total_value": float(s.total_value),
                "has_certificate": s.has_certificate,
                "batch_codes": sorted(list(s.batch_codes))
            })

        # Batch info
        batch_data = await client.query_required_single("""
            select Batch {
                code,
                sample_count,
                samples: {
                    label,
                    position := @position
                }
            } filter .code = 'BATCH-1';
        """)

        members = []
        for s in batch_data.samples:
            members.append({
                "label": s.label,
                "position": int(s.position)
            })
        members.sort(key=lambda x: x["position"])

        batch_info = {
            "code": batch_data.code,
            "sample_count": int(batch_data.sample_count),
            "members": members
        }

        # Build final report dict
        report = {
            "introspection": introspection,
            "link_properties": link_properties,
            "defaults": {
                "batch_insert_size": int(batch_insert_size),
                "distinct_intake_at_in_batch": int(distinct_intake_at_in_batch),
                "distinct_intake_ref_in_batch": int(distinct_intake_ref_in_batch),
                "intake_at_unchanged_after_update": intake_at_unchanged_after_update,
                "intake_ref_unchanged_after_update": intake_ref_unchanged_after_update,
                "late_intake_at_not_before_batch": late_intake_at_not_before_batch,
                "readonly_update_rejected": readonly_update_rejected
            },
            "computed": {
                "label_key_before_update": label_key_before_update,
                "label_key_after_update": label_key_after_update,
                "alpha_assay_count_before": alpha_assay_count_before,
                "alpha_measurement_count_before": alpha_measurement_count_before,
                "alpha_total_value_before": alpha_total_value_before,
                "alpha_assay_count_after": alpha_assay_count_after,
                "alpha_measurement_count_after": alpha_measurement_count_after,
                "alpha_total_value_after": alpha_total_value_after,
                "age_non_negative": age_non_negative,
                "age_difference_matches_intake_difference": age_difference_matches_intake_difference
            },
            "volatility": {
                "schema_computed_volatile": schema_computed_volatile,
                "cartesian_volatile": cartesian_volatile,
                "schema_unchanged_after_probe": schema_unchanged_after_probe
            },
            "samples": samples_list,
            "batch": batch_info
        }

        return report

    finally:
        await client.aclose()
