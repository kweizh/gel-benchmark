import type { Client } from "gel";
import type { SchemaReport } from "./types";

interface AbstractConstraintRow {
  name: string;
}

interface DelegatedConstraintRow {
  ptr_name: string | null;
  ptr_type: string | null;
  obj_type: string | null;
}

function withoutModule(qualifiedName: string): string {
  const idx = qualifiedName.lastIndexOf("::");
  return idx === -1 ? qualifiedName : qualifiedName.slice(idx + 2);
}

/**
 * Performs live introspection of the database schema to discover:
 *  - every abstract constraint defined in module `default`
 *  - every pointer-level constraint marked `delegated` on a module `default` object type
 *  - every module `default` object type carrying a `delegated` object-level constraint
 *
 * Nothing here is hardcoded: all results are derived from querying the
 * `schema::Constraint` introspection type against the live database.
 */
export async function introspectSchema(client: Client): Promise<SchemaReport> {
  const abstractRows = await client.query<AbstractConstraintRow>(
    `select schema::Constraint { name }
     filter .abstract = true and .name like 'default::%';`,
  );
  const abstractConstraints = Array.from(
    new Set(abstractRows.map((r) => r.name)),
  ).sort();

  const delegatedRows = await client.query<DelegatedConstraintRow>(
    `select schema::Constraint {
       ptr_name := .subject[is schema::Pointer].name,
       ptr_type := .subject[is schema::Pointer].source[is schema::ObjectType].name,
       obj_type := .subject[is schema::ObjectType].name,
     }
     filter .delegated = true;`,
  );

  const pointerConstraints = new Set<string>();
  const objectConstraints = new Set<string>();

  for (const row of delegatedRows) {
    if (row.ptr_type && row.ptr_name && row.ptr_type.startsWith("default::")) {
      pointerConstraints.add(`${withoutModule(row.ptr_type)}.${row.ptr_name}`);
    }
    if (row.obj_type && row.obj_type.startsWith("default::")) {
      objectConstraints.add(withoutModule(row.obj_type));
    }
  }

  return {
    abstract_constraints: abstractConstraints,
    delegated_pointer_constraints: Array.from(pointerConstraints).sort(),
    delegated_object_constraints: Array.from(objectConstraints).sort(),
  };
}
