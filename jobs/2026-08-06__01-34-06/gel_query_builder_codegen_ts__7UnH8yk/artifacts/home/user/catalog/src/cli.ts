import * as fs from "node:fs";
import * as path from "node:path";
import { createClient } from "gel";
import e, { type $infer } from "../dbschema/edgeql-js";

const client = createClient();

interface ResourceEntry {
  kind: "article" | "video";
  title: string;
  author: string;
  minutes: number;
  level: string;
  word_count?: number;
  has_captions?: boolean;
}

function loadResources(): ResourceEntry[] {
  const raw = fs.readFileSync(
    path.join(__dirname, "..", "data", "resources.json"),
    "utf-8",
  );
  return JSON.parse(raw) as ResourceEntry[];
}

async function cmdLoad(): Promise<void> {
  const entries = loadResources();

  for (const entry of entries) {
    const authorQuery = e.select(e.Author, (author) => ({
      filter_single: e.op(author.name, "=", entry.author),
    }));

    if (entry.kind === "article") {
      const insertQuery = e
        .insert(e.Article, {
          title: entry.title,
          minutes: entry.minutes,
          level: entry.level,
          author: authorQuery,
          word_count: entry.word_count!,
        })
        .unlessConflict();
      await insertQuery.run(client);
    } else {
      const insertQuery = e
        .insert(e.Video, {
          title: entry.title,
          minutes: entry.minutes,
          level: entry.level,
          author: authorQuery,
          has_captions: entry.has_captions!,
        })
        .unlessConflict();
      await insertQuery.run(client);
    }
  }

  const articleCount = await e
    .select(e.Article, () => ({ n: e.count(e.Article) }))
    .run(client);
  const videoCount = await e
    .select(e.Video, () => ({ n: e.count(e.Video) }))
    .run(client);
  const totalCount = await e
    .select(e.Resource, () => ({ n: e.count(e.Resource) }))
    .run(client);

  const result = {
    articles: articleCount.n,
    videos: videoCount.n,
    total: totalCount.n,
  };
  process.stdout.write(JSON.stringify(result));
}

async function cmdReportAuthors(): Promise<void> {
  const result = await e
    .select(e.Author, (author) => ({
      name: true,
      articles: e.count(author["<author[is Article]"]),
      videos: e.count(author["<author[is Video]"]),
      total_minutes: e.sum(author.resources.minutes),
      avg_minutes: e.op(
        e.op(
          e.sum(author.resources.minutes),
          "/",
          e.cast(e.float64, author.resource_count),
        ),
        "if",
        e.op(author.resource_count, "=", 0),
        "else",
        e.round(
          e.op(
            e.sum(author.resources.minutes),
            "/",
            e.cast(e.float64, author.resource_count),
          ),
          2,
        ),
      ),
      top_title: e.assert_exists(
        e.select(author.resources, (r) => ({
          title: true,
          order_by: [
            { expression: r.minutes, direction: e.DESC },
            { expression: r.title, direction: e.ASC },
          ],
          limit: 1,
        })),
      ).title,
      order_by: [
        { expression: e.sum(author.resources.minutes), direction: e.DESC },
        { expression: author.name, direction: e.ASC },
      ],
    }))
    .run(client);

  const output = result.map((row: Record<string, unknown>) => ({
    name: row.name,
    articles: Number(row.articles),
    videos: Number(row.videos),
    total_minutes: Number(row.total_minutes ?? 0),
    avg_minutes: row.avg_minutes as number,
    top_title: row.top_title ?? null,
  }));

  process.stdout.write(JSON.stringify(output));
}

async function cmdReportLevels(): Promise<void> {
  const result = await e
    .select(e.Resource, (r) => ({
      level: true,
    }))
    .run(client);

  const distinctLevels = [
    ...new Set(result.map((r: { level: string }) => r.level)),
  ].sort();

  const output = [];
  for (const level of distinctLevels) {
    const bucket = await e
      .select(e.Resource, (r) => ({
        filter: e.op(r.level, "=", level),
        count: e.count(r),
        articles: e.count(e.select(r, () => ({ filter: e.op(r.__type__.name, "=", "default::Article") }))),
        videos: e.count(e.select(r, () => ({ filter: e.op(r.__type__.name, "=", "default::Video") }))),
        total_minutes: e.sum(r.minutes),
        total_words: e.sum(
          e.op(
            e.select(r, () => ({ filter: e.op(r.__type__.name, "=", "default::Article") })),
            "if",
            e.cast(e.int64, 0),
            "else",
            e.cast(e.int64, 0),
          ),
        ),
        captioned_videos: e.count(
          e.select(r, (inner) => ({
            filter: e.op(
              e.op(inner.__type__.name, "=", "default::Video"),
              "and",
              e.op(inner.has_captions, "=", true),
            ),
          })),
        ),
      }))
      .run(client);

    const row = bucket as unknown as {
      count: number;
      articles: number;
      videos: number;
      total_minutes: number | null;
      total_words: number | null;
      captioned_videos: number;
    };

    output.push({
      level,
      count: Number(row.count),
      articles: Number(row.articles),
      videos: Number(row.videos),
      total_minutes: Number(row.total_minutes ?? 0),
      total_words: Number(row.total_words ?? 0),
      captioned_videos: Number(row.captioned_videos),
    });
  }

  process.stdout.write(JSON.stringify(output));
}

async function cmdReportAuthor(name: string): Promise<void> {
  const result = await e
    .select(e.Author, (author) => ({
      filter_single: e.op(author.name, "=", name),
      name: true,
      country: true,
      resource_count: true,
      total_minutes: e.sum(author.resources.minutes),
      titles: e.select(author.resources, (r) => ({
        title: true,
        order_by: { expression: r.title, direction: e.ASC },
      })),
    }))
    .run(client);

  if (result === null) {
    process.stderr.write(`author not found: ${name}\n`);
    process.exit(3);
  }

  const row = result as {
    name: string;
    country: string;
    resource_count: number;
    total_minutes: number | null;
    titles: { title: string }[];
  };

  const output = {
    name: row.name,
    country: row.country,
    resource_count: Number(row.resource_count),
    total_minutes: Number(row.total_minutes ?? 0),
    titles: row.titles.map((t: { title: string }) => t.title),
  };

  process.stdout.write(JSON.stringify(output));
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);

  if (args.length === 0) {
    process.stderr.write("catalog: no subcommand specified\n");
    process.exit(2);
  }

  const subcommand = args[0];

  if (subcommand === "load") {
    await cmdLoad();
  } else if (subcommand === "report") {
    const reportType = args[1];
    if (reportType === "authors") {
      await cmdReportAuthors();
    } else if (reportType === "levels") {
      await cmdReportLevels();
    } else if (reportType === "author") {
      const nameIdx = args.indexOf("--name");
      if (nameIdx === -1 || nameIdx + 1 >= args.length) {
        process.stderr.write("catalog: report author requires --name <name>\n");
        process.exit(2);
      }
      const name = args[nameIdx + 1];
      if (!name) {
        process.stderr.write("catalog: report author requires --name <name>\n");
        process.exit(2);
      }
      await cmdReportAuthor(name);
    } else {
      process.stderr.write(`catalog: unknown report type: ${reportType}\n`);
      process.exit(2);
    }
  } else {
    process.stderr.write(`catalog: unknown subcommand: ${subcommand}\n`);
    process.exit(2);
  }
}

main().catch((err: Error) => {
  process.stderr.write(`catalog: ${err.message}\n`);
  process.exit(1);
});
