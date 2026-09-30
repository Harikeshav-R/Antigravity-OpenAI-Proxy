import json
import logging
from typing import Set

logger = logging.getLogger(__name__)

class PlanningLeakFilter:
    def __init__(self, tool_names: Set[str]):
        self.tool_names = tool_names
        self.is_buffering = True
        self.buffer = ""

    def filter_text(self, text: str) -> str:
        if not self.is_buffering:
            return text

        self.buffer += text
        trimmed = self.buffer.lstrip()
        if not trimmed.startswith("{"):
            self.is_buffering = False
            out = self.buffer
            self.buffer = ""
            return out

        # Try to locate closing brace
        depth = 0
        end_idx = -1
        for i, ch in enumerate(trimmed):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        if end_idx != -1:
            json_candidate = trimmed[:end_idx + 1]
            try:
                parsed = json.loads(json_candidate)
                # Check leak signatures
                is_leak = (
                    "thought" in parsed or
                    "call" in parsed or
                    "_i" in parsed or
                    "command" in parsed or
                    any(k in parsed for k in self.tool_names)
                )
                self.is_buffering = False
                rest = trimmed[end_idx + 1:]
                self.buffer = ""
                return rest if is_leak else (json_candidate + rest)
            except (json.JSONDecodeError, TypeError, ValueError) as err:
                logger.debug("Candidate JSON not yet valid or complete: %s", err)

        if len(self.buffer) > 4096:
            # Buffer limit exceeded without valid JSON, release
            self.is_buffering = False
            out = self.buffer
            self.buffer = ""
            return out

        return ""

    def flush(self) -> str:
        if self.buffer:
            out = self.buffer
            self.buffer = ""
            self.is_buffering = False
            return out
        return ""
