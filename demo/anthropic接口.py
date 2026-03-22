from anthropic import Anthropic
import os
import subprocess
from dotenv import load_dotenv

load_dotenv(override=True)


client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "assistant", "content": "你好，你的运行环境是什么"},
]
response = client.messages.create(
    model=MODEL,
    messages=messages,  # type: ignore
    tools=TOOLS,  # type: ignore
    max_tokens=8000,
)

print(response)
