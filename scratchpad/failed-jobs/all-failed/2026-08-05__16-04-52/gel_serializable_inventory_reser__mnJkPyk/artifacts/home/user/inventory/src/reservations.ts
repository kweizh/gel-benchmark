import { createClient, type Client } from "gel";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BasketLine {
  sku: string;
  quantity: number;
}

export interface ReserveRequest {
  idempotencyKey: string;
  basket: BasketLine[];
  expiresAt: string | null;
}

export interface ReserveOutcomeSuccess {
  status: "reserved";
  reservationId: string;
  idempotent: boolean;
}

export interface ReserveOutcomeRejection {
  status: "rejected";
  reason: string;
  details: string[];
}

export type ReserveOutcome = ReserveOutcomeSuccess | ReserveOutcomeRejection;

export interface ReleaseOutcomeSuccess {
  status: "released";
  reservationId: string;
}

export interface ReleaseOutcomeRejection {
  status: "rejected";
  reason: string;
  details: string[];
}

export type ReleaseOutcome = ReleaseOutcomeSuccess | ReleaseOutcomeRejection;

export interface StockItemDef {
  sku: string;
  stock: number;
}

export interface SnapshotItem {
  sku: string;
  stock: number;
  reserved: number;
  available: number;
}

export interface SnapshotReservationLine {
  sku: string;
  quantity: number;
}

export interface SnapshotReservation {
  reservationId: string;
  idempotencyKey: string;
  state: "active" | "released";
  lines: SnapshotReservationLine[];
}

export interface SnapshotLedgerEntry {
  reservationId: string;
  sku: string;
  kind: "reserve" | "release";
  delta: number;
}

