"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.validateBatch = validateBatch;
const types_1 = require("./types");
function isNonEmptyString(value) {
    return typeof value === "string" && value.length > 0;
}
function isNonNegativeInt(value) {
    return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
/**
 * Validates a single record against the documented batch record format.
 * Returns the normalized record on success, or `null` if the record is
 * malformed in any way.
 */
function validateRecord(rec) {
    if (typeof rec !== "object" || rec === null || Array.isArray(rec)) {
        return null;
    }
    const obj = rec;
    if (!isNonEmptyString(obj.sku))
        return null;
    if (!isNonEmptyString(obj.name))
        return null;
    if (!isNonNegativeInt(obj.price_cents))
        return null;
    if (!isNonNegativeInt(obj.stock))
        return null;
    if (!isNonEmptyString(obj.category))
        return null;
    let tags = [];
    if (Object.prototype.hasOwnProperty.call(obj, "tags")) {
        const rawTags = obj.tags;
        if (!Array.isArray(rawTags))
            return null;
        for (const t of rawTags) {
            if (!isNonEmptyString(t))
                return null;
        }
        tags = rawTags;
    }
    return {
        sku: obj.sku,
        name: obj.name,
        price_cents: obj.price_cents,
        stock: obj.stock,
        category: obj.category,
        tags,
    };
}
/**
 * Validates the full parsed JSON payload of a batch file.
 *
 * Throws a `CliError` (`invalid_record` or `duplicate_sku`) when the batch
 * does not conform to the documented format. Record-format validation is
 * always performed before duplicate-sku detection.
 */
function validateBatch(parsed) {
    if (!Array.isArray(parsed)) {
        throw new types_1.CliError(3, "invalid_record", "The top-level JSON value must be an array of records.", { index: null });
    }
    const records = [];
    for (let i = 0; i < parsed.length; i++) {
        const normalized = validateRecord(parsed[i]);
        if (normalized === null) {
            throw new types_1.CliError(3, "invalid_record", `Record at index ${i} does not conform to the batch record format.`, { index: i });
        }
        records.push(normalized);
    }
    const seen = new Set();
    for (const rec of records) {
        if (seen.has(rec.sku)) {
            throw new types_1.CliError(4, "duplicate_sku", `The sku "${rec.sku}" appears in more than one record.`, { sku: rec.sku });
        }
        seen.add(rec.sku);
    }
    return records;
}
