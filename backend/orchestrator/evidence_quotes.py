"""Resolve formatting-equivalent citations back to verbatim source excerpts."""

_QUOTES = frozenset("\"'“”‘’")


def _normalized(text):
    characters, offsets = [], []
    for index, character in enumerate(text):
        character = '"' if character in _QUOTES else " " if character.isspace() else character
        if character == " " and characters and characters[-1] == " ":
            offsets[-1] = (offsets[-1][0], index + 1)
        else:
            characters.append(character)
            offsets.append((index, index + 1))
    return "".join(characters), offsets


def source_quote(quote, text):
    """Allow only quote-style/whitespace changes, returning original source bytes."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    if quote in text:
        return quote
    normalized_quote, _ = _normalized(quote.strip())
    normalized_text, offsets = _normalized(text)
    start = normalized_text.find(normalized_quote)
    if start < 0:
        return None
    return text[offsets[start][0] : offsets[start + len(normalized_quote) - 1][1]]
