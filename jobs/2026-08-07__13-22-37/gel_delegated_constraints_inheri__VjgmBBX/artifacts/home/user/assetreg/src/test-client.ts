import * as gel from "gel";

async function main() {
  const client = gel.createClient();
  // Test BigInt int64 params and str params
  const res = await client.query(
    `insert ServerAsset {
      code := <str>$code,
      serial := <str>$serial,
      region := (select Region filter .key = <str>$region),
      slot := <int64>$slot,
      capacity := <int64>$capacity,
      reserved := <int64>$reserved,
      revision := <int64>$revision,
      tags := unnest(<array<str>>$tags),
      hostname := <str>$hostname
    }`,
    {
      code: "TEST01",
      serial: "SN-TEST-01",
      region: "EU_WEST",
      slot: BigInt(99),
      capacity: BigInt(10),
      reserved: BigInt(2),
      revision: BigInt(1),
      tags: ["WEB", "DB"],
      hostname: "host-test",
    }
  );
  console.log("insert result:", JSON.stringify(res));

  // Test constraint violation error
  try {
    await client.query(
      `insert ServerAsset {
        code := <str>$code,
        serial := <str>$serial,
        region := (select Region filter .key = <str>$region),
        slot := <int64>$slot,
        capacity := <int64>$capacity,
        reserved := <int64>$reserved,
        revision := <int64>$revision,
        hostname := <str>$hostname
      }`,
      {
        code: "bad-code",
        serial: "SN-TEST-02",
        region: "EU_WEST",
        slot: BigInt(98),
        capacity: BigInt(10),
        reserved: BigInt(2),
        revision: BigInt(1),
        hostname: "host-test2",
      }
    );
  } catch (e: any) {
    console.log("error name:", e.name);
    console.log("error _message:", (e as any)._message);
    console.log("error message:", e.message);
    console.log("is ConstraintViolationError:", e instanceof gel.ConstraintViolationError);
    console.log("is MissingRequiredError:", e instanceof gel.MissingRequiredError);
  }

  // Test missing required error
  try {
    await client.query(
      `insert ServerAsset {
        code := <str>$code,
        serial := <str>$serial,
        region := (select Region filter .key = <str>$region),
        slot := <int64>$slot,
        capacity := <int64>$capacity,
        reserved := <int64>$reserved,
        revision := <int64>$revision
      }`,
      {
        code: "TEST02",
        serial: "SN-TEST-03",
        region: "EU_WEST",
        slot: BigInt(97),
        capacity: BigInt(10),
        reserved: BigInt(2),
        revision: BigInt(1),
      }
    );
  } catch (e: any) {
    console.log("missing error name:", e.name);
    console.log("missing error _message:", (e as any)._message);
    console.log("missing is MissingRequiredError:", e instanceof gel.MissingRequiredError);
    console.log("missing is ConstraintViolationError:", e instanceof gel.ConstraintViolationError);
  }

  await client.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
