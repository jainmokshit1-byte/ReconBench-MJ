"""Execute SQL blocks from the PostgreSQL Schema document statement by statement."""

from __future__ import annotations

import re

from alembic import op

_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


def split_postgres_sql(sql: str) -> list[str]:
    """Split PostgreSQL statements without splitting literals or dollar bodies."""
    statements: list[str] = []
    current: list[str] = []
    index = 0
    quote: str | None = None
    dollar_quote: str | None = None
    line_comment = False
    block_comment_depth = 0

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if line_comment:
            current.append(char)
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment_depth:
            current.append(char)
            if char == "/" and next_char == "*":
                current.append(next_char)
                block_comment_depth += 1
                index += 2
            elif char == "*" and next_char == "/":
                current.append(next_char)
                block_comment_depth -= 1
                index += 2
            else:
                index += 1
            continue

        if dollar_quote is not None:
            if sql.startswith(dollar_quote, index):
                current.append(dollar_quote)
                index += len(dollar_quote)
                dollar_quote = None
            else:
                current.append(char)
                index += 1
            continue

        if quote is not None:
            current.append(char)
            if char == quote:
                if next_char == quote:
                    current.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char == "-" and next_char == "-":
            current.extend((char, next_char))
            line_comment = True
            index += 2
        elif char == "/" and next_char == "*":
            current.extend((char, next_char))
            block_comment_depth = 1
            index += 2
        elif char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
        elif char == "$":
            match = _DOLLAR_QUOTE.match(sql, index)
            if match is None:
                current.append(char)
                index += 1
            else:
                dollar_quote = match.group(0)
                current.append(dollar_quote)
                index = match.end()
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()
            index += 1
        else:
            current.append(char)
            index += 1

    if quote is not None or dollar_quote is not None or block_comment_depth:
        raise ValueError("Unterminated PostgreSQL quote or comment in migration SQL")
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def execute_sql_block(sql: str) -> None:
    """Execute each complete SQL statement through the active Alembic connection."""
    for statement in split_postgres_sql(sql):
        op.execute(statement)
