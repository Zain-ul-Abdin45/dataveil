"""End-to-end tests through the real MCP protocol, not just direct function
calls -- see sqlmesh-mcp's test_protocol.py for why this matters separately
from test_server.py: without raising ToolError specifically, the MCP SDK
replaces any exception with a generic "Error executing tool <name>" and
drops the real message, invisible to a test that just calls the Python
function and catches the exception itself.

Spawns the server as a real subprocess and talks to it over stdio, the same
way a real MCP client would.
"""

import os
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@asynccontextmanager
async def open_session(db_path):
    env = os.environ.copy()
    env["DATAVEIL_ADAPTER"] = "duckdb"
    env["DATAVEIL_DUCKDB_PATH"] = str(db_path)
    params = StdioServerParameters(command=sys.executable, args=["-m", "dataveil.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_lists_all_four_tools(tmp_path):
    async with open_session(tmp_path / "test.duckdb") as session:
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {"list_tables", "profile", "propose_cleansing_plan", "apply_cleansing_plan"}


async def test_apply_cleansing_plan_is_flagged_destructive_others_read_only(tmp_path):
    async with open_session(tmp_path / "test.duckdb") as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert tools["apply_cleansing_plan"].annotations.read_only_hint is False
        assert tools["apply_cleansing_plan"].annotations.destructive_hint is True
        for name in ["list_tables", "profile", "propose_cleansing_plan"]:
            assert tools[name].annotations.read_only_hint is True


async def test_apply_cleansing_plan_without_confirm_is_a_tool_error_with_the_real_message(tmp_path):
    async with open_session(tmp_path / "test.duckdb") as session:
        result = await session.call_tool("apply_cleansing_plan", {"table": "people", "plan": [], "confirm": False})
        assert result.is_error is True
        assert "confirm=true" in str(result.content)


async def test_profile_round_trips_real_data_with_no_literal_value(tmp_path):
    db_path = tmp_path / "test.duckdb"
    import duckdb

    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE people (id INTEGER, email VARCHAR)")
    con.execute("INSERT INTO people VALUES (1, 'alice@example.com')")
    con.close()

    async with open_session(db_path) as session:
        result = await session.call_tool("profile", {"table": "people"})
        assert result.is_error is not True
        text = str(result.content)
        assert "people" in text or "row_count" in text
        assert "alice@example.com" not in text


async def test_session_survives_a_tool_error_and_keeps_working(tmp_path):
    async with open_session(tmp_path / "test.duckdb") as session:
        await session.call_tool("apply_cleansing_plan", {"table": "x", "plan": [], "confirm": False})
        result = await session.call_tool("list_tables", {})
        assert result.is_error is not True
