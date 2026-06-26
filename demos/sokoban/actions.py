import json
import re
from typing import Any

ACTIONS = {"up": 0, "right": 1, "down": 2, "left": 3}
ALIASES = {"u": "up", "r": "right", "d": "down", "l": "left", "0": "up", "1": "right", "2": "down", "3": "left"}
ACTION_RE = re.compile(r"\b(up|right|down|left|u|r|d|l|0|1|2|3)\b", re.I)


def normalize_action(action: str | int) -> str:
    text = str(action).strip().lower()
    action = ALIASES.get(text, text)
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    return action


def action_id(action: str | int) -> int:
    return ACTIONS[normalize_action(action)]


def parse_action_sequence(text: str) -> list[str]:
    for candidate in (text.strip(), between(text, "{", "}"), between(text, "[", "]")):
        try:
            actions = extract_actions(json.loads(candidate))
        except (json.JSONDecodeError, ValueError):
            continue
        if actions:
            return actions
    return [normalize_action(match.group(1)) for match in ACTION_RE.finditer(text)]


def between(text: str, start: str, end: str) -> str:
    return text[text.find(start) : text.rfind(end) + 1] if start in text and end in text else ""


def extract_actions(value: Any) -> list[str]:
    if isinstance(value, dict):
        return extract_actions(value.get("actions", []))
    if isinstance(value, list):
        return [normalize_action(item) for item in value]
    if isinstance(value, str | int):
        return [normalize_action(value)]
    return []
