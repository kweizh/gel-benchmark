import * as fs from "node:fs";
import createClient from "gel";

interface BatchRecord {
  sku: string;
  name: string;
  price_cents: number;
  stock: number;
  category: string;
  tags?: string[];
}

interface SyncResult {
  sku: string;
  outcome: "inserted" | "updated" | "unchanged";
  category: string;
  tags: string[];
}

interface SuccessOutput {
  ok: true;
  total: number;
  inserted: number;
  updated: number;
  unchanged: number;
  results: SyncResult[];
}

interface FailureOutput {
  ok: false;
  error_code: string;
  message: string;
  index?: number | null;
  sku?: string;
}

type Output = SuccessOutput | FailureOutput;

function failAndExit(output: FailureOutput, exitCode: number): never {
  process.stdout.write(JSON.stringify(output) + "\n");
  process.exit(exitCode);
}

function parseArgs(): string {
  const inputIdx = process.argv.indexOf("--input");
  if (inputIdx === -1 || inputIdx + 1 >= process.argv.length) {
    failAndExit(
      {
        ok: false,
        error_code: "input_unreadable",
        message: "Missing required --input argument",
      },
      2,
    );
  }
  return process.argv[inputIdx + 1];
}

function readAndParseJSON(filePath: string): unknown {
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, "utf-8");
  } catch {
    failAndExit(
      {
        ok: false,
        error_code: "input_unreadable",
        message: `File not found or unreadable: ${filePath}`,
      },
      2,
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    failAndExit(
      {
        ok: false,
        error_code: "input_unreadable",
        message: `File is not valid JSON: ${filePath}`,
      },
      2,
    );
  }

  return parsed;
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === "string" && v.length > 0;
}

function isInteger(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

function isNonNegativeInteger(v: unknown): v is number {
  return isInteger(v) && v >= 0;
}

function validateRecord(
  record: unknown,
  index: number,
): asserts record is BatchRecord {
  if (record === null || typeof record !== "object") {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} is not an object`,
        index,
      },
      3,
    );
  }

  const r = record as Record<string, unknown>;

  if (!isNonEmptyString(r.sku)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} has missing or invalid "sku" (must be a non-empty string)`,
        index,
      },
      3,
    );
  }

  if (!isNonEmptyString(r.name)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} has missing or invalid "name" (must be a non-empty string)`,
        index,
      },
      3,
    );
  }

  if (!isNonNegativeInteger(r.price_cents)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} has missing or invalid "price_cents" (must be a non-negative integer)`,
        index,
      },
      3,
    );
  }

  if (!isNonNegativeInteger(r.stock)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} has missing or invalid "stock" (must be a non-negative integer)`,
        index,
      },
      3,
    );
  }

  if (!isNonEmptyString(r.category)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${index} has missing or invalid "category" (must be a non-empty string)`,
        index,
      },
      3,
    );
  }

  if (r.tags !== undefined) {
    if (!Array.isArray(r.tags)) {
      failAndExit(
        {
          ok: false,
          error_code: "invalid_record",
          message: `Record at index ${index} has "tags" that is not an array`,
          index,
        },
        3,
      );
    }
    for (let ti = 0; ti < r.tags.length; ti++) {
      if (!isNonEmptyString(r.tags[ti])) {
        failAndExit(
          {
            ok: false,
            error_code: "invalid_record",
            message: `Record at index ${index} has a tag at position ${ti} that is not a non-empty string`,
            index,
          },
          3,
        );
      }
    }
  }
}

function validateBatch(parsed: unknown): BatchRecord[] {
  if (!Array.isArray(parsed)) {
    failAndExit(
      {
        ok: false,
        error_code: "invalid_record",
        message: "Top-level JSON value is not an array",
        index: null,
      },
      3,
    );
  }

  const records: BatchRecord[] = [];
  for (let i = 0; i < parsed.length; i++) {
    validateRecord(parsed[i], i);
    records.push(parsed[i] as BatchRecord);
  }

  // Check for duplicate SKUs
  const seen = new Map<string, number>();
  for (let i = 0; i < records.length; i++) {
    const sku = records[i].sku;
    if (seen.has(sku)) {
      failAndExit(
        {
          ok: false,
          error_code: "duplicate_sku",
          message: `Duplicate SKU "${sku}" found at index ${i} (first seen at index ${seen.get(sku)})`,
          sku,
        },
        4,
      );
    }
    seen.set(sku, i);
  }

  return records;
}

