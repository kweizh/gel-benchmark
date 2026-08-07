import * as fs from "fs";
import * as path from "path";
import { createClient } from "gel";
import {
  GelError,
  ConstraintViolationError,
  MissingRequiredError,
} from "gel/dist/errors/index";

interface ManifestRegion {
  // just a string
}

interface ManifestAsset {
  seq: number;
  kind: string;
  code?: string;
  serial?: string;
  region?: string;
  slot?: number;
  capacity?: number;
  reserved?: number;
  revision?: number;
  tags?: string[];
  hostname?: string;
  volume_gb?: number;
}

interface Manifest {
  regions: string[];
  assets: ManifestAsset[];
}

interface ResultEntry {
  seq: number;
  kind: string;
  serial: string | null;
  status: "inserted" | "rejected";
  reason: string | null;
  error_class: string | null;
}

interface Report {
  total: number;
  inserted: number;
  rejected: number;
  results: ResultEntry[];
  reason_counts: Record<string, number>;
  schema: {
    abstract_constraints: string[];
    delegated_pointer_constraints: string[];
    delegated_object_constraints: string[];
  };
}

function parseArgs(argv: string[]): { input: string; report: string } {
  let input: string | undefined;
  let report: string | undefined;

  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--input" && i + 1 < argv.length) {
      input = argv[i + 1];
      i++;
    } else if (argv[i] === "--report" && i + 1 < argv.length) {
      report = argv[i + 1];
      i++;
    }
  }

  if (!input || !report) {
    console.error("Usage: run-ingest.sh --input <manifest.json> --report <report.json>");
    process.exit(1);
  }

  return { input, report };
}

function readManifest(filePath: string): Manifest {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") {
      throw new Error("Invalid manifest: not an object");
    }
    if (!Array.isArray(data.regions)) {
      throw new Error("Invalid manifest: regions must be an array");
    }
    if (!Array.isArray(data.assets)) {
      throw new Error("Invalid manifest: assets must be an array");
    }
    return data as Manifest;
  } catch (err: any) {
    if (err instanceof SyntaxError) {
      console.error(`Failed to parse manifest: ${err.message}`);
    } else if (err.message && err.message.startsWith("Invalid manifest")) {
      console.error(err.message);
    } else {
      console.error(`Failed to read manifest: ${err.message}`);
    }
    process.exit(1);
  }
}

function extractErrorInfo(err: unknown): { reason: string; error_class: string } | null {
  if (err instanceof ConstraintViolationError) {
    // _message contains just the error message text without the pretty-printed details
    const msg = (err as any)._message || "";
    // The message format is typically "<constraint message>\n"
    const reason = msg.split("\n")[0].trim();
    return { reason, error_class: "ConstraintViolationError" };
  }
  if (err instanceof MissingRequiredError) {
    return { reason: "MISSING_REQUIRED", error_class: "MissingRequiredError" };
  }
  if (err instanceof GelError) {
    // Check if it's an IntegrityError that's not specifically ConstraintViolationError or MissingRequiredError
    const msg = (err as any)._message || "";
    const reason = msg.split("\n")[0].trim();
    // Check the error type name
    const name = err.constructor.name;
    if (name === "ConstraintViolationError" || err instanceof ConstraintViolationError) {
      return { reason, error_class: "ConstraintViolationError" };
    }
    if (name === "MissingRequiredError" || err instanceof MissingRequiredError) {
      return { reason: "MISSING_REQUIRED", error_class: "MissingRequiredError" };
    }
    // For any other GelError, try to classify
    return { reason, error_class: name };
  }
  return null;
}

