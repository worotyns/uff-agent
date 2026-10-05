from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
from typing import Any

from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max,
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "sorted": sorted, "range": range, "map": map, "filter": filter,
    "any": any, "all": all, "isinstance": isinstance, "type": type,
    "enumerate": enumerate, "zip": zip, "reversed": reversed, "abs": abs, "round": round,
}


class JsonQuery(BaseSkill):
    name = "json_query"
    description = (
        "Process JSON data with a Python expression. FOR ANALYSIS ONLY — does not store data. "
        "To persist information use entity_create / entity_query."
        "Use for filtering, grouping, transforming JSON data. "
        "In the expression you have the variable `data` (parsed JSON). "
        "Examples: 'len(data)', '[x for x in data if x[\"price\"] < 100]', "
        "'data[\"items\"][0][\"name\"]', 'sum(x[\"amount\"] for x in data)'."
    )
    parameters = {
        "data": {
            "type": "string",
            "description": "JSON data to process",
        },
        "expression": {
            "type": "string",
            "description": "Python expression over the variable `data`. The result is returned as JSON.",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        data_str = kwargs.get("data", "")
        expression = kwargs.get("expression", "")
        if not data_str or not expression:
            return "Podaj zarówno 'data' (JSON) jak i 'expression' (wyrażenie)."

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError as e:
            return f"Błąd parsowania JSON: {e}"

        try:
            result = eval(expression, {"__builtins__": _SAFE_BUILTINS}, {"data": data})
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)[:5000]
        except Exception as e:
            return f"Błąd wyrażenia: {e}"


class SqlQuery(BaseSkill):
    name = "sql_query"
    description = (
        "Analyze CSV/JSON data via SQL in in-memory SQLite. FOR ANALYSIS ONLY — the database is discarded after the query. "
        "To persist information use entity_create / entity_query."
        "Data is loaded into a 'data' table in SQLite :memory:. "
        "CSV must have a header (the first row is column names). "
        "Examples: 'SELECT * FROM data', "
        "'SELECT category, AVG(price) FROM data GROUP BY category', "
        "'SELECT * FROM data WHERE price < 100 ORDER BY price'."
    )
    parameters = {
        "sql": {
            "type": "string",
            "description": "SQL query (SELECT). INSERT/UPDATE/DELETE are pointless — the database is discarded after the query.",
        },
        "csv_data": {
            "type": "string",
            "description": "CSV data with a header. If omitted, the 'data' table doesn't exist — provide CSV on first use.",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        sql = kwargs.get("sql", "")
        csv_data = kwargs.get("csv_data", "")
        if not sql:
            return "Podaj zapytanie SQL."

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        try:
            if csv_data:
                if not hasattr(self, "_tables_created"):
                    self._tables_created = set()
                reader = csv.DictReader(io.StringIO(csv_data))
                rows = list(reader)
                if not rows:
                    return "CSV nie zawiera danych."
                cols = list(rows[0].keys())
                placeholders = ", ".join(f":{c}" for c in cols)
                col_names = ", ".join(f'"{c}"' for c in cols)
                col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
                conn.execute(f"CREATE TABLE data ({col_defs})")
                conn.executemany(f"INSERT INTO data VALUES ({placeholders})", rows)
                conn.commit()
                logger.info("sql_query: loaded %d rows, columns=%s", len(rows), cols)

            cursor = conn.execute(sql)
            results = cursor.fetchall()

            if not results:
                return "Zapytanie zwróciło 0 wierszy."
            col_names = [d[0] for d in cursor.description]
            rows_out = [dict(zip(col_names, r)) for r in results]
            output = (
                f"Zwrocono {len(rows_out)} wierszy, kolumny: {col_names}\n\n"
                + json.dumps(rows_out, indent=2, ensure_ascii=False, default=str)[:5000]
            )
            return output
        except Exception as e:
            return f"Błąd SQL: {e}"
        finally:
            conn.close()


class DuckDbQuery(BaseSkill):
    name = "duckdb_query"
    description = (
        "Analyze CSV/JSON/Parquet data via DuckDB. FOR ANALYSIS ONLY — the database is discarded after the query. "
        "To persist information use entity_create / entity_query."
        "Useful for larger datasets where SQLite struggles. "
        "Provide files as a dict filename → content (CSV/JSON). "
    )
    parameters = {
        "sql": {
            "type": "string",
            "description": "SQL query (SELECT). DuckDB in :memory: — data is not persisted.",
        },
        "files": {
            "type": "object",
            "description": "Dict {filename: content}. The key is the table name, the value is CSV/JSON data.",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        sql = kwargs.get("sql", "")
        files: dict = kwargs.get("files", {}) or {}
        if not sql:
            return "Podaj zapytanie SQL."

        try:
            import duckdb
        except ImportError:
            return "DuckDB nie jest zainstalowane. Użyj 'sql_query' (SQLite) zamiast DuckDB, albo najpierw zainstaluj: pip install duckdb."

        conn = duckdb.connect(":memory:")
        try:
            for name, content in files.items():
                tbl = name.rsplit(".", 1)[0].replace("/", "_").replace("-", "_")
                f = io.StringIO(content)
                conn.execute(f"CREATE TABLE \"{tbl}\" AS SELECT * FROM read_csv_auto(f)")
                logger.info("duckdb_query: loaded table '%s' from %s (%d chars)", tbl, name, len(content))

            result = conn.execute(sql)
            rows = result.fetchall()
            if not rows:
                return "Zapytanie zwróciło 0 wierszy."
            col_names = [d[0] for d in result.description]
            rows_out = [dict(zip(col_names, r)) for r in rows]
            output = (
                f"Zwrocono {len(rows_out)} wierszy, kolumny: {col_names}\n\n"
                + json.dumps(rows_out, indent=2, ensure_ascii=False, default=str)[:5000]
            )
            return output
        except Exception as e:
            return f"Błąd DuckDB: {e}"
        finally:
            conn.close()