export interface Snapshot {
  items: SnapshotItem[];
  reservations: SnapshotReservation[];
  ledger: SnapshotLedgerEntry[];
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

const RETRY_ATTEMPTS = 16;

function getClient(): Client {
  return createClient().withRetryOptions({ attempts: RETRY_ATTEMPTS });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isValidUUID(str: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    str
  );
}

function validateReserveRequest(
  req: ReserveRequest
): ReserveOutcomeRejection | null {
  if (!req.idempotencyKey || req.idempotencyKey.length === 0) {
    return {
      status: "rejected",
      reason: "INVALID_REQUEST",
      details: ["idempotencyKey is empty"],
    };
  }
  if (!req.basket || req.basket.length === 0) {
    return {
      status: "rejected",
      reason: "INVALID_REQUEST",
      details: ["basket is empty"],
    };
  }
  const seen = new Set<string>();
  for (const line of req.basket) {
    if (!line.sku || line.sku.length === 0) {
      return {
        status: "rejected",
        reason: "INVALID_REQUEST",
        details: ["basket contains empty sku"],
      };
    }
    if (seen.has(line.sku)) {
      return {
        status: "rejected",
        reason: "INVALID_REQUEST",
        details: ["duplicate sku in basket"],
      };
    }
    seen.add(line.sku);
    if (
      typeof line.quantity !== "number" ||
      !Number.isInteger(line.quantity) ||
      line.quantity < 1
    ) {
      return {
        status: "rejected",
        reason: "INVALID_REQUEST",
        details: ["quantity must be a positive integer"],
      };
    }
  }
  return null;
}

function basketsEqual(a: BasketLine[], b: BasketLine[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort((x, y) => x.sku.localeCompare(y.sku));
  const sortedB = [...b].sort((x, y) => x.sku.localeCompare(y.sku));
  for (let i = 0; i < sortedA.length; i++) {
    if (sortedA[i].sku !== sortedB[i].sku) return false;
    if (sortedA[i].quantity !== sortedB[i].quantity) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Exported functions
// ---------------------------------------------------------------------------

export async function resetCatalog(items: StockItemDef[]): Promise<{ ok: true }> {
  const client = getClient();
  try {
    await client.execute(`
      DELETE LedgerEntry;
      DELETE ReservationLine;
      DELETE Reservation;
      DELETE StockItem;
    `);
    for (const item of items) {
      await client.execute(
        `INSERT StockItem { sku := <str>$sku, stock := <int64>$stock, reserved := 0 }`,
        { sku: item.sku, stock: item.stock }
      );
    }
    return { ok: true };
  } finally {
    await client.close();
  }
}

export async function reserve(req: ReserveRequest): Promise<ReserveOutcome> {
  const validation = validateReserveRequest(req);
  if (validation) return validation;

  const client = getClient();
  try {
    return await client.transaction(async (tx) => {
      // Rule 2: Check for existing reservation by idempotency key
      const existingRes = await tx.querySingle<{ id: string; state: string }>(
        `SELECT Reservation { id, state } FILTER .key = <str>$key`,
        { key: req.idempotencyKey }
      );

      if (existingRes) {
        // Get existing lines
        const existingLines = await tx.query<{ sku: string; quantity: number }>(
          `SELECT ReservationLine {
            sku := .item.sku,
            quantity
          } FILTER .reservation.id = <uuid>$id`,
          { id: existingRes.id }
        );

        const existingBasket: BasketLine[] = existingLines.map((l) => ({
          sku: l.sku,
          quantity: Number(l.quantity),
        }));

        if (basketsEqual(existingBasket, req.basket)) {
          return {
            status: "reserved",
            reservationId: existingRes.id,
            idempotent: true,
          } as ReserveOutcomeSuccess;
        }
        return {
          status: "rejected",
          reason: "IDEMPOTENCY_KEY_CONFLICT",
          details: [],
        } as ReserveOutcomeRejection;
      }

      // Rule 3: Check for unknown SKUs
      const skus = req.basket.map((l) => l.sku);
      const existingItems = await tx.query<{ sku: string; stock: number; reserved: number }>(
        `SELECT StockItem { sku, stock, reserved } FILTER .sku IN array_unpack(<array<str>>$skus)`,
        { skus }
      );
      const existingSkus = new Set(existingItems.map((i) => i.sku));
      const unknownSkus = skus.filter((s) => !existingSkus.has(s));
      if (unknownSkus.length > 0) {
        return {
          status: "rejected",
          reason: "UNKNOWN_SKU",
          details: unknownSkus.sort(),
        } as ReserveOutcomeRejection;
      }

      // Rule 4: Check for insufficient stock
      const stockMap = new Map<string, { stock: number; reserved: number }>();
      for (const item of existingItems) {
        stockMap.set(item.sku, { stock: Number(item.stock), reserved: Number(item.reserved) });
      }
      const insufficientSkus: string[] = [];
      for (const line of req.basket) {
        const s = stockMap.get(line.sku)!;
        if (line.quantity > s.stock - s.reserved) {
          insufficientSkus.push(line.sku);
        }
      }
      if (insufficientSkus.length > 0) {
        return {
          status: "rejected",
          reason: "INSUFFICIENT_STOCK",
          details: insufficientSkus.sort(),
        } as ReserveOutcomeRejection;
      }

      // Create reservation
      const expiresAtExpr = req.expiresAt
        ? `<datetime>$expiresAt`
        : `<datetime>{}`;
      const insertParams: Record<string, any> = { key: req.idempotencyKey };
      if (req.expiresAt) {
        insertParams.expiresAt = new Date(req.expiresAt);
      }

      const reservation = await tx.queryRequiredSingle<{ id: string }>(
        `INSERT Reservation {
          key := <str>$key,
          state := 'active',
          expires_at := ${expiresAtExpr}
        }`,
        insertParams
      );

      // Create reservation lines and ledger entries, update reserved
      for (const line of req.basket) {
        // Insert ReservationLine
        await tx.execute(
          `INSERT ReservationLine {
            reservation := (SELECT Reservation FILTER .id = <uuid>$resId),
            item := (SELECT StockItem FILTER .sku = <str>$sku),
            quantity := <int64>$qty
          }`,
          { resId: reservation.id, sku: line.sku, qty: line.quantity }
        );
        // Insert LedgerEntry
        await tx.execute(
          `INSERT LedgerEntry {
            reservation := (SELECT Reservation FILTER .id = <uuid>$resId),
            item := (SELECT StockItem FILTER .sku = <str>$sku),
            delta := <int64>$delta,
            kind := 'reserve'
          }`,
          {
            resId: reservation.id,
            sku: line.sku,
            delta: -line.quantity,
          }
        );
        // Update reserved on StockItem
        await tx.execute(
          `UPDATE StockItem FILTER .sku = <str>$sku
           SET { reserved := .reserved + <int64>$qty }`,
          { sku: line.sku, qty: line.quantity }
        );
      }

      return {
        status: "reserved",
        reservationId: reservation.id,
        idempotent: false,
      } as ReserveOutcomeSuccess;
    });
  } finally {
    await client.close();
  }
}

export async function reserveMany(
  requests: ReserveRequest[]
): Promise<{ outcomes: ReserveOutcome[] }> {
  const results = await Promise.all(requests.map((r) => reserve(r)));
  return { outcomes: results };
}

export async function release(reservationId: string): Promise<ReleaseOutcome> {
  if (!isValidUUID(reservationId)) {
    return {
      status: "rejected",
      reason: "UNKNOWN_RESERVATION",
      details: [],
    };
  }

  const client = getClient();
  try {
    return await client.transaction(async (tx) => {
      const reservation = await tx.querySingle<{ id: string; state: string }>(
        `SELECT Reservation { id, state } FILTER .id = <uuid>$id`,
        { id: reservationId }
      );

      if (!reservation) {
        return {
          status: "rejected",
          reason: "UNKNOWN_RESERVATION",
          details: [],
        } as ReleaseOutcomeRejection;
      }

      if (reservation.state === "released") {
        return {
          status: "rejected",
          reason: "ALREADY_RELEASED",
          details: [],
        } as ReleaseOutcomeRejection;
      }

      // Get the lines
      const lines = await tx.query<{ sku: string; quantity: number }>(
        `SELECT ReservationLine {
          sku := .item.sku,
          quantity
        } FILTER .reservation.id = <uuid>$id`,
        { id: reservationId }
      );

      // Update reservation state
      await tx.execute(
        `UPDATE Reservation FILTER .id = <uuid>$id SET { state := 'released' }`,
        { id: reservationId }
      );

      // Release stock and create ledger entries
      for (const line of lines) {
        await tx.execute(
          `INSERT LedgerEntry {
            reservation := (SELECT Reservation FILTER .id = <uuid>$resId),
            item := (SELECT StockItem FILTER .sku = <str>$sku),
            delta := <int64>$delta,
            kind := 'release'
          }`,
          {
            resId: reservationId,
            sku: line.sku,
            delta: Number(line.quantity),
          }
        );
        await tx.execute(
          `UPDATE StockItem FILTER .sku = <str>$sku
           SET { reserved := .reserved - <int64>$qty }`,
          { sku: line.sku, qty: Number(line.quantity) }
        );
      }

      return {
        status: "released",
        reservationId: reservation.id,
      } as ReleaseOutcomeSuccess;
    });
  } finally {
    await client.close();
  }
}

export async function expireDue(now: string): Promise<{ released: string[] }> {
  const client = getClient();
  try {
    return await client.transaction(async (tx) => {
      // Find active reservations with expires_at <= now
      const due = await tx.query<{ id: string }>(
        `SELECT Reservation { id }
         FILTER .state = 'active'
         AND EXISTS .expires_at
         AND .expires_at <= <datetime>$now`,
        { now: new Date(now) }
      );

      const released: string[] = [];

      for (const res of due) {
        // Get lines
        const lines = await tx.query<{ sku: string; quantity: number }>(
          `SELECT ReservationLine {
            sku := .item.sku,
            quantity
          } FILTER .reservation.id = <uuid>$id`,
          { id: res.id }
        );

        // Update reservation state
        await tx.execute(
          `UPDATE Reservation FILTER .id = <uuid>$id SET { state := 'released' }`,
          { id: res.id }
        );

        // Release stock and create ledger entries
        for (const line of lines) {
          await tx.execute(
            `INSERT LedgerEntry {
              reservation := (SELECT Reservation FILTER .id = <uuid>$resId),
              item := (SELECT StockItem FILTER .sku = <str>$sku),
              delta := <int64>$delta,
              kind := 'release'
            }`,
            {
              resId: res.id,
              sku: line.sku,
              delta: Number(line.quantity),
            }
          );
          await tx.execute(
            `UPDATE StockItem FILTER .sku = <str>$sku
             SET { reserved := .reserved - <int64>$qty }`,
            { sku: line.sku, qty: Number(line.quantity) }
          );
        }

        released.push(res.id);
      }

      released.sort();
      return { released };
    });
  } finally {
    await client.close();
  }
}

export async function snapshot(): Promise<Snapshot> {
  const client = getClient();
  try {
    const items = await client.query<{
      sku: string;
      stock: number;
      reserved: number;
    }>(`SELECT StockItem { sku, stock, reserved } ORDER BY .sku`);

    const reservationsRaw = await client.query<{
      id: string;
      key: string;
      state: string;
    }>(`SELECT Reservation { id, key, state } ORDER BY .id`);

    // Get all reservation lines
    const allLines = await client.query<{
      reservationId: string;
      sku: string;
      quantity: number;
    }>(
      `SELECT ReservationLine {
        reservationId := .reservation.id,
        sku := .item.sku,
        quantity
      }`
    );

    // Group lines by reservation
    const linesByRes = new Map<string, { sku: string; quantity: number }[]>();
    for (const line of allLines) {
      const list = linesByRes.get(line.reservationId) || [];
      list.push({ sku: line.sku, quantity: Number(line.quantity) });
      linesByRes.set(line.reservationId, list);
    }

    const ledgerRaw = await client.query<{
      reservationId: string;
      sku: string;
      kind: string;
      delta: number;
    }>(
      `SELECT LedgerEntry {
        reservationId := .reservation.id,
        sku := .item.sku,
        kind,
        delta
      }`
    );

    return {
      items: items.map((i) => ({
        sku: i.sku,
        stock: Number(i.stock),
        reserved: Number(i.reserved),
        available: Number(i.stock) - Number(i.reserved),
      })),
      reservations: reservationsRaw.map((r) => {
        const lines = linesByRes.get(r.id) || [];
        lines.sort((a, b) => a.sku.localeCompare(b.sku));
        return {
          reservationId: r.id,
          idempotencyKey: r.key,
          state: r.state as "active" | "released",
          lines,
        };
      }),
      ledger: ledgerRaw.map((e) => ({
        reservationId: e.reservationId,
        sku: e.sku,
        kind: e.kind as "reserve" | "release",
        delta: Number(e.delta),
      })),
    };
  } finally {
    await client.close();
  }
}

export function getRetryAttempts(): number {
  return RETRY_ATTEMPTS;
}
