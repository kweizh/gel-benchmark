import * as fs from "fs";
import { createClient } from "gel";

async function main() {
  // 1. Parse arguments
  let inputPath: string | null = null;
  for (let i = 2; i < process.argv.length; i++) {
    if (process.argv[i] === "--input") {
      inputPath = process.argv[i + 1] || null;
      break;
    }
  }

  if (!inputPath) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "input_unreadable",
      message: "No --input argument was provided."
    }));
    process.exit(2);
  }

  // 2. Read file
  let fileContent: string;
  try {
    fileContent = fs.readFileSync(inputPath, "utf8");
  } catch (err: any) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "input_unreadable",
      message: `Failed to read input file: ${err.message}`
    }));
    process.exit(2);
  }

  // 3. Parse JSON
  let batch: any;
  try {
    batch = JSON.parse(fileContent);
  } catch (err: any) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "input_unreadable",
      message: `Failed to parse input file as JSON: ${err.message}`
    }));
    process.exit(2);
  }

  // 4. Validate top-level value is an array
  if (!Array.isArray(batch)) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "invalid_record",
      message: "The top-level JSON value is not an array.",
      index: null
    }));
    process.exit(3);
  }

  // 5. Validate each record
  for (let i = 0; i < batch.length; i++) {
    const record = batch[i];
    
    if (typeof record !== "object" || record === null || Array.isArray(record)) {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} is not a valid JSON object.`,
        index: i
      }));
      process.exit(3);
    }

    if (typeof record.sku !== "string" || record.sku === "") {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} has an invalid or missing 'sku'.`,
        index: i
      }));
      process.exit(3);
    }

    if (typeof record.name !== "string" || record.name === "") {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} has an invalid or missing 'name'.`,
        index: i
      }));
      process.exit(3);
    }

    if (typeof record.price_cents !== "number" || !Number.isInteger(record.price_cents) || record.price_cents < 0) {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} has an invalid or missing 'price_cents'.`,
        index: i
      }));
      process.exit(3);
    }

    if (typeof record.stock !== "number" || !Number.isInteger(record.stock) || record.stock < 0 || record.stock > 100000) {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} has an invalid or missing 'stock'.`,
        index: i
      }));
      process.exit(3);
    }

    if (typeof record.category !== "string" || record.category === "") {
      console.log(JSON.stringify({
        ok: false,
        error_code: "invalid_record",
        message: `Record at index ${i} has an invalid or missing 'category'.`,
        index: i
      }));
      process.exit(3);
    }

    if (record.tags !== undefined) {
      if (!Array.isArray(record.tags)) {
        console.log(JSON.stringify({
          ok: false,
          error_code: "invalid_record",
          message: `Record at index ${i} has an invalid 'tags' property (must be an array).`,
          index: i
        }));
        process.exit(3);
      }
      for (let j = 0; j < record.tags.length; j++) {
        const tag = record.tags[j];
        if (typeof tag !== "string" || tag === "") {
          console.log(JSON.stringify({
            ok: false,
            error_code: "invalid_record",
            message: `Record at index ${i} has an invalid tag at index ${j} (must be a non-empty string).`,
            index: i
          }));
          process.exit(3);
        }
      }
    }
  }

  // 6. Validate duplicate SKUs
  const seenSkus = new Set<string>();
  for (const record of batch) {
    if (seenSkus.has(record.sku)) {
      console.log(JSON.stringify({
        ok: false,
        error_code: "duplicate_sku",
        message: `Duplicate SKU detected: '${record.sku}'`,
        sku: record.sku
      }));
      process.exit(4);
    }
    seenSkus.add(record.sku);
  }

  // Handle empty batch case immediately
  if (batch.length === 0) {
    console.log(JSON.stringify({
      ok: true,
      total: 0,
      inserted: 0,
      updated: 0,
      unchanged: 0,
      results: []
    }));
    process.exit(0);
  }

  // 7. Execute synchronisation inside a transaction
  const client = createClient();
  try {
    const output = await client.transaction(async (tx) => {
      // Step A: Fetch existing products for the SKUs in the batch
      const skus = batch.map((r: any) => r.sku);
      const existingList = await tx.query<any>(`
        select Product {
          sku,
          name,
          price_cents,
          stock,
          category: { name },
          tags: { label }
        } filter .sku in array_unpack(<array<str>>$skus)
      `, { skus });

      const existingMap = new Map<string, any>();
      for (const p of existingList) {
        existingMap.set(p.sku, p);
      }

      // Step B: Classify outcomes for each record
      const results: any[] = [];
      let inserted = 0;
      let updated = 0;
      let unchanged = 0;

      for (const record of batch) {
        const existing = existingMap.get(record.sku);
        let outcome: "inserted" | "updated" | "unchanged";

        if (!existing) {
          outcome = "inserted";
          inserted++;
        } else {
          const recordTags = Array.from(new Set(record.tags || [])).sort();
          const existingTags = (existing.tags || []).map((t: any) => t.label).sort();
          const tagsMatch = recordTags.length === existingTags.length && recordTags.every((t, i) => t === existingTags[i]);

          const isNameDiff = existing.name !== record.name;
          const isPriceDiff = existing.price_cents !== record.price_cents;
          const isStockDiff = existing.stock !== record.stock;
          const isCatDiff = existing.category?.name !== record.category;
          const isTagsDiff = !tagsMatch;

          if (isNameDiff || isPriceDiff || isStockDiff || isCatDiff || isTagsDiff) {
            outcome = "updated";
            updated++;
          } else {
            outcome = "unchanged";
            unchanged++;
          }
        }

        const deDupSortedTags = Array.from(new Set(record.tags || [])).sort();
        results.push({
          sku: record.sku,
          outcome,
          category: record.category,
          tags: deDupSortedTags
        });
      }

      // Step C: If there are any modifications, execute updates
      // (Even if unchanged, we can run them or skip. Running them is safer and completely idempotent)
      const batchJson = JSON.stringify(batch);

      // Ensure all categories exist
      await tx.execute(`
        with
          batch := to_json(<str>$batchJson),
          categories := (
            select distinct <str>json_array_unpack(batch)['category']
          )
        for cat_name in categories union (
          insert Category { name := cat_name }
          unless conflict on .name else Category
        )
      `, { batchJson });

      // Ensure all tags exist
      await tx.execute(`
        with
          batch := to_json(<str>$batchJson),
          items := json_array_unpack(batch),
          tags_arrays := (
            select json_get(items, 'tags')
            filter json_typeof(json_get(items, 'tags')) = 'array'
          ),
          unique_tags := (
            select distinct <str>json_array_unpack(tags_arrays)
          )
        for tag_label in unique_tags union (
          insert Tag { label := tag_label }
          unless conflict on .label else Tag
        )
      `, { batchJson });

      // Insert/update products
      await tx.execute(`
        with
          batch := to_json(<str>$batchJson)
        for item in json_array_unpack(batch) union (
          with
            sku_val := <str>item['sku'],
            name_val := <str>item['name'],
            price_val := <int64>item['price_cents'],
            stock_val := <int64>item['stock'],
            cat_val := <str>item['category'],
            tags_val := json_get(item, 'tags'),
            item_tags := (
              if json_typeof(tags_val) = 'array' then
                <str>json_array_unpack(tags_val)
              else
                <str>{}
            ),
            cat_obj := (select Category filter .name = cat_val limit 1),
            tag_objs := (select Tag filter .label in item_tags)

          insert Product {
            sku := sku_val,
            name := name_val,
            price_cents := price_val,
            stock := stock_val,
            category := cat_obj,
            tags := tag_objs
          }
          unless conflict on .sku else (
            update Product set {
              name := name_val,
              price_cents := price_val,
              stock := stock_val,
              category := cat_obj,
              tags := tag_objs
            }
          )
        )
      `, { batchJson });

      // Sort results by sku in ascending lexicographic order
      results.sort((a, b) => {
        if (a.sku < b.sku) return -1;
        if (a.sku > b.sku) return 1;
        return 0;
      });

      return {
        ok: true,
        total: batch.length,
        inserted,
        updated,
        unchanged,
        results
      };
    });

    console.log(JSON.stringify(output));
    process.exit(0);
  } catch (err: any) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "db_error",
      message: `Database error: ${err.message || err}`
    }));
    process.exit(5);
  } finally {
    await client.close();
  }
}

main();
