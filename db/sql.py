"""SQL lexical utilities shared by database backends.

This module intentionally performs only placeholder adaptation. It does not
translate SQLite SQL syntax or attempt to make dialect-specific statements
portable.
"""


class SQLPlaceholderError(ValueError):
    """Raised when SQL placeholders do not match the supplied parameters."""


def qmark_to_format(sql, parameter_count):
    """Convert exactly *parameter_count* unquoted qmark placeholders to %s.

    Question marks inside SQL strings, quoted identifiers, line/block comments,
    and PostgreSQL dollar-quoted bodies are preserved. Nested block comments
    are supported. PostgreSQL's doubled question-mark operator escape (??) is
    preserved for Psycopg to interpret.

    The explicit parameter count makes a single PostgreSQL '?' operator
    distinguishable from a bind marker: only the required number of markers
    are converted, and callers must review each converted statement.
    """
    if not isinstance(sql, str):
        raise TypeError("sql must be a string")
    if not isinstance(parameter_count, int) or parameter_count < 0:
        raise ValueError("parameter_count must be a non-negative integer")

    out = []
    i = 0
    converted = 0
    length = len(sql)

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        # Single-quoted strings; E'...' additionally treats backslash as escape.
        if ch == "'":
            start = i
            prefix_is_escape = (
                i > 0 and sql[i - 1] in "eE"
                and (i == 1 or not (sql[i - 2].isalnum() or sql[i - 2] in "_$"))
            )
            i += 1
            closed = False
            while i < length:
                if prefix_is_escape and sql[i] == "\\\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < length and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                i += 1
            if not closed:
                raise SQLPlaceholderError("unterminated SQL string literal")
            out.append(sql[start:i])
            continue

        # Quoted identifiers; doubled quotes escape a quote.
        if ch == '"':
            start = i
            i += 1
            while i < length:
                if sql[i] == '"':
                    if i + 1 < length and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            if i == length and (not sql or sql[-1] != '"'):
                raise SQLPlaceholderError("unterminated quoted SQL identifier")
            out.append(sql[start:i])
            continue

        # SQL line comments.
        if ch == "-" and nxt == "-":
            start = i
            i += 2
            while i < length and sql[i] not in "\r\n":
                i += 1
            out.append(sql[start:i])
            continue

        # SQL block comments can be nested in PostgreSQL.
        if ch == "/" and nxt == "*":
            start = i
            i += 2
            depth = 1
            while i < length and depth:
                pair = sql[i:i + 2]
                if pair == "/*":
                    depth += 1
                    i += 2
                elif pair == "*/":
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise SQLPlaceholderError("unterminated SQL block comment")
            out.append(sql[start:i])
            continue

        # PostgreSQL dollar-quoted strings: $$...$$ or $tag$...$tag$.
        if ch == "$":
            j = i + 1
            while j < length and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < length and sql[j] == "$":
                delimiter = sql[i:j + 1]
                end = sql.find(delimiter, j + 1)
                if end == -1:
                    raise SQLPlaceholderError("unterminated PostgreSQL dollar-quoted string")
                else:
                    end += len(delimiter)
                    out.append(sql[i:end])
                    i = end
                continue

        if ch == "?" and nxt == "?":
            out.append("??")
            i += 2
            continue

        if ch == "?":
            if converted >= parameter_count:
                raise SQLPlaceholderError(
                    "SQL contains more unquoted qmark placeholders than parameters"
                )
            out.append("%s")
            converted += 1
            i += 1
            continue

        out.append(ch)
        i += 1

    if converted != parameter_count:
        raise SQLPlaceholderError(
            f"SQL contains {converted} convertible placeholders; "
            f"{parameter_count} parameters were supplied"
        )
    return "".join(out)
