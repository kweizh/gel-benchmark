export interface RawRecordShape {
  sku: string;
  name: string;
  price_cents: number;
  stock: number;
  category: string;
  tags: string[];
}

export type Outcome = "inserted" | "updated" | "unchanged";

export interface RecordResult {
  sku: string;
  outcome: Outcome;
  category: string;
  tags: string[];
}

export interface SuccessOutput {
  ok: true;
  total: number;
  inserted: number;
  updated: number;
  unchanged: number;
  results: RecordResult[];
}

export type ErrorCode =
  | "input_unreadable"
  | "invalid_record"
  | "duplicate_sku"
  | "db_error";

export interface FailureOutput {
  ok: false;
  error_code: ErrorCode;
  message: string;
  index?: number | null;
  sku?: string;
}

/**
 * Thrown to signal a well-classified CLI failure. Carries the exit code
 * and the exact shape of the JSON document that must be printed to stdout.
 */
export class CliError extends Error {
  readonly exitCode: number;
  readonly errorCode: ErrorCode;
  readonly index?: number | null;
  readonly sku?: string;

  constructor(
    exitCode: number,
    errorCode: ErrorCode,
    message: string,
    extra?: { index?: number | null; sku?: string },
  ) {
    super(message);
    this.exitCode = exitCode;
    this.errorCode = errorCode;
    this.index = extra?.index;
    this.sku = extra?.sku;
  }

  toOutput(): FailureOutput {
    const out: FailureOutput = {
      ok: false,
      error_code: this.errorCode,
      message: this.message,
    };
    if (this.index !== undefined) {
      out.index = this.index;
    }
    if (this.sku !== undefined) {
      out.sku = this.sku;
    }
    return out;
  }
}

export interface ExistingProduct {
  sku: string;
  name: string;
  price_cents: number;
  stock: number;
  category_name: string;
  tag_labels: string[];
}
