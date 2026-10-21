"""Incremental, string-aware guards for structured JSON generation only."""

import re


class JSONOutputGuard:
    def __init__(self):
        self.in_string = False
        self.escaped = False
        self.depth = 0
        self.started = False
        self.closed = False
        self.whitespace = 0
        self.trailing = 0
        self.size = 0
        self.syntax_tail = ""

    def feed(self, text):
        for char in text:
            self.size += 1
            if self.size > 1024 * 1024:
                return "response_size"
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.in_string = False
                continue
            self.syntax_tail = (self.syntax_tail + char)[-512:]
            self.whitespace = self.whitespace + 1 if char.isspace() else 0
            if self.whitespace >= 512:
                return "whitespace_loop"
            if self.closed and not char.isspace():
                self.trailing += 1
                if self.trailing >= 64:
                    return "trailing_output"
            if char == '"':
                self.in_string = True
            elif char in "[{":
                self.started = True
                self.depth += 1
            elif char in "]}":
                self.depth -= 1
                if self.started and self.depth == 0:
                    self.closed = True
        if re.search(r"(?:</(?:tool_call|function)>\s*){8,}$", self.syntax_tail):
            return "repeated_syntax"
        return None
