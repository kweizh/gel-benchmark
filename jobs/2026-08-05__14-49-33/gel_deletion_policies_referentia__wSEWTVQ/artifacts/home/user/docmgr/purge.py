import gel
from typing import Dict, Any

async def purge_workspace(client, workspace_name: str) -> Dict[str, Any]:
    """
    Purge the workspace with the given name and report what the purge removed.
    
    This function performs the whole purge inside a single database transaction.
    If the workspace is not found, it raises LookupError.
    Database integrity errors (gel.errors.ConstraintViolationError) are propagated unwrapped.
    """
    async for tx in client.transaction():
        async with tx:
            # 1. Check if the workspace exists
            workspace_exists = await tx.query_single(
                "select exists(select Workspace filter .name = <str>$name)",
                name=workspace_name
            )
            if not workspace_exists:
                raise LookupError(f"Workspace '{workspace_name}' not found")
            
            # 2. Count folders of that workspace before deletion
            folders_count = await tx.query_single(
                "select count(Folder filter .workspace.name = <str>$name)",
                name=workspace_name
            )
            
            # 3. Count documents in those folders before deletion
            documents_count = await tx.query_single(
                "select count(Document filter .folder.workspace.name = <str>$name)",
                name=workspace_name
            )
            
            # 4. Find the distinct attachments linked by those documents before deletion
            attachment_ids = await tx.query(
                "select (select Document filter .folder.workspace.name = <str>$name).attachments.id",
                name=workspace_name
            )
            attachment_id_list = list(attachment_ids)
            
            # 5. Perform the actual deletion of the workspace
            # This triggers cascading deletes:
            # - Workspace -> Folders (on target delete delete source)
            # - Folders -> Documents (on target delete delete source)
            # - Documents -> Attachments (on source delete delete target if orphan)
            await tx.execute(
                "delete Workspace filter .name = <str>$name",
                name=workspace_name
            )
            
            # 6. Check which of the original attachments still exist
            if attachment_id_list:
                surviving_attachments = await tx.query(
                    "select Attachment filter .id in array_unpack(<array<uuid>>$ids)",
                    ids=attachment_id_list
                )
                surviving_ids = {att.id for att in surviving_attachments}
            else:
                surviving_ids = set()
            
            attachments_kept = len(surviving_ids)
            attachments_deleted = len(attachment_id_list) - attachments_kept
            
            return {
                "workspace": workspace_name,
                "folders_deleted": folders_count,
                "documents_deleted": documents_count,
                "attachments_deleted": attachments_deleted,
                "attachments_kept": attachments_kept,
            }
