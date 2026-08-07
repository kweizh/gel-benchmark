import { readFileSync } from "node:fs";
import { createClient, type Client } from "gel";
import type { Transaction } from "gel/dist/transaction.js";

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

type ErrorCode = "IO_ERROR" | "INVALID_PAYLOAD" | "STALE_REVISION";

class IngestError extends Error {
  code: ErrorCode;
  constructor(code: ErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

function ioError(message: string): never {
  throw new IngestError("IO_ERROR", message);
}

function invalid(message: string): never {
  throw new IngestError("INVALID_PAYLOAD", message);
}

// ---------------------------------------------------------------------------
// Validated payload types
// ---------------------------------------------------------------------------

interface ValidatedVariant {
  code: string;
  label: string;
  stock: number;
}

interface ValidatedProduct {
  sku: string;
  title: string;
  price_cents: number;
  vendor_code: string;
  vendor_name: string;
  tags: string[]; // deduplicated, in first-seen order
  variants: ValidatedVariant[];
}

interface ValidatedPayload {
  source: string;
  revision: number;
  products: ValidatedProduct[];
}

// ---------------------------------------------------------------------------
// Small validation helpers
// ---------------------------------------------------------------------------

function isPlainObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function isNonEmptyString(x: unknown): x is string {
  return typeof x === "string" && x.length > 0;
}

function isInt(x: unknown): x is number {
  return typeof x === "number" && Number.isInteger(x) && Number.isFinite(x);
}

function isIntAtLeast(x: unknown, min: number): x is number {
  return isInt(x) && x >= min;
}

// ---------------------------------------------------------------------------
// Payload validation
// ---------------------------------------------------------------------------

function validatePayload(raw: unknown): ValidatedPayload {
  if (!isPlainObject(raw)) {
    invalid("payload must be a JSON object");
  }

  const source = raw.source;
  if (!isNonEmptyString(source)) {
    invalid("source must be a non-empty string");
  }

  const revision = raw.revision;
  if (!isIntAtLeast(revision, 1)) {
    invalid("revision must be an integer >= 1");
  }

  const productsRaw = raw.products;
  if (!Array.isArray(productsRaw) || productsRaw.length === 0) {
    invalid("products must be a non-empty array");
  }

  const seenSkus = new Set<string>();
  const products: ValidatedProduct[] = [];

  for (const productRaw of productsRaw) {
    if (!isPlainObject(productRaw)) {
      invalid("each product must be an object");
    }

    const sku = productRaw.sku;
    if (!isNonEmptyString(sku)) {
      invalid("product sku must be a non-empty string");
    }
    if (seenSkus.has(sku)) {
      invalid(`duplicate sku in batch: ${sku}`);
    }
    seenSkus.add(sku);

    const title = productRaw.title;
    if (!isNonEmptyString(title)) {
      invalid("product title must be a non-empty string");
    }

    const priceCents = productRaw.price_cents;
    if (!isIntAtLeast(priceCents, 0)) {
      invalid("product price_cents must be an integer >= 0");
    }

    const vendorRaw = productRaw.vendor;
    if (!isPlainObject(vendorRaw)) {
      invalid("product vendor must be an object");
    }
    const vendorCode = vendorRaw.code;
    const vendorName = vendorRaw.name;
    if (!isNonEmptyString(vendorCode) || !isNonEmptyString(vendorName)) {
      invalid("product vendor.code and vendor.name must be non-empty strings");
    }

    let tags: string[] = [];
    if (productRaw.tags !== undefined) {
      const tagsRaw = productRaw.tags;
      if (!Array.isArray(tagsRaw)) {
        invalid("product tags must be an array");
      }
      for (const t of tagsRaw) {
        if (!isNonEmptyString(t)) {
          invalid("each tag must be a non-empty string");
        }
      }
      tags = tagsRaw as string[];
    }
    const dedupedTags: string[] = [];
    const seenTags = new Set<string>();
    for (const t of tags) {
      if (!seenTags.has(t)) {
        seenTags.add(t);
        dedupedTags.push(t);
      }
    }

    let variants: ValidatedVariant[] = [];
    if (productRaw.variants !== undefined) {
      const variantsRaw = productRaw.variants;
      if (!Array.isArray(variantsRaw)) {
        invalid("product variants must be an array");
      }
      const seenCodes = new Set<string>();
      for (const vRaw of variantsRaw) {
        if (!isPlainObject(vRaw)) {
          invalid("each variant must be an object");
        }
        const code = vRaw.code;
        const label = vRaw.label;
        const stock = vRaw.stock;
        if (!isNonEmptyString(code)) {
          invalid("variant code must be a non-empty string");
        }
        if (!isNonEmptyString(label)) {
          invalid("variant label must be a non-empty string");
        }
        if (!isIntAtLeast(stock, 0)) {
          invalid("variant stock must be an integer >= 0");
        }
        if (seenCodes.has(code)) {
          invalid(`duplicate variant code for product ${sku}: ${code}`);
        }
        seenCodes.add(code);
        variants.push({ code, label, stock });
      }
    }

    products.push({
      sku,
      title,
      price_cents: priceCents,
      vendor_code: vendorCode,
      vendor_name: vendorName,
      tags: dedupedTags,
      variants,
    });
  }

  return { source, revision, products };
}

// ---------------------------------------------------------------------------
// Output types
// ---------------------------------------------------------------------------

interface OutputVariant {
  code: string;
  label: string;
  stock: number;
}

interface OutputProduct {
  sku: string;
  title: string;
  price_cents: number;
  vendor_code: string;
  tags: string[];
  variants: OutputVariant[];
  created: boolean;
}

interface Counts {
  products_created: number;
  products_updated: number;
  vendors_created: number;
  vendors_updated: number;
  tags_created: number;
  variants_created: number;
  variants_updated: number;
  variants_removed: number;
}

interface Totals {
  products_in_db: number;
  variants_in_db: number;
  tags_in_db: number;
  vendors_in_db: number;
  stock_total: number;
}

interface SuccessResult {
  ok: true;
  source: string;
  revision: number;
  counts: Counts;
  products: OutputProduct[];
  totals: Totals;
}

function asc(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

// ---------------------------------------------------------------------------
// Core transaction logic
// ---------------------------------------------------------------------------

async function applyBatch(
  tx: Transaction,
  payload: ValidatedPayload,
): Promise<SuccessResult> {
  const { source, revision, products } = payload;

  // --- staleness check -----------------------------------------------------
  const existingSource = await tx.querySingle<{ revision: number } | null>(
    `select SyncSource { revision } filter .code = <str>$source`,
    { source },
  );
  if (existingSource !== null && revision <= existingSource.revision) {
    throw new IngestError(
      "STALE_REVISION",
      `batch revision ${revision} is not greater than stored revision ${existingSource.revision} for source "${source}"`,
    );
  }

  // --- gather "before" snapshot ---------------------------------------------
  const vendorCodeToName = new Map<string, string>();
  for (const p of products) {
    vendorCodeToName.set(p.vendor_code, p.vendor_name); // last occurrence wins
  }
  const vendorCodes = [...vendorCodeToName.keys()];

  const tagLabelSet = new Set<string>();
  for (const p of products) {
    for (const t of p.tags) tagLabelSet.add(t);
  }
  const tagLabels = [...tagLabelSet];

  const skus = products.map((p) => p.sku);

  const [existingVendors, existingTagRows, existingProductRows, existingVariantRows] =
    await Promise.all([
      tx.query<{ code: string; name: string }>(
        `select Vendor { code, name } filter .code in array_unpack(<array<str>>$codes)`,
        { codes: vendorCodes },
      ),
      tx.query<{ label: string }>(
        `select Tag { label } filter .label in array_unpack(<array<str>>$labels)`,
        { labels: tagLabels },
      ),
      tx.query<{ sku: string }>(
        `select Product { sku } filter .sku in array_unpack(<array<str>>$skus)`,
        { skus },
      ),
      tx.query<{ code: string; product: { sku: string } }>(
        `select Variant { code, product: { sku } } filter .product.sku in array_unpack(<array<str>>$skus)`,
        { skus },
      ),
    ]);

  const existingVendorByCode = new Map<string, string>();
  for (const v of existingVendors) existingVendorByCode.set(v.code, v.name);

  const existingTagLabels = new Set<string>();
  for (const t of existingTagRows) existingTagLabels.add(t.label);

  const existingProductSkus = new Set<string>();
  for (const p of existingProductRows) existingProductSkus.add(p.sku);

  const existingVariantsBySku = new Map<string, Set<string>>();
  for (const v of existingVariantRows) {
    const sku = v.product.sku;
    if (!existingVariantsBySku.has(sku)) existingVariantsBySku.set(sku, new Set());
    existingVariantsBySku.get(sku)!.add(v.code);
  }

  // --- counts (computed from the "before" snapshot) --------------------------
  const counts: Counts = {
    products_created: 0,
    products_updated: 0,
    vendors_created: 0,
    vendors_updated: 0,
    tags_created: 0,
    variants_created: 0,
    variants_updated: 0,
    variants_removed: 0,
  };

  for (const [code, name] of vendorCodeToName) {
    if (existingVendorByCode.has(code)) {
      if (existingVendorByCode.get(code) !== name) counts.vendors_updated++;
    } else {
      counts.vendors_created++;
    }
  }

  for (const label of tagLabels) {
    if (!existingTagLabels.has(label)) counts.tags_created++;
  }

  for (const p of products) {
    if (existingProductSkus.has(p.sku)) {
      counts.products_updated++;
    } else {
      counts.products_created++;
    }
    const existingCodes = existingVariantsBySku.get(p.sku) ?? new Set<string>();
    const payloadCodes = new Set(p.variants.map((v) => v.code));
    for (const v of p.variants) {
      if (existingCodes.has(v.code)) {
        counts.variants_updated++;
      } else {
        counts.variants_created++;
      }
    }
    for (const code of existingCodes) {
      if (!payloadCodes.has(code)) counts.variants_removed++;
    }
  }

  // --- writes ----------------------------------------------------------------

  // Vendors
  for (const [code, name] of vendorCodeToName) {
    await tx.execute(
      `insert Vendor {
        code := <str>$code,
        name := <str>$name,
      }
      unless conflict on .code
      else (
        update Vendor set { name := <str>$name }
      )`,
      { code, name },
    );
  }

  // Tags
  for (const label of tagLabels) {
    await tx.execute(
      `insert Tag { label := <str>$label }
      unless conflict on .label
      else (
        select Tag filter .label = <str>$label
      )`,
      { label },
    );
  }

  // Products (create or overwrite title/price/vendor/tags)
  for (const p of products) {
    await tx.execute(
      `insert Product {
        sku := <str>$sku,
        title := <str>$title,
        price_cents := <int64>$price_cents,
        revision := <int64>$revision,
        vendor := (select Vendor filter .code = <str>$vendor_code),
        tags := (select Tag filter .label in array_unpack(<array<str>>$tags)),
      }
      unless conflict on .sku
      else (
        update Product
        set {
          title := <str>$title,
          price_cents := <int64>$price_cents,
          revision := <int64>$revision,
          vendor := (select Vendor filter .code = <str>$vendor_code),
          tags := (select Tag filter .label in array_unpack(<array<str>>$tags)),
        }
      )`,
      {
        sku: p.sku,
        title: p.title,
        price_cents: p.price_cents,
        revision,
        vendor_code: p.vendor_code,
        tags: p.tags,
      },
    );
  }

  // Variants: upsert payload variants, then delete stale ones per product
  for (const p of products) {
    for (const v of p.variants) {
      await tx.execute(
        `insert Variant {
          product := (select Product filter .sku = <str>$sku),
          code := <str>$code,
          label := <str>$label,
          stock := <int64>$stock,
        }
        unless conflict on (.product, .code)
        else (
          update Variant
          set {
            label := <str>$label,
            stock := <int64>$stock,
          }
        )`,
        { sku: p.sku, code: v.code, label: v.label, stock: v.stock },
      );
    }
    const codes = p.variants.map((v) => v.code);
    await tx.execute(
      `delete Variant
      filter .product.sku = <str>$sku and .code not in array_unpack(<array<str>>$codes)`,
      { sku: p.sku, codes },
    );
  }

  // Sync source
  await tx.execute(
    `insert SyncSource {
      code := <str>$source,
      revision := <int64>$revision,
    }
    unless conflict on .code
    else (
      update SyncSource set { revision := <int64>$revision }
    )`,
    { source, revision },
  );

  // --- totals (whole DB, after this batch) ------------------------------------
  const totals = await tx.queryRequiredSingle<Totals>(
    `select {
      products_in_db := (select count(Product)),
      variants_in_db := (select count(Variant)),
      tags_in_db := (select count(Tag)),
      vendors_in_db := (select count(Vendor)),
      stock_total := (select sum(Variant.stock)),
    }`,
  );

  // --- assemble product output -------------------------------------------------
  const outputProducts: OutputProduct[] = products
    .map((p) => ({
      sku: p.sku,
      title: p.title,
      price_cents: p.price_cents,
      vendor_code: p.vendor_code,
      tags: [...p.tags].sort(asc),
      variants: p.variants
        .map((v) => ({ code: v.code, label: v.label, stock: v.stock }))
        .sort((a, b) => asc(a.code, b.code)),
      created: !existingProductSkus.has(p.sku),
    }))
    .sort((a, b) => asc(a.sku, b.sku));

  return {
    ok: true,
    source,
    revision,
    counts,
    products: outputProducts,
    totals,
  };
}

// ---------------------------------------------------------------------------
// CLI entry point
// ---------------------------------------------------------------------------

function parseArgs(argv: string[]): string {
  const idx = argv.indexOf("--input");
  if (idx === -1 || idx + 1 >= argv.length) {
    ioError("--input <path> is required");
  }
  return argv[idx + 1];
}

function loadPayload(path: string): unknown {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    ioError(`could not read input file: ${(err as Error).message}`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    ioError(`input file is not valid JSON: ${(err as Error).message}`);
  }
}

async function main() {
  let client: Client | undefined;
  try {
    const inputPath = parseArgs(process.argv.slice(2));
    const raw = loadPayload(inputPath);
    const payload = validatePayload(raw);

    client = createClient();
    const result = await client.transaction((tx) => applyBatch(tx, payload));

    process.stdout.write(JSON.stringify(result) + "\n");
    process.exitCode = 0;
  } catch (err) {
    let code: ErrorCode = "INVALID_PAYLOAD";
    let message = "unknown error";
    if (err instanceof IngestError) {
      code = err.code;
      message = err.message;
    } else if (err instanceof Error) {
      message = err.message;
      code = "INVALID_PAYLOAD";
    }
    process.stdout.write(
      JSON.stringify({ ok: false, error_code: code, message }) + "\n",
    );
    process.exitCode = 1;
  } finally {
    if (client) {
      await client.close();
    }
  }
}

main();
