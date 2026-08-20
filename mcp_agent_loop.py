"""
SunSafe — Agent Loop against the MCP Weather Server
-------------------------------------------------------
An updated version of agent_loop.py: instead of tools hand-defined in Python
(the TOOLS dict), the tools are discovered dynamically from an MCP server
(mcp_weather_server.py) — the Agent Loop "does not know" how the weather API is
built, it only talks to MCP. This makes it possible to swap providers
(Open-Meteo -> something else) without touching the agent logic, and to use the
same server from Claude Desktop for manual testing.

Install:
    pip install mcp google-genai python-dotenv httpx

Run as a standalone check:
    python mcp_agent_loop.py
"""

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

logger = logging.getLogger("sunsafe.mcp_agent")
logging.basicConfig(level=logging.INFO)

# הפעלת mcp_weather_server.py כתת-תהליך, בתקשורת stdio.
#
# חשוב: משתמשים ב-sys.executable (לא רק "python") כדי להריץ את השרת
# עם *אותו* אינטרפרטר בדיוק שמריץ את הקובץ הזה — כולל אותו venv עם
# החבילות המותקנות (mcp, httpx). אם משתמשים רק ב-"python", ב-Windows
# זה עלול להצביע על התקנת Python אחרת (בלי mcp/httpx מותקנים), מה
# שגורם לתת-התהליך לקרוס מיד עם ImportError — וללקוח זה נראה כמו
# "Connection closed" סתום בלי שום רמז לסיבה האמיתית.
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(_SERVER_DIR, "mcp_weather_server.py")],
    cwd=_SERVER_DIR,
)


def make_client() -> genai.Client:
    """Gemini Developer API — the course's default track (API key, no GCP)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY לא מוגדר. הוסיפו אותו ל-.env. "
            "מקבלים מפתח חינמי דרך https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


def _normalize_schema_types(schema: dict) -> dict:
    """
    MCP returns a JSON Schema with lowercase types ("object", "string"), while
    the Gemini SDK expects uppercase ones ("OBJECT", "STRING"). This function
    converts recursively between the two formats.
    """
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            result[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            result[key] = {k: _normalize_schema_types(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _normalize_schema_types(value)
        else:
            result[key] = value
    return result


def mcp_tools_to_function_declarations(mcp_tools) -> list[types.FunctionDeclaration]:
    """Convert a list of tools from session.list_tools() into Gemini FunctionDeclarations."""
    declarations = []
    for tool in mcp_tools:
        schema = _normalize_schema_types(
            tool.inputSchema or {"type": "OBJECT", "properties": {}}
        )
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description or "",
                parameters=schema,
            )
        )
    return declarations


def _parse_tool_result(result) -> object:
    """Extract a readable result from a CallToolResult (usually TextContent holding JSON)."""
    texts = [c.text for c in result.content if hasattr(c, "text")]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


async def agent_loop_mcp(
    task: str,
    server_params: StdioServerParameters = DEFAULT_SERVER_PARAMS,
    max_iterations: int = 15,
) -> str:
    client = make_client()

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_declarations = mcp_tools_to_function_declarations(tools_result.tools)
            logger.info(
                "Loaded %d tool(s) from MCP server: %s",
                len(tool_declarations),
                [t.name for t in tool_declarations],
            )

            chat = client.chats.create(
                model="gemini-3.5-flash-lite",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(function_declarations=tool_declarations)]
                ),
            )
            response = chat.send_message(task)

            for i in range(max_iterations):
                fn_calls = [
                    p.function_call
                    for p in response.candidates[0].content.parts
                    if p.function_call
                ]
                if not fn_calls:
                    return response.text  # סיום — אין עוד קריאות לכלים

                results = []
                for fc in fn_calls:
                    args = dict(fc.args)
                    logger.info("[iter %s] calling MCP tool %s(%s)", i, fc.name, args)
                    mcp_result = await session.call_tool(fc.name, args)
                    parsed = _parse_tool_result(mcp_result)
                    logger.info("[iter %s] tool=%s -> %s", i, fc.name, parsed)
                    results.append(
                        types.Part.from_function_response(
                            name=fc.name, response={"result": parsed}
                        )
                    )
                response = chat.send_message(results)

            raise RuntimeError(f"Agent exceeded {max_iterations} iterations")


def run(task: str, server_params: StdioServerParameters = DEFAULT_SERVER_PARAMS) -> str:
    """A convenient synchronous wrapper for calling from regular code (e.g. send_uv_report.py)."""
    return asyncio.run(agent_loop_mcp(task, server_params))


if __name__ == "__main__":
    answer = run(
        "מה ה-UV Index הנוכחי בתל אביב? (קואורדינטות: lat=32.08, lon=34.78). "
        "תן תשובה קצרה בעברית, כולל אם צריך הגנה מהשמש עכשיו."
    )
    print("תשובת הסוכן (דרך MCP):")
    print(answer)
