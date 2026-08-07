import * as fs from "node:fs";
import { createClient } from "gel";
import { validateBatch } from "./validate";
import {
  CliError,
  ExistingProduct,
  Outcome,
  RawRecordShape,
  RecordResult,
  SuccessOutput,
} from "./types";

const READ_EXISTING_QUERY = `
select Product {
  sku,
  name,
  price_cents,
  stock,
  category_name := .category.name,
  tag_labels := .tags.label,
}
filter .sku in array_unpack(<array<str>>$skus)
`;

const SYNC_BATCH_QUERY = `
with
  raw := <json>$batch,
  items := json_array_unpack(raw),
  category_names := distinct (for item in items union (<str>item['category'])),
  tag_labels := distinct (
    for item in items union (
      for t in json_array_unpack(json_get(item, 'tags') ?? to_json('[]')) union (<str>t)
    )
  ),
  ensured_categories := (
    for name in category_names union (
      insert Category { name := name } unless conflict on .name else (select Category)
    )
  ),
  ensured_tags := (
    for label in tag_labels union (
      insert Tag { label := label } unless conflict on .label else (select Tag)
    )
  ),
  products := (
    for item in items union (
      with
        sku := <str>item['sku'],
        nm := <str>item['name'],
        price := <int64>item['price_cents'],
        stk := <int64>item['stock'],
        cat_name := <str>item['category'],
        wanted_tag_labels := distinct (for t in json_array_unpack(json_get(item, 'tags') ?? to_json('[]')) union (<str>t)),
        cat := assert_single((select ensured_categories filter .name = cat_name)),
        wanted_tags := (select ensured_tags filter .label in wanted_tag_labels),
      select (
        insert Product {
          sku := sku,
          name := nm,
          price_cents := price,
          stock := stk,
          category := cat,
          tags := wanted_tags,
        }
        unless conflict on .sku
        else (
          update Product set {
            name := nm,
            price_cents := price,
            stock := stk,
            category := cat,
            tags := wanted_tags,
          }
        )
      )
    )
  )
select count(products)
`;

function parseArgs(argv: string[]): string | null {
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--input") {
      return argv[i + 1] ?? null;
    }
    if (arg.startsWith("--input=")) {
      return arg.slice("--input=".length);
    }
  }
  return null;
}

function readBatchFile(inputPath: string | null): unknown {
  if (!inputPath) {
    throw new CliError(
      2,
      "input_unreadable",
      "Missing required --input <path> argument.",
    );
  }

  let content: string;
  try {
    content = fs.readFileSync(inputPath, "utf8");
  } catch (err) {
    throw new CliError(
      2,
      "input_unreadable",
      `Could not read input file "${inputPath}": ${(err as Error).message}`,
    );
  }

  try {
    return JSON.parse(content);
  } catch (err) {
    throw new CliError(
      2,
      "input_unreadable",
      `Input file "${inputPath}" does not contain valid JSON: ${(err as Error).message}`,
    );
  }
}

function dedupeSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort();
}

function computeOutcome(
  existing: ExistingProduct | undefined,
  rec: RawRecordShape,
  dedupedTags: string[],
): Outcome {
  if (!existing) {
    return "inserted";
  }
  const existingTagsSet = new Set(existing.tag_labels);
  const wantedTagsSet = new Set(dedupedTags);
  const tagsEqual =
    existingTagsSet.size === wantedTagsSet.size &&
    [...wantedTagsSet].every((t) => existingTagsSet.has(t));
  const changed =
    existing.name !== rec.name ||
    existing.price_cents !== rec.price_cents ||
    existing.stock !== rec.stock ||
    existing.category_name !== rec.category ||
    !tagsEqual;
  return changed ? "updated" : "unchanged";
}

async function runSync(records: RawRecordShape[]): Promise<SuccessOutput> {
  const dedupedTagsBySku = new Map<string, string[]>();
  for (const rec of records) {
    dedupedTagsBySku.set(rec.sku, dedupeSorted(rec.tags));
  }

  if (records.length === 0) {
    return {
      ok: true,
      total: 0,
      inserted: 0,
      updated: 0,
      unchanged: 0,
      results: [],
    };
  }

  const client = createClient();
  let outcomes: Map<string, Outcome>;
  try {
    outcomes = await client.transaction(async (tx) => {
      const skus = records.map((r) => r.sku);
      const existingRows = await tx.query<ExistingProduct>(
        READ_EXISTING_QUERY,
        { skus },
      );
      const existingBySku = new Map<string, ExistingProduct>();
      for (const row of existingRows) {
        existingBySku.set(row.sku, row);
      }

      const result = new Map<string, Outcome>();
      for (const rec of records) {
        const existing = existingBySku.get(rec.sku);
        const deduped = dedupedTagsBySku.get(rec.sku)!;
        result.set(rec.sku, computeOutcome(existing, rec, deduped));
      }

      await tx.execute(SYNC_BATCH_QUERY, { batch: records });

      return result;
    });
  } catch (err) {
    throw new CliError(
      5,
      "db_error",
      `The database rejected the batch: ${(err as Error).message}`,
    );
  } finally {
    await client.close();
  }

  let inserted = 0;
  let updated = 0;
  let unchanged = 0;
  const results: RecordResult[] = records.map((rec) => {
    const outcome = outcomes.get(rec.sku)!;
    if (outcome === "inserted") inserted++;
    else if (outcome === "updated") updated++;
    else unchanged++;
    return {
      sku: rec.sku,
      outcome,
      category: rec.category,
      tags: dedupedTagsBySku.get(rec.sku)!,
    };
  });
  results.sort((a, b) => (a.sku < b.sku ? -1 : a.sku > b.sku ? 1 : 0));

  return {
    ok: true,
    total: records.length,
    inserted,
    updated,
    unchanged,
    results,
  };
}

async function main(): Promise<number> {
  try {
    const inputPath = parseArgs(process.argv.slice(2));
    const parsed = readBatchFile(inputPath);
    const records = validateBatch(parsed);

    console.error(`Processing ${records.length} record(s)...`);
    const output = await runSync(records);
    process.stdout.write(JSON.stringify(output) + "\n");
    return 0;
  } catch (err) {
    if (err instanceof CliError) {
      process.stdout.write(JSON.stringify(err.toOutput()) + "\n");
      return err.exitCode;
    }
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error_code: "db_error",
        message: `Unexpected error: ${(err as Error).message}`,
      }) + "\n",
    );
    return 5;
  }
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(err);
    process.exit(5);
  },
);
