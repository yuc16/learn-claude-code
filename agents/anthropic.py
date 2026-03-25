from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT_MODULE = Path(__file__).resolve().parent.parent / "anthropic.py"
SPEC = importlib.util.spec_from_file_location(
    "_learn_claude_code_anthropic", ROOT_MODULE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load compatibility module from {ROOT_MODULE}")

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

Anthropic = MODULE.Anthropic
MessageResponse = MODULE.MessageResponse
TextBlock = MODULE.TextBlock
ToolUseBlock = MODULE.ToolUseBlock
ensure_openai_codex_auth = MODULE.ensure_openai_codex_auth
refresh_openai_codex_auth = MODULE.refresh_openai_codex_auth
