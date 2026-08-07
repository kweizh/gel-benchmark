// Catalog analytics CLI.
//
// Every database read/write goes through the generated query builder in
// `dbschema/edgeql-js`; no hand-written EdgeQL strings are used anywhere in
// this file.

import * as fs from "node:fs";
import * as path from "node:path";
import { createClient } from "gel";
import e from "../dbschema/edgeql-js";
import type { $infer } from "../dbschema/edgeql-js";

type CatalogClient = ReturnType<typeof createClient>;

// ---------------------------------------------------------------------------
// Input data parsing (data/resources.json)
// ---------------------------------------------------------------------------

interface ArticleEntry {
  kind: "article";
  title: string;
  author: string;
  minutes: number;
  level: string;
  word_count: number;
}

interface VideoEntry {
  kind: "video";
  title: string;
  author: string;
  minutes: number;
  level: string;
  has_captions: boolean;
}

type ResourceEntry = ArticleEntry | VideoEntry;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`expected a string for field "${field}"`);
  }
  return value;
}

function requireNumber(value: unknown, field: string): number {
  if (typeof value !== "number") {
    throw new Error(`expected a number for field "${field}"`);
  }
  return value;
}

function requireBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`expected a boolean for field "${field}"`);
  }
  return value;
}

function parseResourceEntry(value: unknown): ResourceEntry {
  if (!isRecord(value)) {
    throw new Error("each resource entry must be an object");
  }
  const title = requireString(value.title, "title");
  const author = requireString(value.author, "author");
  const minutes = requireNumber(value.minutes, "minutes");
  const level = requireString(value.level, "level");
  const kind = value.kind;
  if (kind === "article") {
    return {
      kind: "article",
      title,
      author,
      minutes,
      level,
      word_count: requireNumber(value.word_count, "word_count"),
    };
  }
  if (kind === "video") {
    return {
      kind: "video",
      title,
      author,
      minutes,
      level,
      has_captions: requireBoolean(value.has_captions, "has_captions"),
    };
  }
  throw new Error(`unknown resource kind: ${JSON.stringify(kind)}`);
}

function loadResourceEntries(filePath: string): ResourceEntry[] {
  const raw: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (!Array.isArray(raw)) {
    throw new Error("resources.json must contain a JSON array");
  }
  return raw.map(parseResourceEntry);
}

// ---------------------------------------------------------------------------
// Query builder definitions
// ---------------------------------------------------------------------------

const authorsQuery = e.select(e.Author, () => ({
  name: true,
  country: true,
}));

const articlesQuery = e.select(e.Article, () => ({
  title: true,
  minutes: true,
  level: true,
  word_count: true,
  author: { name: true },
}));

const videosQuery = e.select(e.Video, () => ({
  title: true,
  minutes: true,
  level: true,
  has_captions: true,
  author: { name: true },
}));

const countsQuery = e.select({
  articles: e.count(e.Article),
  videos: e.count(e.Video),
  total: e.count(e.Resource),
});

type AuthorRow = $infer<typeof authorsQuery>[number];
type ArticleRow = $infer<typeof articlesQuery>[number];
type VideoRow = $infer<typeof videosQuery>[number];

// ---------------------------------------------------------------------------
// In-memory shaping helpers
// ---------------------------------------------------------------------------

interface ResourceRecord {
  kind: "article" | "video";
  title: string;
  minutes: number;
  level: string;
  authorName: string;
  wordCount: number | null;
  hasCaptions: boolean | null;
}

function articleToRecord(row: ArticleRow): ResourceRecord {
  return {
    kind: "article",
    title: row.title,
    minutes: row.minutes,
    level: row.level,
    authorName: row.author.name,
    wordCount: row.word_count,
    hasCaptions: null,
  };
}

function videoToRecord(row: VideoRow): ResourceRecord {
  return {
    kind: "video",
    title: row.title,
    minutes: row.minutes,
    level: row.level,
    authorName: row.author.name,
    wordCount: null,
    hasCaptions: row.has_captions,
  };
}

async function fetchResourceRecords(
  client: CatalogClient,
): Promise<ResourceRecord[]> {
  const [articles, videos] = await Promise.all([
    articlesQuery.run(client),
    videosQuery.run(client),
  ]);
  return [...articles.map(articleToRecord), ...videos.map(videoToRecord)];
}

