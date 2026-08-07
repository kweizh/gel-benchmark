import { createClient } from "gel";
import e from "../dbschema/edgeql-js";
import * as fs from "fs";
import * as path from "path";

interface ResourceEntry {
  kind: "article" | "video";
  title: string;
  author: string;
  minutes: number;
  level: string;
  word_count?: number;
  has_captions?: boolean;
}

async function load() {
  const client = createClient();

  // 1. Get existing resource titles
  const existingResources = await e.select(e.Resource, () => ({
    title: true,
  })).run(client);
  const existingTitles = new Set(existingResources.map((r) => r.title));

  // 2. Read resources.json
  const resourcesPath = path.join(process.cwd(), "data/resources.json");
  const resources: ResourceEntry[] = JSON.parse(fs.readFileSync(resourcesPath, "utf-8"));

  // 3. Insert non-existing ones
  for (const entry of resources) {
    if (existingTitles.has(entry.title)) {
      continue;
    }

    const authorQuery = e.select(e.Author, (author) => ({
      filter_single: e.op(author.name, "=", e.str(entry.author)),
    }));

    if (entry.kind === "article") {
      const wordCount = entry.word_count ?? 0;
      await e.insert(e.Article, {
        title: e.str(entry.title),
        minutes: e.int64(entry.minutes),
        level: e.str(entry.level),
        word_count: e.int64(wordCount),
        author: authorQuery,
      }).run(client);
    } else if (entry.kind === "video") {
      const hasCaptions = entry.has_captions ?? false;
      await e.insert(e.Video, {
        title: e.str(entry.title),
        minutes: e.int64(entry.minutes),
        level: e.str(entry.level),
        has_captions: e.bool(hasCaptions),
        author: authorQuery,
      }).run(client);
    }
  }

  // 4. Print counts
  const counts = await e.select({
    articles: e.count(e.Article),
    videos: e.count(e.Video),
    total: e.count(e.Resource),
  }).run(client);

  console.log(JSON.stringify(counts));
}

async function reportAuthors() {
  const client = createClient();

  const authors = await e.select(e.Author, (author) => ({
    name: true,
    resources: {
      title: true,
      minutes: true,
    },
    articles: author["<author[is Article]"],
    videos: author["<author[is Video]"],
  })).run(client);

  const result = authors.map((author) => {
    const totalMinutes = author.resources.reduce((sum, r) => sum + Number(r.minutes), 0);
    const resourceCount = author.resources.length;
    const avgMinutes = resourceCount > 0 ? Number((totalMinutes / resourceCount).toFixed(2)) : 0;

    let topTitle: string | null = null;
    if (resourceCount > 0) {
      const sortedResources = [...author.resources].sort((a, b) => {
        const minA = Number(a.minutes);
        const minB = Number(b.minutes);
        if (minA !== minB) {
          return minB - minA; // descending by minutes
        }
        return a.title.localeCompare(b.title); // ascending by title
      });
      topTitle = sortedResources[0].title;
    }

    return {
      name: author.name,
      articles: author.articles.length,
      videos: author.videos.length,
      total_minutes: totalMinutes,
      avg_minutes: avgMinutes,
      top_title: topTitle,
    };
  });

  // Sort: total_minutes descending, then name ascending
  result.sort((a, b) => {
    if (a.total_minutes !== b.total_minutes) {
      return b.total_minutes - a.total_minutes;
    }
    return a.name.localeCompare(b.name);
  });

  console.log(JSON.stringify(result));
}

async function reportLevels() {
  const client = createClient();

  const resources = await e.select(e.Resource, () => ({
    level: true,
    minutes: true,
    ...e.is(e.Article, {
      word_count: true,
    }),
    ...e.is(e.Video, {
      has_captions: true,
    }),
  })).run(client);

  const levelBuckets: Record<string, {
    level: string;
    count: number;
    articles: number;
    videos: number;
    total_minutes: number;
    total_words: number;
    captioned_videos: number;
  }> = {};

  for (const r of resources) {
    const lvl = r.level;
    if (!levelBuckets[lvl]) {
      levelBuckets[lvl] = {
        level: lvl,
        count: 0,
        articles: 0,
        videos: 0,
        total_minutes: 0,
        total_words: 0,
        captioned_videos: 0,
      };
    }

    const bucket = levelBuckets[lvl];
    bucket.count += 1;
    bucket.total_minutes += Number(r.minutes);

    if ("word_count" in r && r.word_count !== null && r.word_count !== undefined) {
      bucket.articles += 1;
      bucket.total_words += Number(r.word_count);
    } else if ("has_captions" in r && r.has_captions !== null && r.has_captions !== undefined) {
      bucket.videos += 1;
      if (r.has_captions) {
        bucket.captioned_videos += 1;
      }
    }
  }

  const result = Object.values(levelBuckets);
  result.sort((a, b) => a.level.localeCompare(b.level));

  console.log(JSON.stringify(result));
}

async function reportAuthor(name: string) {
  const client = createClient();

  const author = await e.select(e.Author, (a) => ({
    name: true,
    country: true,
    resource_count: true,
    resources: {
      title: true,
      minutes: true,
    },
    filter_single: e.op(a.name, "=", e.str(name)),
  })).run(client);

  if (!author) {
    process.stderr.write(`author not found: ${name}\n`);
    process.exit(3);
  }

  const titles = author.resources.map((r) => r.title).sort((a, b) => a.localeCompare(b));
  const totalMinutes = author.resources.reduce((sum, r) => sum + Number(r.minutes), 0);

  const result = {
    name: author.name,
    country: author.country,
    resource_count: Number(author.resource_count),
    total_minutes: totalMinutes,
    titles,
  };

  console.log(JSON.stringify(result));
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    process.stderr.write("Error: missing subcommand\n");
    process.exit(2);
  }

  const subcommand = args[0];

  try {
    if (subcommand === "load") {
      if (args.length > 1) {
        process.stderr.write("Error: unrecognized arguments for load\n");
        process.exit(2);
      }
      await load();
    } else if (subcommand === "report") {
      if (args.length < 2) {
        process.stderr.write("Error: missing report type\n");
        process.exit(2);
      }
      const reportType = args[1];
      if (reportType === "authors") {
        if (args.length > 2) {
          process.stderr.write("Error: unrecognized arguments for report authors\n");
          process.exit(2);
        }
        await reportAuthors();
      } else if (reportType === "levels") {
        if (args.length > 2) {
          process.stderr.write("Error: unrecognized arguments for report levels\n");
          process.exit(2);
        }
        await reportLevels();
      } else if (reportType === "author") {
        let name: string | null = null;
        for (let i = 2; i < args.length; i++) {
          if (args[i] === "--name") {
            if (i + 1 < args.length) {
              name = args[i + 1];
              i++;
            }
          } else if (args[i].startsWith("--name=")) {
            name = args[i].substring(7);
          } else {
            process.stderr.write(`Error: unrecognized argument ${args[i]}\n`);
            process.exit(2);
          }
        }
        if (name === null || name === "") {
          process.stderr.write("Error: report author requires a --name value\n");
          process.exit(2);
        }
        await reportAuthor(name);
      } else {
        process.stderr.write(`Error: unrecognized report type ${reportType}\n`);
        process.exit(2);
      }
    } else {
      process.stderr.write(`Error: unrecognized subcommand ${subcommand}\n`);
      process.exit(2);
    }
  } catch (err) {
    process.stderr.write(`Error: ${(err as Error).message}\n`);
    process.exit(1);
  }
}

main();
