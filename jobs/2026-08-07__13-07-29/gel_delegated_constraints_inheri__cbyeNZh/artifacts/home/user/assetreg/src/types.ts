export interface AssetRecord {
  seq: number;
  kind: string;
  code: string;
  serial: string;
  region: string;
  slot: number;
  capacity: number;
  reserved: number;
  revision: number;
  tags?: string[];
  hostname?: string;
  volume_gb?: number;
}

export interface Manifest {
  regions: string[];
  assets: AssetRecord[];
}

export type Status = "inserted" | "rejected";

export interface ResultEntry {
  seq: number;
  kind: string;
  serial: string;
  status: Status;
  reason: string | null;
  error_class: string | null;
}

export interface SchemaReport {
  abstract_constraints: string[];
  delegated_pointer_constraints: string[];
  delegated_object_constraints: string[];
}

export interface Report {
  total: number;
  inserted: number;
  rejected: number;
  results: ResultEntry[];
  reason_counts: Record<string, number>;
  schema: SchemaReport;
}
