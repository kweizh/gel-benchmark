import { createClient, IsolationLevel } from "gel";

const client = createClient({
  dsn: "gel://admin@127.0.0.1:5656/probe",
  tlsSecurity: "insecure",
}).withTransactionOptions({ isolation: IsolationLevel.Serializable });

await client.execute(`delete default::ReservationLine`);
await client.execute(`delete default::LedgerEntry`);
await client.execute(`delete default::Reservation`);
await client.execute(`delete default::StockItem`);
await client.execute(`insert default::StockItem { sku := 'A', stock := 1, reserved := 0 }`);

async function reserve(label: string, key: string) {
  try {
    const r = await client.transaction(async (tx) => {
      const existing = await tx.querySingle(
        `select default::Reservation { id } filter .key = <str>$key`, { key }
      );
      if (existing) return { kind: "replay" };
      const item = await tx.queryRequiredSingle(
        `select default::StockItem { stock, reserved } filter .sku = 'A'`
      );
      if (item.reserved + 1 > item.stock) return { kind: "insufficient" };
      const res = await tx.querySingle(
        `insert default::Reservation { key := <str>$key, state := default::State.active }`, { key }
      );
      await tx.execute(`update default::StockItem filter .sku = 'A' set { reserved := .reserved + 1 }`);
      await tx.execute(`
        insert default::ReservationLine {
          reservation := (select default::Reservation filter .id = <uuid>$rid),
          item := (select default::StockItem filter .sku = 'A'),
          quantity := 1
        }`, { rid: res.id });
      return { kind: "success" };
    });
    return r.kind;
  } catch (err: any) {
    return "ERR:" + err.constructor?.name;
  }
}

let counts: Record<string, number> = {};
for (let i = 0; i < 30; i++) {
  await client.execute(`update default::StockItem filter .sku='A' set { reserved := 0 }`);
  await client.execute(`delete default::ReservationLine`);
  await client.execute(`delete default::Reservation`);
  const [a, b] = await Promise.all([reserve("A", `k-${i}-a`), reserve("B", `k-${i}-b`)]);
  const sig = `${a}+${b}`;
  counts[sig] = (counts[sig] || 0) + 1;
}
console.log(JSON.stringify(counts, null, 2));
const item = await client.querySingle(`select default::StockItem { stock, reserved } filter .sku='A'`);
console.log("final item:", JSON.stringify(item));
await client.close();
