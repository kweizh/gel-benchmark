import * as fs from "node:fs";
import { createClient } from "gel";
import type { Manifest, Report, ResultEntry } from "./types";
import { ingestAsset } from "./ingestAsset";
import { introspectSchema } from "./schemaIntrospect";

interface CliArgs {
  input: string;
  report: string;
}

function parseArgs(argv: string[]): CliArgs {
  let input: string | undefined;
  let report: string | undefined;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--input") {
      input = argv[++i];
    } else if (arg.startsWith("--input=")) {
      input = arg.slice("--input=".length);
    } else if (arg === "--report") {
      report = argv[++i];
    } else if (arg.startsWith("--report=")) {
      report = arg.slice("--report=".length);
    }
  }

  if (!input || !report) {
    throw new Error(
      "Both --input <manifest.json> and --report <report.json> flags are required",
    );
  }

  return { input, report };
}

function fail(message: string): never {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

async function main() {
  let args: CliArgs;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  }

  let manifestText: string;
  try {
    manifestText = fs.readFileSync(args.input, "utf8");
  } catch (err) {
    fail(
      `Could not read manifest file '${args.input}': ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }

  let manifest: Manifest;
  try {
    manifest = JSON.parse(manifestText);
  } catch (err) {
    fail(
      `Could not parse manifest file '${args.input}' as JSON: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }

  if (
    !manifest ||
    typeof manifest !== "object" ||
    !Array.isArray(manifest.regions) ||
    !Array.isArray(manifest.assets)
  ) {
    fail(
      `Manifest file '${args.input}' must be an object with 'regions' and 'assets' arrays`,
    );
  }

  const client = createClient();

  try {
    for (const key of manifest.regions) {
      await client.query(
        `insert Region { key := <str>$key } unless conflict on .key else (select Region);`,
        { key },
      );
    }

    const sortedAssets = [...manifest.assets].sort((a, b) => a.seq - b.seq);

    const results: ResultEntry[] = [];
    const reasonCounts: Record<string, number> = {};
    let insertedCount = 0;
    let rejectedCount = 0;

    for (const rec of sortedAssets) {
      const outcome = await ingestAsset(client, rec);
      results.push(outcome);
      if (outcome.status === "inserted") {
        insertedCount++;
      } else {
        rejectedCount++;
        const reason = outcome.reason ?? "UNKNOWN";
        reasonCounts[reason] = (reasonCounts[reason] ?? 0) + 1;
      }
    }

    const schema = await introspectSchema(client);

    const report: Report = {
      total: sortedAssets.length,
      inserted: insertedCount,
      rejected: rejectedCount,
      results,
      reason_counts: reasonCounts,
      schema,
    };

    fs.writeFileSync(args.report, `${JSON.stringify(report, null, 2)}\n`, "utf8");

    console.log(`SUMMARY inserted=${insertedCount} rejected=${rejectedCount}`);
    process.exit(rejectedCount > 0 ? 2 : 0);
  } finally {
    await client.close();
  }
}

main().catch((err) => {
  process.stderr.write(`Unexpected error: ${err instanceof Error ? err.stack ?? err.message : String(err)}\n`);
  process.exit(1);
});
