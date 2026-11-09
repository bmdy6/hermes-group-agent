"""Explicit live read-only MCP acceptance. No responses, secrets or IDs are printed.

Use Hermes v0.23.0's frozen Python 3.14 environment (MCP v2 SDK).
Inject GITHUB_READ_ONLY_TOKEN and LINEAR_READ_ONLY_TOKEN in memory.
Never run this with a write-capable personal token.
"""
import asyncio
import json
import logging
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams

logging.disable(logging.CRITICAL)


async def verify(service):
    if service == 'github':
        url = 'https://api.githubcopilot.com/mcp/readonly'
        token = os.environ.get('GITHUB_READ_ONLY_TOKEN')
        extra = {'X-MCP-Readonly': 'true', 'X-MCP-Toolsets': 'repos,issues,pull_requests'}
        tool, arguments = 'search_repositories', {'query': os.environ['GITHUB_TEST_QUERY'], 'perPage': 1}
    else:
        url = 'https://mcp.linear.app/mcp/readonly'
        token = os.environ.get('LINEAR_READ_ONLY_TOKEN')
        extra = {}
        tool, arguments = 'list_teams', {}
    result = dict(service=service, initialized=False, tools_listed=False,
                  all_readonly=False, write_tools_absent=False, read=False)
    if not token:
        return result
    try:
        async with httpx2.AsyncClient(headers={'Authorization': f'Bearer {token}', **extra}, timeout=25) as client:
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result['initialized'] = True
                    tools, cursor = [], None
                    while True:
                        page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    names = {tool.name for tool in tools}
                    result['tools_listed'] = bool(tools)
                    result['all_readonly'] = bool(tools) and all(
                        tool.annotations and tool.annotations.read_only_hint is True for tool in tools)
                    result['write_tools_absent'] = not any(
                        name.startswith(('create_', 'update_', 'delete_', 'add_', 'merge_', 'push_', 'assign_', 'save_'))
                        or name in {'issue_write', 'pull_request_review_write', 'manage_pull_request_review', 'manage_issue_field'}
                        for name in names)
                    # No write probe: absence + readOnlyHint proves the requested surface
                    # without risking a mutation if a provider changes behavior.
                    if tool in names:
                        response = await session.call_tool(tool, arguments)
                        result['read'] = not response.is_error
    except Exception:
        pass  # Library error bodies may contain identifiers or Authorization details.
    return result


async def main():
    results = [await verify(service) for service in ('github', 'linear')]
    for result in results:
        print(json.dumps(result), flush=True)
    return 0 if all(all(value for key, value in row.items() if key != 'service') for row in results) else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
