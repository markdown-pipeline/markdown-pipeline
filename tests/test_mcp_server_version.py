"""MD-207: the MCP server reports its real version in `initialize` (serverInfo.version), not an empty string."""
import asyncio
import os
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from mdpipeline import __version__, mcp_server


def test_build_server_carries_the_package_version(tmp_path):
    from mdpipeline.brain import init_brain
    server = mcp_server.build_server(init_brain(tmp_path))
    assert server.version == __version__


def test_initialize_answers_with_the_package_version_over_stdio(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", "mcp"], env=dict(os.environ), cwd=str(tmp_path))

    async def go():
        async with Client(params) as client:
            return client.server_info.version

    assert asyncio.run(go()) == __version__
