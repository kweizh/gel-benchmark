import e, { createClient } from "./dbschema/edgeql-js";
import { DESC } from "./dbschema/edgeql-js/select";

const client = createClient();

async function main() {
  // 1. free object select with counts
  const counts = await e.select({
    articles: e.count(e.Article),
    videos: e.count(e.Video),
    total: e.count(e.Resource),
  }).run(client);
  console.log("COUNTS", JSON.stringify(counts));

  // 2. author with computed counts and type intersection
  const authors = await e.select(e.Author, (a) => ({
    name: a.name,
    articles: e.count(a.resources.is(e.Article)),
    videos: e.count(a.resources.is(e.Video)),
    total_minutes: e.op(e.sum(a.resources.minutes), "??", e.int64(0)),
    resource_count: a.resource_count,
  })).run(client);
  console.log("AUTHORS", JSON.stringify(authors));

  // 3. top_title via nested select chained .title
  const withTop = await e.select(e.Author, (a) => ({
    name: a.name,
    top_title: e.select(a.resources, (r) => ({
      title: r.title,
      order_by: [{ expression: r.minutes, direction: DESC }, { expression: r.title }],
      limit: 1,
    })).title,
  })).run(client);
  console.log("WITHTOP", JSON.stringify(withTop));

  // 4. insert with unlessConflict
  const ins = await e.insert(e.Article, {
    title: "Scratch Test Article",
    minutes: 5,
    level: "beginner",
    word_count: 100,
    author: e.select(e.Author, () => ({ filter_single: { name: "Ada Lovelace" } })),
  }).unlessConflict((art) => ({ on: art.title })).run(client);
  console.log("INS", JSON.stringify(ins));

  // 5. distinct levels
  const levels = await e.select(e.Resource.level).run(client);
  console.log("LEVELS", JSON.stringify(levels));

  // 6. group by level
  const grouped = await e.group(e.Resource, (r) => ({
    level: r.level,
  })).run(client);
  console.log("GROUPED", JSON.stringify(grouped));

  // cleanup
  await e.delete(e.Article, (art) => ({ filter: e.op(art.title, "=", "Scratch Test Article") })).run(client);
  console.log("DONE");
}

main().catch((err) => { console.error("ERR", err); process.exit(1); });
