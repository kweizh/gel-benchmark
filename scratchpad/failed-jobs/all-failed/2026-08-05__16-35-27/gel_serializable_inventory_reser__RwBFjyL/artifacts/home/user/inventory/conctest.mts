import { createClient, IsolationLevel } from "gel";

const client = createClient({
  dsn: "gel://admin@127.0.0.1:5656/probe",
  tlsSecurity: "insecure",
}).withTransactionOptions({ isolation: IsolationLevel.Serializable });

async function tryInsert(label: string, key: string) {
  try {
    const r = await client.transaction(async (tx) => {
      const existing = await tx.querySingle(
        `select default::Reservation { id } filter .key = <str>$key`, { key }
      );
      if (existing) return { kind: "replay", id: existing.id };
      await tx.querySingle(`select count(default::StockItem)`);
      return await tx.querySingle(
        `insert default::Reservation { key := <str>$key, state := default::State.active }`, { key }
      );
    });
    return r.kind === "replay" ? "replay" : "success";
  } catch (err: any) {
    return "ERR:" + err.constructor?.name;
  }
}

let counts: Record<string, number> = {};
for (let i = 0; i < 30; i++) {
  const key = `ct-${i}`;
  await client.execute(`delete default::Reservation filter .key = <str>$key`, { key });
  const [a, b] = await Promise.all([tryInsert("A", key), tryInsert("B", key)]);
  const sig = `${a}+${b}`;
  counts[sig] = (counts[sig] || 0) + 1;
}
console.log(JSON.stringify(counts));
await client.close();
