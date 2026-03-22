from openai import OpenAI
import os
import subprocess


client = OpenAI(
    api_key="sk-T0BAsPpXyPnukpyZ0aE446EaD7604cB39f081a55271cF130",
    base_url="https://aihubmix.com/v1",
)

MODEL = "glm-5"

SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
]

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "assistant", "content": "你好"},
]
response = client.chat.completions.create(
    model=MODEL,
    messages=messages,  # type: ignore
    tools=TOOLS,  # type: ignore
    max_tokens=8000,
)

print(response.choices[0].message.content)
print(response)
