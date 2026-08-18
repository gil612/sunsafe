"""
SunSafe — Agent Loop (Gemini API, מסלול פשוט ללא GCP)
--------------------------------------------------------
שלב 2 מה-SPEC: Agent Loop מיושם ידנית מול Gemini API, עם Tool אחד
לבדיקה (get_current_uv) לפני שמתחברים ל-MCP Server המלא.

התקנה:
    pip install google-genai httpx python-dotenv

הרצה כבדיקה עצמאית:
    python agent_loop.py
"""

import os
import logging

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logger = logging.getLogger("sunsafe.agent")
logging.basicConfig(level=logging.INFO)


def make_client() -> genai.Client:
    """
    יוצר Client מול Gemini Developer API (מסלול הברירת מחדל של הקורס — API Key,
    ללא צורך בפרויקט GCP). לא ננעלים על Vertex AI — מי שירצה בעתיד
    להחליף למסלול הארגוני, יעדכן רק את הפונקציה הזו.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY לא מוגדר. הוסיפו אותו ל-.env (ראו .env.example). "
            "מקבלים מפתח חינמי דרך https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Tool #1 (לבדיקת הלולאה בלבד): get_current_uv
#
# כרגע קריאה ישירה ל-Open-Meteo. בשלב הבא (branch feature/mcp-weather-server)
# הפונקציה הזו "תעבור מאחורי" שרת MCP — אבל חוזה הכלי (השם, הפרמטרים,
# הפורמט המוחזר) יישאר זהה, כך שהחלפה עתידית לא תשבור את ה-Agent Loop.
# ---------------------------------------------------------------------------

def get_current_uv(lat: float, lon: float) -> dict:
    """מחזיר UV Index נוכחי, טמפרטורה וכיסוי עננים למיקום נתון (Open-Meteo, חינמי, ללא API key)."""
    response = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "uv_index,temperature_2m,cloud_cover",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["current"]


GET_CURRENT_UV_DECLARATION = types.FunctionDeclaration(
    name="get_current_uv",
    description=(
        "מחזיר את מדד ה-UV הנוכחי (uv_index), הטמפרטורה וכיסוי העננים "
        "עבור קואורדינטות גיאוגרפיות נתונות. יש לקרוא לפונקציה הזו כל "
        "פעם שצריך מידע עדכני על רמת קרינת UV במקום מסוים."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "lat": {"type": "NUMBER", "description": "קו רוחב (latitude)"},
            "lon": {"type": "NUMBER", "description": "קו אורך (longitude)"},
        },
        "required": ["lat", "lon"],
    },
)

TOOLS = {
    "get_current_uv": get_current_uv,
}

TOOL_DECLARATIONS = [GET_CURRENT_UV_DECLARATION]


def execute_tool(name: str, args: dict):
    if name not in TOOLS:
        raise ValueError(f"כלי לא מוכר: {name}")
    logger.info("Executing tool %s with args=%s", name, args)
    return TOOLS[name](**args)


def log_tool_call(iteration: int, name: str, args: dict, result) -> None:
    logger.info("[iter %s] tool=%s args=%s -> result=%s", iteration, name, args, result)


def agent_loop(
    task: str,
    tool_declarations: list[types.FunctionDeclaration] = TOOL_DECLARATIONS,
    max_iterations: int = 15,
) -> str:
    client = make_client()
    chat = client.chats.create(
        model="gemini-3.5-flash-lite",
        config=types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=tool_declarations)]
        ),
    )
    response = chat.send_message(task)

    for i in range(max_iterations):
        fn_calls = [
            p.function_call for p in response.candidates[0].content.parts if p.function_call
        ]
        if not fn_calls:
            return response.text  # סיום — אין עוד קריאות לכלים

        results = []
        for fc in fn_calls:
            args = dict(fc.args)
            result = execute_tool(fc.name, args)
            log_tool_call(i, fc.name, args, result)
            results.append(
                types.Part.from_function_response(name=fc.name, response={"result": result})
            )
        response = chat.send_message(results)

    raise RuntimeError(f"Agent exceeded {max_iterations} iterations")


if __name__ == "__main__":
    answer = agent_loop(
        "מה ה-UV Index הנוכחי בתל אביב? (קואורדינטות: lat=32.08, lon=34.78). "
        "תן תשובה קצרה בעברית, כולל אם צריך הגנה מהשמש עכשיו."
    )
    print("תשובת הסוכן:")
    print(answer)
