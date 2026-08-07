"""Tenant-scoped data gateway for the multi-tenant document SaaS.

Everything below is an unimplemented stub.
"""


async def list_workspaces(tenant_slug, role):
    raise NotImplementedError("list_workspaces is not implemented yet")


async def get_document(tenant_slug, role, document_id):
    raise NotImplementedError("get_document is not implemented yet")


async def create_document(
    tenant_slug, role, workspace_name, title, body, comment_bodies, author_email
):
    raise NotImplementedError("create_document is not implemented yet")


async def rename_document(tenant_slug, role, document_id, new_title):
    raise NotImplementedError("rename_document is not implemented yet")


async def delete_document(tenant_slug, role, document_id):
    raise NotImplementedError("delete_document is not implemented yet")


async def archive_workspace(tenant_slug, role, workspace_name):
    raise NotImplementedError("archive_workspace is not implemented yet")


async def platform_document_counts(role):
    raise NotImplementedError("platform_document_counts is not implemented yet")