const UPSERT_CATEGORIES_QUERY = `
with
  batch := to_json(<str>$batch),
  records := json_array_unpack(batch),
  cat_names := (select distinct <str>records['category']),
  for name in cat_names union (
    insert Category { name := name }
    unless conflict on .name
    else (select Category)
  )
`;

const UPSERT_TAGS_QUERY = `
with
  batch := to_json(<str>$batch),
  records := json_array_unpack(batch),
  tag_labels := (select distinct <str>json_array_unpack(json_get(records, 'tags') ?? to_json('[]'))),
  for label in tag_labels union (
    insert Tag { label := label }
    unless conflict on .label
    else (select Tag)
  )
`;

const UPSERT_PRODUCTS_QUERY = `
with
  batch := to_json(<str>$batch),
  records := json_array_unpack(batch),
  for r in records union (
    with
      sku := <str>r['sku'],
      existing := (select Product filter .sku = sku),
      cat := assert_single((select Category filter .name = <str>r['category'])),
      rec_tag_labels := <str>json_array_unpack(json_get(r, 'tags') ?? to_json('[]')),
      desired_tags := (select Tag filter .label in rec_tag_labels),
      outcome := (
        if not exists existing then 'inserted'
        else if (
          existing.name != <str>r['name']
          or existing.price_cents != <int64>r['price_cents']
          or existing.stock != <int64>r['stock']
          or existing.category != cat
          or count(existing.tags) != count(desired_tags)
          or exists (existing.tags except desired_tags)
        ) then 'updated'
        else 'unchanged'
      ),
      _product := (
        insert Product {
          sku := sku,
          name := <str>r['name'],
          price_cents := <int64>r['price_cents'],
          stock := <int64>r['stock'],
          category := cat,
          tags := desired_tags
        }
        unless conflict on .sku
        else (
          update Product set {
            name := <str>r['name'],
            price_cents := <int64>r['price_cents'],
            stock := <int64>r['stock'],
            category := cat,
            tags := desired_tags
          }
        )
      )
    select {
      sku := sku,
      outcome := outcome,
      category := <str>r['category'],
      tags := array_agg((select rec_tag_labels order by rec_tag_labels))
    }
  )
`;

interface ProductQueryRow {
  sku: string;
  outcome: string;
  category: string;
  tags: string[];
}

async function main(): Promise<void> {
  const inputPath = parseArgs();
  const parsed = readAndParseJSON(inputPath);
  const records = validateBatch(parsed);

  if (records.length === 0) {
    const output: SuccessOutput = {
      ok: true,
      total: 0,
      inserted: 0,
      updated: 0,
      unchanged: 0,
      results: [],
    };
    process.stdout.write(JSON.stringify(output) + "\n");
    process.exit(0);
  }

  const batchJSON = JSON.stringify(records);

  const client = createClient();

  try {
    const rows: ProductQueryRow[] = await client.transaction(async (tx) => {
      await tx.execute(UPSERT_CATEGORIES_QUERY, { batch: batchJSON });
      await tx.execute(UPSERT_TAGS_QUERY, { batch: batchJSON });
      return await tx.query<ProductQueryRow>(UPSERT_PRODUCTS_QUERY, {
        batch: batchJSON,
      });
    });

    // Sort results by SKU ascending
    rows.sort((a, b) => (a.sku < b.sku ? -1 : a.sku > b.sku ? 1 : 0));

    const counts = { inserted: 0, updated: 0, unchanged: 0 };
    const results: SyncResult[] = [];

    for (const row of rows) {
      const outcome = row.outcome as SyncResult["outcome"];
      counts[outcome]++;
      results.push({
        sku: row.sku,
        outcome,
        category: row.category,
        tags: row.tags,
      });
    }

    const output: SuccessOutput = {
      ok: true,
      total: records.length,
      ...counts,
      results,
    };

    process.stdout.write(JSON.stringify(output) + "\n");
    process.exit(0);
  } catch (err: unknown) {
    const message =
      err instanceof Error ? err.message : String(err);
    failAndExit(
      {
        ok: false,
        error_code: "db_error",
        message: `Database error: ${message}`,
      },
      5,
    );
  }
}

main().catch((err) => {
  const message =
    err instanceof Error ? err.message : String(err);
  failAndExit(
    {
      ok: false,
      error_code: "db_error",
      message: `Unexpected error: ${message}`,
    },
    5,
  );
});
