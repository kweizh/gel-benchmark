# Gel Database (formerly EdgeDB) Benchmark Research & Evaluation Plan

This document provides a comprehensive research summary and benchmark evaluation plan for **Gel** (formerly EdgeDB), a next-generation graph-relational database built on top of PostgreSQL. It is designed to act as a definitive guide for creating high-quality evaluation datasets and benchmark tasks for AI coding agents.

---

## 1. Library Overview

### Description
**Gel** is a graph-relational database that combines the strict type safety, performance, and transactional guarantees of a relational database (PostgreSQL) with the intuitive data modeling of a graph database and the developer-friendly usability of an ORM. Instead of tables and rows, Gel models data using **Object Types** containing **Properties** (scalar values) and **Links** (relationships to other objects).

### Ecosystem Role
Gel replaces both the SQL database and the traditional Object-Relational Mapper (ORM) in the application stack. It provides:
- A declarative **Schema Definition Language (SDL)**.
- A highly expressive and composable query language, **EdgeQL** (or **GelQL**).
- A built-in, interactive **Schema Migration Engine**.
- First-party client SDKs (TypeScript/JS, Python, Go, Rust, .NET) that speak an efficient binary protocol with built-in connection pooling and automatic transaction retries.
- Built-in extensions for modern app development, such as **ext::auth** (built-in authentication) and **ext::ai** (semantic search and vector embeddings).

### Project Setup
To set up a local Gel project completely containerized without cloud or SaaS dependencies, follow these steps:

1. **Spin up a local Gel Docker container**:
   ```bash
   docker run --name gel-local -d \
     -p 5656:5656 \
     -e GEL_SERVER_SECURITY=insecure_dev_mode \
     geldata/gel:latest
   ```

2. **Initialize a local project directory**:
   Create a new directory and create a `gel.toml` file in its root:
   ```toml
   # gel.toml
   [project]
   minimum-gel-version = "6.0"
   ```

3. **Link the project to the running Docker instance**:
   Using the Gel CLI, register the Docker container as a named instance and link it to your project:
   ```bash
   # Link the running docker container to an instance named 'my_local_instance'
   gel instance link --docker --container gel-local my_local_instance

   # Initialize the project and link it to the instance
   gel init --link --server-instance my_local_instance --non-interactive
   ```
   This command automatically creates a `./dbschema` directory containing `default.gel` (your schema file) and registers the connection context.

4. **Run schema migrations**:
   ```bash
   # Create a migration based on default.gel
   gel migration create --non-interactive --allow-unsafe

   # Apply the migration to the database
   gel migrate
   ```

---

## 2. Core Primitives & APIs

### 2.1 Schema Definition Language (SDL)
Gel schemas are declared in `.gel` files inside the `./dbschema` directory. The primary file is typically `dbschema/default.gel`.

