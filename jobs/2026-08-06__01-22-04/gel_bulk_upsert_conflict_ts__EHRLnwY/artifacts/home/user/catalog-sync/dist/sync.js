"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const fs = __importStar(require("node:fs"));
const gel_1 = require("gel");
const validate_1 = require("./validate");
const types_1 = require("./types");
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
function parseArgs(argv) {
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
function readBatchFile(inputPath) {
    if (!inputPath) {
        throw new types_1.CliError(2, "input_unreadable", "Missing required --input <path> argument.");
    }
    let content;
    try {
        content = fs.readFileSync(inputPath, "utf8");
    }
    catch (err) {
        throw new types_1.CliError(2, "input_unreadable", `Could not read input file "${inputPath}": ${err.message}`);
    }
    try {
        return JSON.parse(content);
    }
    catch (err) {
        throw new types_1.CliError(2, "input_unreadable", `Input file "${inputPath}" does not contain valid JSON: ${err.message}`);
    }
}
function dedupeSorted(values) {
    return Array.from(new Set(values)).sort();
}
function computeOutcome(existing, rec, dedupedTags) {
    if (!existing) {
        return "inserted";
    }
    const existingTagsSet = new Set(existing.tag_labels);
    const wantedTagsSet = new Set(dedupedTags);
    const tagsEqual = existingTagsSet.size === wantedTagsSet.size &&
        [...wantedTagsSet].every((t) => existingTagsSet.has(t));
    const changed = existing.name !== rec.name ||
        existing.price_cents !== rec.price_cents ||
        existing.stock !== rec.stock ||
        existing.category_name !== rec.category ||
        !tagsEqual;
    return changed ? "updated" : "unchanged";
}
async function runSync(records) {
    const dedupedTagsBySku = new Map();
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
    const client = (0, gel_1.createClient)();
    let outcomes;
    try {
        outcomes = await client.transaction(async (tx) => {
            const skus = records.map((r) => r.sku);
            const existingRows = await tx.query(READ_EXISTING_QUERY, { skus });
            const existingBySku = new Map();
            for (const row of existingRows) {
                existingBySku.set(row.sku, row);
            }
            const result = new Map();
            for (const rec of records) {
                const existing = existingBySku.get(rec.sku);
                const deduped = dedupedTagsBySku.get(rec.sku);
                result.set(rec.sku, computeOutcome(existing, rec, deduped));
            }
            await tx.execute(SYNC_BATCH_QUERY, { batch: records });
            return result;
        });
    }
    catch (err) {
        throw new types_1.CliError(5, "db_error", `The database rejected the batch: ${err.message}`);
    }
    finally {
        await client.close();
    }
    let inserted = 0;
    let updated = 0;
    let unchanged = 0;
    const results = records.map((rec) => {
        const outcome = outcomes.get(rec.sku);
        if (outcome === "inserted")
            inserted++;
        else if (outcome === "updated")
            updated++;
        else
            unchanged++;
        return {
            sku: rec.sku,
            outcome,
            category: rec.category,
            tags: dedupedTagsBySku.get(rec.sku),
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
async function main() {
    try {
        const inputPath = parseArgs(process.argv.slice(2));
        const parsed = readBatchFile(inputPath);
        const records = (0, validate_1.validateBatch)(parsed);
        console.error(`Processing ${records.length} record(s)...`);
        const output = await runSync(records);
        process.stdout.write(JSON.stringify(output) + "\n");
        return 0;
    }
    catch (err) {
        if (err instanceof types_1.CliError) {
            process.stdout.write(JSON.stringify(err.toOutput()) + "\n");
            return err.exitCode;
        }
        process.stdout.write(JSON.stringify({
            ok: false,
            error_code: "db_error",
            message: `Unexpected error: ${err.message}`,
        }) + "\n");
        return 5;
    }
}
main().then((code) => process.exit(code), (err) => {
    console.error(err);
    process.exit(5);
});
