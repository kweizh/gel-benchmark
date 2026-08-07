"use strict";
/**
 * Idempotent bulk catalog sync CLI.
 *
 * Reads a JSON batch of supplier product records and synchronises it against
 * the local Gel instance: categories and tags are resolved by name (created on
 * demand, never duplicated) and products are inserted or brought in line with
 * their record. The whole batch is applied atomically inside a single
 * client-managed transaction and the outcome of every record is reported as
 * machine-readable JSON on stdout.
 *
 * Usage:  node dist/sync.js --input <path/to/batch.json>
 */
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
const fs = __importStar(require("fs"));
const gel_1 = require("gel");
/* ------------------------------------------------------------------ *
 * EdgeQL
 *
 * The entire batch reaches the server as a single JSON parameter
 * (`<json>$batch`) and every operation below is set-based: it iterates over
 * `json_array_unpack(batch)` rather than issuing one query per record.
 * ------------------------------------------------------------------ */
/** Read the existing products whose `sku` appears in the batch, with the
 * fields needed to classify each record's outcome. Evaluated before any
 * writes so it reflects the pre-run state. */
const Q_READ_EXISTING = `with
  batch := <json>$batch,
  skus := (for r in json_array_unpack(batch) union (<str>json_get(r, 'sku'))),
  p := (select Product filter .sku in {skus})
select p {
  sku,
  name,
  price_cents,
  stock,
  category_name := .category.name,
  tag_labels := array_agg(.tags.label) ?? <array<str>>[],
}`;
/** Ensure every category named by the batch exists, creating the missing
 * ones and reusing the rest. Never duplicates. */
const Q_UPSERT_CATEGORIES = `with
  batch := <json>$batch,
  names := distinct (for r in json_array_unpack(batch) union (<str>json_get(r, 'category')))
for n in {names} union (
  insert Category { name := n }
  unless conflict on .name else (select Category filter .name = n)
)`;
/** Ensure every tag labelled by the batch exists, creating the missing ones
 * and reusing the rest. Never duplicates. */
const Q_UPSERT_TAGS = `with
  batch := <json>$batch,
  labels := distinct (
    for r in json_array_unpack(batch) union (
      array_unpack(<array<str>>json_get(r, 'tags')) ?? <str>{}
    )
  )
for l in {labels} union (
  insert Tag { label := l }
  unless conflict on .label else (select Tag filter .label = l)
)`;
/** Insert products for records whose `sku` does not yet exist. */
const Q_INSERT_PRODUCTS = `with
  batch := <json>$batch,
  rec := (for r in json_array_unpack(batch) union (
    select {
      sku := <str>json_get(r, 'sku'),
      name := <str>json_get(r, 'name'),
      price_cents := <int64>json_get(r, 'price_cents'),
      stock := <int64>json_get(r, 'stock'),
      category_name := <str>json_get(r, 'category'),
      tag_names := <array<str>>json_get(r, 'tags'),
    }
  )),
  existing_skus := (select Product filter .sku in {rec.sku}).sku,
  new_recs := (select rec filter .sku not in {existing_skus})
for r in {new_recs} union (
  insert Product {
    sku := r.sku,
    name := r.name,
    price_cents := r.price_cents,
    stock := r.stock,
    category := (select Category filter .name = r.category_name limit 1),
    tags := (select Tag filter .label in {array_unpack(r.tag_names) ?? <str>{}}),
  }
)`;
/** Update products that already existed and differ from their record. Records
 * that already match are left untouched (no write), which keeps a re-run a true
 * no-op. The two `except` clauses compare the existing tag set with the
 * record's tag set as sets, so order and duplicates are irrelevant. */