```sdl
module default {
  # Abstract type for polymorphism
  abstract type Content {
    required title: str;
    required created_at: datetime {
      default := datetime_of_statement();
    }
  }

  type User {
    required email: str {
      constraint exclusive;
    }
    required name: str;
    multi friends: User;
  }

  type Post extending Content {
    required body: str;
    required author: User;

    # Simple Index
    index on (.title);

    # Composite Index
    index on ((.author, .title));
  }
}
```
*API Reference*: [Schema Datamodel](https://docs.geldata.com/reference/datamodel)

### 2.2 EdgeQL / GelQL Basics (CRUD)
EdgeQL queries are fully composable and return structured JSON-like results natively.

#### Insert
```edgeql
insert User {
  name := "Alice",
  email := "alice@example.com"
};
```

#### Select with Shapes
```edgeql
select User {
  name,
  email,
  friends: {
    name
  }
} filter .email = "alice@example.com";
```

#### Update
```edgeql
update User
filter .email = "alice@example.com"
set {
  name := "Alice Smith"
};
```

#### Delete
```edgeql
delete User
filter .email = "alice@example.com";
```
*API Reference*: [EdgeQL Select](https://docs.geldata.com/reference/edgeql/select)

### 2.3 Advanced Graph Traversal & Backlinks
In Gel, relationships can be traversed in reverse (backlinks) using the `.<` operator combined with a type intersection `[is <Type>]`.

```edgeql
# Fetch users and all their written posts using a backlink
select User {
  name,
  email,
  posts := ..<author[is Post] {
    title,
    body
  }
};
```
*API Reference*: [Links & Backlinks](https://docs.geldata.com/reference/datamodel/links)

### 2.4 Polymorphic Queries
When querying abstract types, you can fetch fields specific to concrete subtypes using type intersections.

```edgeql
# Assume Movie and Show extend abstract type Content
select Content {
  title,
  [is Movie].director,
  [is Show].num_seasons
};
```
*API Reference*: [Polymorphism](https://docs.edgedb.com/tutorial/nested-structures/polymorphism)

### 2.5 Grouping & Aggregations
Unlike SQL, Gel has a top-level `group` statement that partitions a set and returns free-form objects containing the partition key and the matched elements.

```edgeql
group Post {
  title,
  body,
  author: { name }
}
by .author;
```
*API Reference*: [Group Statement](https://docs.geldata.com/reference/reference/edgeql/group)

### 2.6 JSON Handling
Gel supports a native `json` scalar type. You can cast strings to JSON, query paths inside JSON, and update JSON objects.

```edgeql
# Inserting JSON
insert User {
  name := "Bob",
  email := "bob@example.com",
  # Assume 'preferences' is a property of type json
  preferences := <json>'{"theme": "dark", "notifications": true}'
};

# Querying JSON path
select User {
  name,
  theme := .preferences['theme']
} filter json_get(.preferences, 'notifications') = <json>true;
```
*API Reference*: [JSON Standard Library](https://docs.geldata.com/reference/stdlib/json)

### 2.7 Client Library Integrations

#### Python Client (Async)
```python
import asyncio
import gel

async def main():
    # create_async_client() automatically resolves credentials from the project link
    client = gel.create_async_client()

    # Basic Query
    user = await client.query_single("""
        select User { name, email }
        filter .email = $email
    """, email="alice@example.com")
    print(user.name if user else "Not found")

    # Transaction Block (with automatic retries)
    async for tx in client.transaction():
        async with tx:
            await tx.execute("""
                insert User {
                  name := "Charlie",
                  email := "charlie@example.com"
                }
            """)

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```
*API Reference*: [Python Client Reference](https://docs.geldata.com/reference/using/python/client)

#### TypeScript Client
```typescript
import { createClient } from "gel";

async function run() {
  const client = createClient();

  // Basic Query
  const user = await client.querySingle<{ name: string; email: string }>(`
    select User { name, email }
    filter .email = <str>$email
  `, { email: "alice@example.com" });

  console.log(user?.name);

  // Transaction Block (automatically retried on conflict)
  await client.transaction(async (tx) => {
    await tx.execute(`
      insert User {
        name := "Charlie",
        email := "charlie@example.com"
      }
    `);
  });
}

run();
```
*API Reference*: [TypeScript Client Reference](https://docs.geldata.com/reference/using/clients)

---

## 3. Real-World Use Cases & Templates

### Common Integration Patterns
1. **Next.js + Gel**: Gel integrates beautifully with modern TypeScript frameworks. Developers typically use `@gel/generate` to generate fully-typed query builders (`edgeql-js`) directly from their schema.
2. **FastAPI + Gel**: Python developers frequently combine FastAPI with the asynchronous Gel client, leveraging Pydantic models for validation and Gel's native JSON serialization for high-performance API endpoints.
3. **tRPC + Gel**: The strict type safety of Gel schemas and the TypeScript query builder allows developers to establish end-to-end type safety from the database to the React frontend.

*Example Reference*: [tRPC with Gel Guide](https://docs.geldata.com/guides/tutorials/trpc)

---

## 4. Developer Friction Points

These common challenges serve as excellent candidates for complex evaluation tasks to test an agent's problem-solving capability:

### Limited Evaluation Env

* The tasks would always be evaluated in a limited environment, please make sure NEVER generate task description that required plenty of memory.
* **Fixture Dependencies in Tests**: When writing automated tests (like `pytest`) that interact with a local Gel server, tests that invoke the Gel CLI (e.g., `gel migration status`, `gel migrate`) might fail with a `Connection refused` error if they run before the database server is fully started. Always ensure that any test function executing a database-dependent CLI command explicitly requests the test fixture that starts the server (e.g., the `client` fixture), even if the test does not use the client object directly.
  * **Example**:
    ```python
    # BAD: Runs before server starts if placed early in the file
    def test_migration_status_is_in_sync():
        proc = subprocess.run(["gel", "migration", "status"], ...)

    # GOOD: Explicitly depends on the 'client' fixture which ensures the server is running
    def test_migration_status_is_in_sync(client):
        proc = subprocess.run(["gel", "migration", "status"], ...)
    ```

### Friction Point 1: Backlink Cardinality & Type Intersection
* **The Challenge**: When traversing a link in reverse (e.g., `.<author[is Post]`), Gel defaults to treating it as a `multi` link (returning a set of objects). If a developer wants to declare a computed 1-to-1 reverse link, they must explicitly use the `single` keyword in the schema definition. Furthermore, omitting the type intersection `[is Post]` when the link is on an abstract type causes compilation errors.
* **Relevant Resource**: [Links - Backlinks Reference](https://docs.geldata.com/reference/datamodel/links#backlinks)

### Friction Point 2: Access Policies and Global Variables in Transactions
* **The Challenge**: Defining object-level access policies (row-level security) usually relies on `global` variables (e.g., `global current_user_id: uuid`). When executing mutations inside a client transaction, developers often forget that they must set the global variable context on a *scoped* client instance (using `client.with_globals(...)` or `client.withGlobals(...)`) rather than trying to execute an EdgeQL `set global` statement inside the transaction block, which can cause session state isolation issues.
* **Relevant Resource**: [Access Policies Reference](https://docs.geldata.com/reference/datamodel/access_policies)

### Friction Point 3: Out-of-Sync Migration History
* **The Challenge**: If a developer manually runs a DDL statement (e.g., `create type...`) in the database REPL or if a migration script is modified out-of-sync with the local `dbschema/migrations` folder, the Gel CLI will refuse to run `gel migrate`. Resolving this requires understanding how to use `gel migration extract` to sync the file system with the database, or `gel migrate --dev-mode` to force-align the development instance.
* **Relevant Resource**: [Migrations Guide](https://docs.geldata.com/resources/guides/migrations/guide)

---

## 5. Evaluation Ideas

Below is a curated list of high-level evaluation ideas for benchmark tasks, ranging from simple schema design to complex client-side integrations:

### Category A: Schema & Migrations (Simple to Medium)
1. **Define a Polymorphic E-Commerce Catalog**: Implement an abstract `Product` type extended by `Book` and `Apparel`, with custom constraints on price and stock.
2. **Implement a Composite Index and Unique Constraint**: Create a schema for a user-group membership system ensuring a user can only join a group once, optimized with a composite index.
3. **Execute a Multi-Step Schema Migration**: Add a required property to an existing object type that already has data, providing a default fallback value during the interactive migration.

### Category B: Queries & GelQL (Medium to Complex)
4. **Draft a Hierarchical Backlink Query**: Write a single EdgeQL query that selects a `Category` and recursively fetches all subcategories and their associated products.
5. **Construct a Complex Grouping and Aggregation Pipeline**: Group a set of `Transaction` objects by transaction category and month, calculating the total sum and average amount for each group.
6. **Implement a Dynamic JSON Preference Merging Query**: Write a query that updates a user's JSON preferences by merging new key-value pairs without overwriting other existing keys.

### Category C: Client Integration & Security (Complex)
7. **Build a Thread-Safe Transactional Money Transfer API**: Implement a Python or TypeScript endpoint that transfers funds between two `Account` objects, handling concurrent request conflicts with automatic transaction retries.
8. **Secure a Multi-Tenant Schema with Object-Level Access Policies**: Define access policies using a `global tenant_id` and write client code that securely queries tenant-specific data using scoped clients.

---

## 6. Sources

1. [Gel Docker Deployment Reference](https://docs.geldata.com/reference/running/deployment/docker) — Details on spinning up local Gel containers and linking them.
2. [Gel Schema Datamodel](https://docs.geldata.com/reference/datamodel) — Core documentation on Gel SDL, object types, properties, and links.
3. [Gel Links & Backlinks](https://docs.geldata.com/reference/datamodel/links) — Explanation of link directions, backlinks, and cardinality.
4. [Gel Access Policies Reference](https://docs.geldata.com/reference/datamodel/access_policies) — Detailed syntax and behavior of row-level security and global variables.
5. [Gel Python Client API](https://docs.geldata.com/reference/using/python/client) — Documentation for setting up asynchronous clients, transactions, and globals in Python.
6. [Gel TypeScript Client API](https://docs.geldata.com/reference/using/clients) — Documentation for the JS/TS client library and connection mechanisms.
7. [Gel Migration Guide](https://docs.geldata.com/resources/guides/migrations/guide) — Step-by-step workflow for creating, applying, and fixing migrations.
8. [Gel Group Reference](https://docs.geldata.com/reference/reference/edgeql/group) — Syntax and response format of the top-level `group` statement.
9. [Gel JSON Standard Library](https://docs.geldata.com/reference/stdlib/json) — Complete list of operators and functions for handling JSON scalars.
