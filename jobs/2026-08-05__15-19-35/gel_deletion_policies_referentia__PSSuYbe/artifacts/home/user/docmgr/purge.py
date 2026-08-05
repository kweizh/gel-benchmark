"""Workspace purge routine.

Deletes an entire :class:`Workspace` (and, thanks to the schema's link
deletion policies, every ``Folder``/``Document`` it contains and every
``Attachment`` that becomes orphaned as a result) inside a single database
transaction, then reports what actually changed.
"""

from __future__ import annotations

import gel


async def purge_workspace(
    client: "gel.AsyncIOClient", workspace_name: str
) -> dict:
    """Purge the workspace named ``workspace_name``.

    The whole operation (pre-purge snapshot, delete, post-purge check) runs
    inside a single Gel transaction. If the database refuses the delete
    (e.g. because an archived document would be removed), the original
    ``gel.errors.ConstraintViolationError`` propagates to the caller and the
    transaction is rolled back automatically, leaving the database
    untouched.
    """
    async for tx in client.transaction():
        async with tx:
            workspace_id = await tx.query_single(
                """
                select (select Workspace filter .name = <str>$name).id
                """,
                name=workspace_name,
            )
            if workspace_id is None:
                raise LookupError(
                    f"no Workspace named {workspace_name!r}"
                )

            folder_ids = set(
                await tx.query(
                    """
                    select (
                        select Folder filter .workspace.id = <uuid>$wid
                    ).id
                    """,
                    wid=workspace_id,
                )
            )

            document_ids = set(
                await tx.query(
                    """
                    select (
                        select Document
                        filter .folder.workspace.id = <uuid>$wid
                    ).id
                    """,
                    wid=workspace_id,
                )
            )

            attachment_ids = set(
                await tx.query(
                    """
                    with docs := (
                        select Document
                        filter .folder.workspace.id = <uuid>$wid
                    )
                    select distinct docs.attachments.id
                    """,
                    wid=workspace_id,
                )
            )

            # This is the statement that does the actual work: deleting the
            # Workspace cascades to its Folders and Documents, and the
            # schema's "if orphan" policy garbage-collects Attachments that
            # are no longer referenced by any surviving Document. If the
            # workspace (directly or indirectly) contains a Document that is
            # still archived, this raises ConstraintViolationError and the
            # whole transaction is rolled back.
            await tx.execute(
                "delete Workspace filter .id = <uuid>$wid",
                wid=workspace_id,
            )

            surviving_folder_ids = set(
                await tx.query(
                    """
                    select Folder.id
                    filter Folder.id in array_unpack(<array<uuid>>$ids)
                    """,
                    ids=list(folder_ids),
                )
            )
            surviving_document_ids = set(
                await tx.query(
                    """
                    select Document.id
                    filter Document.id in array_unpack(<array<uuid>>$ids)
                    """,
                    ids=list(document_ids),
                )
            )
            surviving_attachment_ids = set(
                await tx.query(
                    """
                    select Attachment.id
                    filter Attachment.id in array_unpack(<array<uuid>>$ids)
                    """,
                    ids=list(attachment_ids),
                )
            )

            return {
                "workspace": workspace_name,
                "folders_deleted": len(folder_ids - surviving_folder_ids),
                "documents_deleted": len(
                    document_ids - surviving_document_ids
                ),
                "attachments_deleted": len(
                    attachment_ids - surviving_attachment_ids
                ),
                "attachments_kept": len(
                    attachment_ids & surviving_attachment_ids
                ),
            }