const Q_UPDATE_PRODUCTS = `with
  batch := <json>$batch,
  rec := (for r in json_array_unpack(batch) union (
    select {
      sku := <str>json_get(r, 'sku'),
      name := <str>json_get(r, 'name'),
      price_cents := <int64>json_get(r, 'price_cents'),
      stock := <int64>json_get(r, 'stock'),
      category_name := <str>json_get(r, 'category'),
      tag_names := <array<str>>json_get(r, 'tags'),
    }
  )),
  existing := (select Product filter .sku in {rec.sku}),
  changed := (select rec filter exists (
    select existing filter .sku = rec.sku and (
      .name != rec.name or
      .price_cents != rec.price_cents or
      .stock != rec.stock or
      .category.name != rec.category_name or
      exists (.tags.label except (array_unpack(rec.tag_names) ?? <str>{})) or
      exists ((array_unpack(rec.tag_names) ?? <str>{}) except .tags.label)
    )
  ))
for r in {changed} union (
  update Product filter .sku = r.sku set {
    name := r.name,
    price_cents := r.price_cents,
    stock := r.stock,
    category := (select Category filter .name = r.category_name limit 1),
    tags := (select Tag filter .label in {array_unpack(r.tag_names) ?? <str>{}}),
  }
)`;
/* ------------------------------------------------------------------ *
 * Output
 * ------------------------------------------------------------------ */
/** Write exactly one JSON document to stdout, then exit with `code`. The
 * write callback guarantees the document is flushed before the process
 * terminates, so the output is never truncated. */
function finish(doc, code) {
    process.stdout.write(JSON.stringify(doc, null, 2) + "\n", () => {
        process.exit(code);
    });
}
function emitSuccess(r) {
    finish({
        ok: true,
        total: r.total,
        inserted: r.inserted,
        updated: r.updated,
        unchanged: r.unchanged,
        results: r.results,
    }, 0);
}
function emitFailure(errorCode, message, code, extra = {}) {
    finish({ ok: false, error_code: errorCode, message, ...extra }, code);
}
function errMessage(e) {
    if (e instanceof Error && e.message)
        return e.message;
    const s = String(e);
    return s && s !== "undefined" && s !== "null" ? s : "unknown error";
}
/* ------------------------------------------------------------------ *
 * Argument parsing
 * ------------------------------------------------------------------ */
/** Return the value of the first `--input` option, or `undefined` when it is
 * absent (including the case where the flag is given without a value). */
function parseInputPath(argv) {
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a === "--input") {
            return argv[i + 1];
        }
        if (a.startsWith("--input=")) {
            return a.slice("--input=".length);
        }
    }
    return undefined;
}
/* ------------------------------------------------------------------ *
 * Batch validation
 * ------------------------------------------------------------------ */
function isNonEmptyString(v) {
    return typeof v === "string" && v.length > 0;
}
/** A JSON integer `>= 0`. Booleans, null, strings and fractional numbers are
 * rejected. */
function isNonNegativeInteger(v) {
    return typeof v === "number" && Number.isInteger(v) && v >= 0;
}
/** Validate one record against the batch format. Extra fields are ignored. */
function isValidRecord(rec) {
    if (typeof rec !== "object" || rec === null || Array.isArray(rec)) {
        return false;
    }
    const r = rec;
    if (!isNonEmptyString(r.sku))
        return false;
    if (!isNonEmptyString(r.name))
        return false;
    if (!isNonNegativeInteger(r.price_cents))
        return false;
    if (!isNonNegativeInteger(r.stock))
        return false;
    if (!isNonEmptyString(r.category))
        return false;
    if (r.tags !== undefined) {
        if (!Array.isArray(r.tags))
            return false;
        for (const t of r.tags) {
            if (!isNonEmptyString(t))
                return false;
        }
    }
    return true;
}
/* ------------------------------------------------------------------ *
 * Synchronisation
 * ------------------------------------------------------------------ */
/** Compare an existing product with a record. Returns true when every
 * comparable field already matches (so the record is `unchanged`). */