function compareStrings(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

// ---------------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------------

interface AuthorReportRow {
  name: string;
  articles: number;
  videos: number;
  total_minutes: number;
  avg_minutes: number;
  top_title: string | null;
}

function buildAuthorReport(
  authors: AuthorRow[],
  resources: ResourceRecord[],
): AuthorReportRow[] {
  const rows = authors.map((author): AuthorReportRow => {
    const own = resources.filter((r) => r.authorName === author.name);
    const articles = own.filter((r) => r.kind === "article").length;
    const videos = own.filter((r) => r.kind === "video").length;
    const total_minutes = own.reduce((sum, r) => sum + r.minutes, 0);
    const avg_minutes = own.length === 0 ? 0 : round2(total_minutes / own.length);

    let topTitle: string | null = null;
    let topMinutes = -Infinity;
    for (const r of own) {
      if (
        r.minutes > topMinutes ||
        (r.minutes === topMinutes &&
          topTitle !== null &&
          compareStrings(r.title, topTitle) < 0)
      ) {
        topMinutes = r.minutes;
        topTitle = r.title;
      }
    }

    return {
      name: author.name,
      articles,
      videos,
      total_minutes,
      avg_minutes,
      top_title: topTitle,
    };
  });

  rows.sort((a, b) => {
    if (b.total_minutes !== a.total_minutes) {
      return b.total_minutes - a.total_minutes;
    }
    return compareStrings(a.name, b.name);
  });

  return rows;
}

interface LevelReportRow {
  level: string;
  count: number;
  articles: number;
  videos: number;
  total_minutes: number;
  total_words: number;
  captioned_videos: number;
}

function buildLevelReport(resources: ResourceRecord[]): LevelReportRow[] {
  const levels = Array.from(new Set(resources.map((r) => r.level))).sort(
    compareStrings,
  );

  return levels.map((level): LevelReportRow => {
    const bucket = resources.filter((r) => r.level === level);
    const articles = bucket.filter((r) => r.kind === "article");
    const videos = bucket.filter((r) => r.kind === "video");
    const total_words = articles.reduce(
      (sum, r) => sum + (r.wordCount ?? 0),
      0,
    );
    const captioned_videos = videos.filter(
      (r) => r.hasCaptions === true,
    ).length;

    return {
      level,
      count: bucket.length,
      articles: articles.length,
      videos: videos.length,
      total_minutes: bucket.reduce((sum, r) => sum + r.minutes, 0),
      total_words,
      captioned_videos,
    };
  });
}

interface AuthorDetailReport {
  name: string;
  country: string;
  resource_count: number;
  total_minutes: number;
  titles: string[];
}

function buildAuthorDetail(
  author: AuthorRow,
  resources: ResourceRecord[],
): AuthorDetailReport {
  const own = resources.filter((r) => r.authorName === author.name);
  return {
    name: author.name,
    country: author.country,
    resource_count: own.length,
    total_minutes: own.reduce((sum, r) => sum + r.minutes, 0),
    titles: own.map((r) => r.title).sort(compareStrings),
  };
}

// ---------------------------------------------------------------------------
// Command implementations
// ---------------------------------------------------------------------------

async function insertEntry(
  client: CatalogClient,
  entry: ResourceEntry,
): Promise<void> {
  const authorRef = e.assert_exists(
    e.select(e.Author, () => ({ filter_single: { name: entry.author } })),
  );

  if (entry.kind === "article") {
    await e
      .insert(e.Article, {
        title: entry.title,
        minutes: entry.minutes,
        level: entry.level,
        word_count: entry.word_count,
        author: authorRef,
      })
      .unlessConflict((article) => ({ on: article.title }))
      .run(client);
  } else {
    await e
      .insert(e.Video, {
        title: entry.title,
        minutes: entry.minutes,
        level: entry.level,
        has_captions: entry.has_captions,
        author: authorRef,
      })
      .unlessConflict((video) => ({ on: video.title }))
      .run(client);
  }
}

async function runLoad(): Promise<number> {
  const client = createClient();
  try {
    const dataPath = path.join(__dirname, "..", "data", "resources.json");
    const entries = loadResourceEntries(dataPath);
    for (const entry of entries) {
      await insertEntry(client, entry);
    }
    const counts = await countsQuery.run(client);
    process.stdout.write(`${JSON.stringify(counts)}\n`);
    return 0;
  } finally {
    await client.close();
  }
}

async function runReportAuthors(): Promise<number> {
  const client = createClient();
  try {
    const [authors, resources] = await Promise.all([
      authorsQuery.run(client),
      fetchResourceRecords(client),
    ]);
    const report = buildAuthorReport(authors, resources);
    process.stdout.write(`${JSON.stringify(report)}\n`);
    return 0;
  } finally {
    await client.close();
  }
}

async function runReportLevels(): Promise<number> {
  const client = createClient();
  try {
    const resources = await fetchResourceRecords(client);
    const report = buildLevelReport(resources);
    process.stdout.write(`${JSON.stringify(report)}\n`);
    return 0;
  } finally {
    await client.close();
  }
}

function extractNameFlag(args: string[]): string | null {
  const prefix = "--name=";
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--name") {
      const value = args[i + 1];
      return value !== undefined && value.length > 0 ? value : null;
    }
    if (arg.startsWith(prefix)) {
      const value = arg.slice(prefix.length);
      return value.length > 0 ? value : null;
    }
  }
  return null;
}

async function runReportAuthor(args: string[]): Promise<number> {
  const name = extractNameFlag(args);
  if (name === null) {
    process.stderr.write(
      "catalog: report author requires a --name <value> flag\n",
    );
    return 2;
  }

  const client = createClient();
  try {
    const [authors, resources] = await Promise.all([
      authorsQuery.run(client),
      fetchResourceRecords(client),
    ]);
    const author = authors.find((a) => a.name === name);
    if (!author) {
      process.stderr.write(`author not found: ${name}\n`);
      return 3;
    }
    const report = buildAuthorDetail(author, resources);
    process.stdout.write(`${JSON.stringify(report)}\n`);
    return 0;
  } finally {
    await client.close();
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

async function main(): Promise<number> {
  const [subcommand, ...rest] = process.argv.slice(2);

  if (subcommand === "load") {
    return runLoad();
  }

  if (subcommand === "report") {
    const [reportType, ...reportArgs] = rest;
    if (reportType === "authors") {
      return runReportAuthors();
    }
    if (reportType === "levels") {
      return runReportLevels();
    }
    if (reportType === "author") {
      return runReportAuthor(reportArgs);
    }
    process.stderr.write(
      `catalog: unknown report type: ${reportType ?? "(none)"}\n`,
    );
    return 2;
  }

  process.stderr.write(
    `catalog: unknown subcommand: ${subcommand ?? "(none)"}\n`,
  );
  return 2;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`catalog: ${message}\n`);
    process.exitCode = 1;
  });
