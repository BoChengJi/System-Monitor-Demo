
import json
from pathlib import Path

# 延遲載入，避免部署未安裝 pyodbc 就報錯
try:
    import pyodbc  # noqa: F401
    HAS_PYODBC = True
except Exception:
    HAS_PYODBC = False

import sqlite3

CONFIG = json.loads(Path("config.json").read_text(encoding="utf-8"))

def get_conn():
    db_type = CONFIG["db"]["type"]
    if db_type == "sqlite":
        return sqlite3.connect(CONFIG["db"]["sqlite_path"])
    elif db_type == "mssql":
        if not HAS_PYODBC:
            raise RuntimeError("pyodbc 未安裝，請先安裝並設定 ODBC Driver 18 for SQL Server")
        cfg = CONFIG["db"]["mssql"]
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={cfg['server']},{cfg['port']};"
            f"DATABASE={cfg['database']};"
            f"UID={cfg['username']};PWD={cfg['password']};"
            f"Encrypt=yes;TrustServerCertificate=yes"
        )
        import pyodbc
        return pyodbc.connect(conn_str)
    else:
        raise ValueError("Unsupported DB type in config.json")