function matches(ex, rec) {
    const exTags = (ex.tag_labels ?? []).slice().sort();
    return (ex.name === rec.name &&
        Number(ex.price_cents) === rec.price_cents &&
        Number(ex.stock) === rec.stock &&
        ex.category_name === rec.category &&
        JSON.stringify(exTags) === JSON.stringify(rec.tags));
}
async function syncBatch(client, batch) {
    return client.transaction(async (tx) => {
        // 1. Snapshot the products that already exist for these SKUs. This read
        //    happens before any write, so it captures the pre-run state used to
        //    classify outcomes.
        const existing = await tx.query(Q_READ_EXISTING, { batch });
        const exMap = new Map();
        for (const p of existing)
            exMap.set(p.sku, p);
        // 2. Classify every record against that snapshot.
        const results = [];
        let inserted = 0;
        let updated = 0;
        let unchanged = 0;
        for (const rec of batch) {
            const ex = exMap.get(rec.sku);
            let outcome;
            if (!ex) {
                outcome = "inserted";
                inserted++;
            }
            else if (matches(ex, rec)) {
                outcome = "unchanged";
                unchanged++;
            }
            else {
                outcome = "updated";
                updated++;
            }
            results.push({
                sku: rec.sku,
                outcome,
                category: rec.category,
                tags: rec.tags,
            });
        }
        // 3. Apply the batch atomically: resolve categories, resolve tags, insert
        //    new products, update changed products. Unchanged records produce no
        //    write at all.
        await tx.execute(Q_UPSERT_CATEGORIES, { batch });
        await tx.execute(Q_UPSERT_TAGS, { batch });
        await tx.execute(Q_INSERT_PRODUCTS, { batch });
        await tx.execute(Q_UPDATE_PRODUCTS, { batch });
        results.sort((a, b) => (a.sku < b.sku ? -1 : a.sku > b.sku ? 1 : 0));
        return { total: batch.length, inserted, updated, unchanged, results };
    });
}
/* ------------------------------------------------------------------ *
 * Entry point
 * ------------------------------------------------------------------ */
async function main() {
    const inputPath = parseInputPath(process.argv.slice(2));
    if (inputPath === undefined) {
        emitFailure("input_unreadable", "No --input argument was provided.", 2);
        return;
    }
    let raw;
    try {
        raw = fs.readFileSync(inputPath, "utf8");
    }
    catch {
        emitFailure("input_unreadable", `The input file could not be read: ${inputPath}`, 2);
        return;
    }
    let data;
    try {
        data = JSON.parse(raw);
    }
    catch {
        emitFailure("input_unreadable", "The input file is not valid JSON.", 2);
        return;
    }
    if (!Array.isArray(data)) {
        emitFailure("invalid_record", "The top-level JSON value must be an array.", 3, { index: null });
        return;
    }
    // Record-format validation (takes precedence over duplicate detection).
    for (let i = 0; i < data.length; i++) {
        if (!isValidRecord(data[i])) {
            emitFailure("invalid_record", `Record at index ${i} violates the batch format.`, 3, { index: i });
            return;
        }
    }
    // Duplicate-SKU detection.
    const seen = new Set();
    for (const rec of data) {
        const sku = rec.sku;
        if (seen.has(sku)) {
            emitFailure("duplicate_sku", `SKU '${sku}' appears in more than one record.`, 4, { sku });
            return;
        }
        seen.add(sku);
    }
    // Normalise: guarantee `tags` is always a de-duplicated, sorted array.
    const batch = data.map((r) => ({
        sku: r.sku,
        name: r.name,
        price_cents: r.price_cents,
        stock: r.stock,
        category: r.category,
        tags: Array.from(new Set(r.tags ?? [])).sort(),
    }));
    let client;
    try {
        client = (0, gel_1.createClient)();
    }
    catch (e) {
        process.stderr.write(`[sync] failed to create client: ${errMessage(e)}\n`);
        emitFailure("db_error", errMessage(e), 5);
        return;
    }
    try {
        const result = await syncBatch(client, batch);
        emitSuccess(result);
    }
    catch (e) {
        process.stderr.write(`[sync] database error: ${errMessage(e)}\n`);
        emitFailure("db_error", errMessage(e), 5);
    }
    finally {
        try {
            await client.close();
        }
        catch {
            /* ignore */
        }
    }
}
main().catch((e) => {
    process.stderr.write(`[sync] fatal: ${errMessage(e)}\n`);
    emitFailure("db_error", errMessage(e), 5);
});
