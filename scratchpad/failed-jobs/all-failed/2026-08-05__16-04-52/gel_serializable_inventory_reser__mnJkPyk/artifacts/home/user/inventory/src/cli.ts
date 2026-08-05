import {
  resetCatalog,
  reserve,
  reserveMany,
  release,
  expireDue,
  snapshot,
  getRetryAttempts,
} from "./reservations";

async function main(): Promise<void> {
  let input = "";
  process.stdin.setEncoding("utf-8");

  for await (const chunk of process.stdin) {
    input += chunk;
  }

  let command: any;
  try {
    command = JSON.parse(input);
  } catch {
    process.stderr.write("invalid json\n");
    process.exit(1);
  }

  try {
    let result: any;

    switch (command.op) {
      case "reset": {
        result = await resetCatalog(command.items);
        break;
      }
      case "reserve": {
        result = await reserve(command.request);
        break;
      }
      case "reserveMany": {
        result = await reserveMany(command.requests);
        break;
      }
      case "release": {
        result = await release(command.reservationId);
        break;
      }
      case "expire": {
        result = await expireDue(command.now);
        break;
      }
      case "snapshot": {
        result = await snapshot();
        break;
      }
      case "retryAttempts": {
        result = { attempts: getRetryAttempts() };
        break;
      }
      default: {
        process.stderr.write(`unknown op: ${command.op}\n`);
        process.exit(1);
      }
    }

    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(0);
  } catch (err: any) {
    process.stderr.write(`error: ${err?.message ?? String(err)}\n`);
    process.exit(1);
  }
}

main();
