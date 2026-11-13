import sqlite3
from functools import lru_cache

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError


@lru_cache(maxsize=128)
def is_sql_mutation(sql: str) -> bool:
    """Fail closed unless SQL is a single read-only SQLite SELECT AST."""
    try:
        statements = parse(sql, read="sqlite")
    except (SqlglotError, RecursionError):
        return True
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return True
    blocked = (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Pragma, exp.Transaction)
    for node in statements[0].walk():
        if isinstance(node, blocked):
            return True
        if isinstance(node, exp.Anonymous) and node.name.casefold() in {"load_extension", "readfile", "writefile"}:
            return True
    return False


def read_query(connection: sqlite3.Connection, sql: str, parameters=()) -> sqlite3.Cursor:
    if is_sql_mutation(sql):
        raise ValueError("Only a single read-only SELECT is permitted")
    return connection.execute(sql, parameters)


def read_authorizer(action, arg1, arg2, database, source):
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").casefold() in {
        "count", "sum", "coalesce", "group_concat", "lower", "upper",
    }:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
