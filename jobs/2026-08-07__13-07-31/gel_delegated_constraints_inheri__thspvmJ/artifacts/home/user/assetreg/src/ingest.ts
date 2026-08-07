import * as fs from "fs";
import * as path from "path";
import { createClient } from "gel";

interface AssetResult {
    seq: number;
    kind: string;
    serial: string;
    status: "inserted" | "rejected";
    reason: string | null;
    error_class: string | null;
}

async function main() {
    let inputPath = "";
    let reportPath = "";

    for (let i = 2; i < process.argv.length; i++) {
        if (process.argv[i] === "--input") {
            inputPath = process.argv[i + 1] || "";
            i++;
        } else if (process.argv[i] === "--report") {
            reportPath = process.argv[i + 1] || "";
            i++;
        }
    }

    if (!inputPath || !reportPath) {
        console.error("Error: Both --input and --report flags are mandatory.");
        process.exit(1);
    }

    let manifestContent = "";
    try {
        manifestContent = fs.readFileSync(inputPath, "utf-8");
    } catch (err: any) {
        console.error(`Error: Cannot read input file at ${inputPath}: ${err.message}`);
        process.exit(1);
    }

    let manifest: any;
    try {
        manifest = JSON.parse(manifestContent);
    } catch (err: any) {
        console.error(`Error: Cannot parse input JSON: ${err.message}`);
        process.exit(1);
    }

    if (!manifest || typeof manifest !== "object" || !Array.isArray(manifest.regions) || !Array.isArray(manifest.assets)) {
        console.error("Error: Invalid manifest structure. Must be a JSON object with 'regions' and 'assets' arrays.");
        process.exit(1);
    }

    const client = createClient();

    try {
        // 1. Ensure all declared regions exist
        for (const regionKey of manifest.regions) {
            if (typeof regionKey === "string") {
                await client.query(
                    `insert Region { key := <str>$key } unless conflict on .key else (select Region);`,
                    { key: regionKey }
                );
            }
        }

        // 2. Process each asset in strictly ascending seq order
        const sortedAssets = [...manifest.assets].sort((a, b) => {
            const seqA = a && typeof a.seq === "number" ? a.seq : 0;
            const seqB = b && typeof b.seq === "number" ? b.seq : 0;
            return seqA - seqB;
        });

        let insertedCount = 0;
        let rejectedCount = 0;
        const results: AssetResult[] = [];
        const reasonCounts: Record<string, number> = {};

        for (const asset of sortedAssets) {
            const seq = asset && typeof asset.seq === "number" ? asset.seq : 0;
            const kind = asset && typeof asset.kind === "string" ? asset.kind : "";
            const serial = asset && typeof asset.serial === "string" ? asset.serial : "";

            try {
                if (kind === "server") {
                    await client.query(`
                        insert ServerAsset {
                            code := <optional str>$code,
                            serial := <optional str>$serial,
                            region := (select Region filter .key = <optional str>$region),
                            slot := <optional int64>$slot,
                            capacity := <optional int64>$capacity,
                            reserved := <optional int64>$reserved,
                            revision := <optional int64>$revision,
                            tags := array_unpack(<optional array<str>>$tags),
                            hostname := <optional str>$hostname
                        }
                    `, {
                        code: asset.code !== undefined ? asset.code : null,
                        serial: asset.serial !== undefined ? asset.serial : null,
                        region: asset.region !== undefined ? asset.region : null,
                        slot: asset.slot !== undefined && asset.slot !== null ? Number(asset.slot) : null,
                        capacity: asset.capacity !== undefined && asset.capacity !== null ? Number(asset.capacity) : null,
                        reserved: asset.reserved !== undefined && asset.reserved !== null ? Number(asset.reserved) : null,
                        revision: asset.revision !== undefined && asset.revision !== null ? Number(asset.revision) : null,
                        tags: asset.tags || [],
                        hostname: asset.hostname !== undefined ? asset.hostname : null
                    });
                } else if (kind === "storage") {
                    await client.query(`
                        insert StorageAsset {
                            code := <optional str>$code,
                            serial := <optional str>$serial,
                            region := (select Region filter .key = <optional str>$region),
                            slot := <optional int64>$slot,
                            capacity := <optional int64>$capacity,
                            reserved := <optional int64>$reserved,
                            revision := <optional int64>$revision,
                            tags := array_unpack(<optional array<str>>$tags),
                            volume_gb := <optional int64>$volume_gb
                        }
                    `, {
                        code: asset.code !== undefined ? asset.code : null,
                        serial: asset.serial !== undefined ? asset.serial : null,
                        region: asset.region !== undefined ? asset.region : null,
                        slot: asset.slot !== undefined && asset.slot !== null ? Number(asset.slot) : null,
                        capacity: asset.capacity !== undefined && asset.capacity !== null ? Number(asset.capacity) : null,
                        reserved: asset.reserved !== undefined && asset.reserved !== null ? Number(asset.reserved) : null,
                        revision: asset.revision !== undefined && asset.revision !== null ? Number(asset.revision) : null,
                        tags: asset.tags || [],
                        volume_gb: asset.volume_gb !== undefined && asset.volume_gb !== null ? Number(asset.volume_gb) : null
                    });
                } else {
                    throw {
                        name: "MissingRequiredError",
                        message: "missing value for required property 'kind'"
                    };
                }

                insertedCount++;
                results.push({
                    seq,
                    kind,
                    serial,
                    status: "inserted",
                    reason: null,
                    error_class: null
                });
            } catch (err: any) {
                rejectedCount++;
                let errorClass = err.name || "Error";
                let reason = "Error";
                if (errorClass === "ConstraintViolationError") {
                    reason = err.message.split("\n")[0];
                } else if (errorClass === "MissingRequiredError") {
                    reason = "MISSING_REQUIRED";
                } else {
                    reason = err.message.split("\n")[0];
                }

                reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
                results.push({
                    seq,
                    kind,
                    serial,
                    status: "rejected",
                    reason,
                    error_class: errorClass
                });
            }
        }

        // 3. Introspect schema info
        const q1 = await client.query(`
            select schema::Constraint { name } filter .abstract and .name like 'default::%';
        `);
        const abstract_constraints = q1.map((item: any) => item.name).sort();

        const q2 = await client.query(`
            select schema::Constraint {
                subject_name := .subject.name,
                pointer_source_name := (.subject[is schema::Pointer]).source.name
            } filter .delegated and (.subject[is schema::Pointer]).source.name like 'default::%';
        `);
        const delegated_pointer_constraints_raw = q2.map((item: any) => {
            const source = item.pointer_source_name || "";
            const typeName = source.startsWith("default::") ? source.substring(9) : source;
            const pointerName = item.subject_name || "";
            return `${typeName}.${pointerName}`;
        });
        const delegated_pointer_constraints = Array.from(new Set(delegated_pointer_constraints_raw)).sort();

        const q3 = await client.query(`
            select schema::Constraint {
                subject_name := .subject.name
            } filter .delegated and .subject[is schema::ObjectType].name like 'default::%';
        `);
        const delegated_object_constraints_raw = q3.map((item: any) => {
            const name = item.subject_name || "";
            return name.startsWith("default::") ? name.substring(9) : name;
        });
        const delegated_object_constraints = Array.from(new Set(delegated_object_constraints_raw)).sort();

        // 4. Build report object
        const report = {
            total: sortedAssets.length,
            inserted: insertedCount,
            rejected: rejectedCount,
            results,
            reason_counts: reasonCounts,
            schema: {
                abstract_constraints,
                delegated_pointer_constraints,
                delegated_object_constraints
            }
        };

        // Write report
        try {
            fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf-8");
        } catch (err: any) {
            console.error(`Error: Cannot write report file at ${reportPath}: ${err.message}`);
            process.exit(1);
        }

        // Output summary line as the last line
        console.log(`SUMMARY inserted=${insertedCount} rejected=${rejectedCount}`);

        // Exit code: 0 if no rejections, 2 if some rejections
        if (rejectedCount > 0) {
            process.exit(2);
        } else {
            process.exit(0);
        }

    } catch (err: any) {
        console.error("Fatal error during ingest process:", err);
        process.exit(1);
    } finally {
        await client.close();
    }
}

main();
