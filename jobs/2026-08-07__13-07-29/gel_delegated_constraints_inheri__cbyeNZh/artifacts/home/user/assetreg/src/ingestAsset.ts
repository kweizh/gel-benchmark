import { type Client, ConstraintViolationError, MissingRequiredError } from "gel";
import type { AssetRecord, ResultEntry } from "./types";

const KIND_TO_TYPE: Record<string, string> = {
  server: "ServerAsset",
  storage: "StorageAsset",
};

interface FieldSpec {
  line: string;
  argName: string;
  value: unknown;
}

/**
 * Attempts to insert a single asset record against the live database.
 * The insert is issued as a single, standalone query so that a rejected
 * record leaves nothing behind (Gel implicitly wraps each top-level query
 * in its own transaction).
 */
export async function ingestAsset(
  client: Client,
  rec: AssetRecord,
): Promise<ResultEntry> {
  const base: Pick<ResultEntry, "seq" | "kind" | "serial"> = {
    seq: rec.seq,
    kind: rec.kind,
    serial: rec.serial,
  };

  const typeName = KIND_TO_TYPE[rec.kind];
  if (!typeName) {
    return {
      ...base,
      status: "rejected",
      reason: `UNKNOWN_KIND(${rec.kind})`,
      error_class: "InvalidValueError",
    };
  }

  const fields: FieldSpec[] = [
    { line: "code := <str>$code", argName: "code", value: rec.code },
    { line: "serial := <str>$serial", argName: "serial", value: rec.serial },
    {
      line: "region := (select Region filter .key = <str>$region_key)",
      argName: "region_key",
      value: rec.region,
    },
    { line: "slot := <int64>$slot", argName: "slot", value: rec.slot },
    {
      line: "capacity := <int64>$capacity",
      argName: "capacity",
      value: rec.capacity,
    },
    {
      line: "reserved := <int64>$reserved",
      argName: "reserved",
      value: rec.reserved,
    },
    {
      line: "revision := <int64>$revision",
      argName: "revision",
      value: rec.revision,
    },
  ];

  if (rec.tags !== undefined) {
    fields.push({
      line: "tags := array_unpack(<array<str>>$tags)",
      argName: "tags",
      value: rec.tags,
    });
  }

  if (rec.kind === "server") {
    if (rec.hostname !== undefined) {
      fields.push({
        line: "hostname := <str>$hostname",
        argName: "hostname",
        value: rec.hostname,
      });
    }
  } else if (rec.kind === "storage") {
    if (rec.volume_gb !== undefined) {
      fields.push({
        line: "volume_gb := <int64>$volume_gb",
        argName: "volume_gb",
        value: rec.volume_gb,
      });
    }
  }

  const args: Record<string, unknown> = {};
  for (const f of fields) args[f.argName] = f.value;

  const query = `select (insert ${typeName} {\n  ${fields
    .map((f) => f.line)
    .join(",\n  ")}\n}) { id };`;

  try {
    await client.query(query, args);
    return { ...base, status: "inserted", reason: null, error_class: null };
  } catch (err) {
    if (err instanceof ConstraintViolationError) {
      const reason = (err as unknown as { _message: string })._message;
      return {
        ...base,
        status: "rejected",
        reason,
        error_class: "ConstraintViolationError",
      };
    }
    if (err instanceof MissingRequiredError) {
      return {
        ...base,
        status: "rejected",
        reason: "MISSING_REQUIRED",
        error_class: "MissingRequiredError",
      };
    }
    throw err;
  }
}