async function introspectSchema(client: any): Promise<Report["schema"]> {
  // Get abstract constraints in module default
  const abstractConstraints = await client.query(`
    SELECT schema::Constraint {
      name
    }
    FILTER .abstract = true
      AND .name LIKE 'default::%'
    ORDER BY .name
  `);

  // Get delegated pointer constraints
  // A pointer constraint is delegated if the constraint has delegated=true
  // and is attached to a pointer (property or link)
  const delegatedPointerConstraints = await client.query(`
    WITH
      ptrs := (
        SELECT schema::Pointer {
          name,
          constraints: {
            name,
            delegated
          }
        }
        FILTER .name LIKE 'default::%'
          AND EXISTS .constraints
      ),
      delegated := (
        SELECT ptrs.constraints
        FILTER .delegated = true
      )
    SELECT DISTINCT (
      # Get the type name (without module prefix) and pointer name
      re_replace(
        re_replace((SELECT ptrs FILTER .constraints = delegated).name,
          '^default::', ''),
        '::', '.'
      )
    )
    ORDER BY . ASC
  `);

  // Get delegated object constraints
  const delegatedObjectConstraints = await client.query(`
    SELECT schema::ObjectType {
      name
    }
    FILTER .name LIKE 'default::%'
      AND EXISTS (
        .constraints FILTER .delegated = true
      )
    ORDER BY .name
  `);

  return {
    abstract_constraints: abstractConstraints.map((c: any) => c.name).sort(),
    delegated_pointer_constraints: delegatedPointerConstraints.sort(),
    delegated_object_constraints: delegatedObjectConstraints
      .map((t: any) => t.name.replace(/^default::/, ""))
      .sort(),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const manifest = readManifest(args.input);

  const client = createClient();

  try {
    // First, ensure all regions exist
    for (const regionKey of manifest.regions) {
      await client.execute(
        `INSERT Region { key := <str>$key } UNLESS CONFLICT ON .key ELSE Region`,
        { key: regionKey }
      );
    }

    // Sort assets by seq
    const sortedAssets = [...manifest.assets].sort((a, b) => a.seq - b.seq);

    const results: ResultEntry[] = [];
    const reasonCounts: Record<string, number> = {};
    let inserted = 0;
    let rejected = 0;

    for (const asset of sortedAssets) {
      const typeName = asset.kind === "server" ? "ServerAsset" : "StorageAsset";
      const serial = asset.serial ?? null;

      try {
        // Build the insert query
        const query = `
          WITH
            r := (SELECT Region FILTER .key = <str>$region_key LIMIT 1),
          INSERT ${typeName} {
            code := <str>$code,
            serial := <str>$serial,
            region := r,
            slot := <int64>$slot,
            capacity := <int64>$capacity,
            reserved := <int64>$reserved,
            revision := <int64>$revision,
            ${asset.kind === "server" ? "hostname := <str>$hostname," : ""}
            ${asset.kind === "storage" ? "volume_gb := <int64>$volume_gb," : ""}
            ${asset.tags && asset.tags.length > 0 ? "tags := <array<str>>$tags" : ""}
          }
        `;

        await client.execute(query, {
          region_key: asset.region,
          code: asset.code,
          serial: asset.serial,
          slot: asset.slot,
          capacity: asset.capacity,
          reserved: asset.reserved,
          revision: asset.revision,
          hostname: asset.kind === "server" ? asset.hostname : undefined,
          volume_gb: asset.kind === "storage" ? asset.volume_gb : undefined,
          tags: asset.tags ?? [],
        });

        results.push({
          seq: asset.seq,
          kind: asset.kind,
          serial,
          status: "inserted",
          reason: null,
          error_class: null,
        });
        inserted++;
      } catch (err: unknown) {
        const errorInfo = extractErrorInfo(err);
        if (errorInfo) {
          results.push({
            seq: asset.seq,
            kind: asset.kind,
            serial,
            status: "rejected",
            reason: errorInfo.reason,
            error_class: errorInfo.error_class,
          });
          reasonCounts[errorInfo.reason] = (reasonCounts[errorInfo.reason] || 0) + 1;
          rejected++;
        } else {
          // Unexpected error - rethrow
          throw err;
        }
      }
    }

    // Introspect schema
    const schema = await introspectSchema(client);

    const report: Report = {
      total: manifest.assets.length,
      inserted,
      rejected,
      results,
      reason_counts: reasonCounts,
      schema,
    };

    fs.writeFileSync(args.report, JSON.stringify(report, null, 2), "utf-8");

    console.log(`SUMMARY inserted=${inserted} rejected=${rejected}`);

    process.exit(rejected > 0 ? 2 : 0);
  } finally {
    await client.close();
  }
}

main().catch((err) => {
  console.error(`Fatal error: ${err.message}`);
  process.exit(1);
});
