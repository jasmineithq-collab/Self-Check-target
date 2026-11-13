import base64
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook


DEFAULT_ROSTER = Path(__file__).with_name("private") / "staff list.xlsx"
CODE_PATTERN = re.compile(r"[A-Z0-9_-]{1,32}")


@dataclass(frozen=True)
class Staff:
    code: str
    name: str
    manager_code: str | None = None


def load_roster(encoded: str = "", path: str = "") -> dict[str, Staff]:
    source = Path(path).expanduser() if path else DEFAULT_ROSTER
    if not encoded and not path and not source.is_file():
        demo = (
            Staff("DSM001", "Mei Tan"), Staff("DSM002", "Farah Noor"),
            Staff("REP001", "Aina Rahman", "DSM001"), Staff("REP002", "Daniel Lim", "DSM001"),
            Staff("REP003", "Sam Lee", "DSM002"),
            Staff("SADSM1", "Demo Sales Manager"),
            Staff("JEWD", "Demo Salesperson", "SADSM1"),
            Staff("DEMOREP1", "Demo Salesperson 2", "SADSM1"),
        )
        return {staff.code: staff for staff in demo}

    workbook = load_workbook(
        BytesIO(base64.b64decode(encoded, validate=True)) if encoded else source,
        read_only=True, data_only=True,
    )
    try:
        rows = workbook["DSM Management"].values
        columns = {str(value).strip(): index for index, value in enumerate(next(rows))}
        managers: dict[str, Staff] = {}
        reps: dict[str, Staff] = {}
        for row in rows:
            if not any(value is not None for value in row):
                continue
            manager_code, manager_name, rep_code, rep_name = (
                str(row[columns[key]] or "").strip()
                for key in ("DSM Code", "DSM Name", "Salesman Code", "Salesman Name")
            )
            manager_code, rep_code = manager_code.upper(), rep_code.upper()
            if not (CODE_PATTERN.fullmatch(manager_code) and CODE_PATTERN.fullmatch(rep_code)
                    and manager_name and rep_name):
                raise ValueError("Invalid staff roster row")
            manager = Staff(manager_code, manager_name)
            if managers.setdefault(manager_code, manager) != manager or rep_code in reps:
                raise ValueError("Conflicting staff codes")
            reps[rep_code] = Staff(rep_code, rep_name, manager_code)
        if not reps or managers.keys() & reps.keys():
            raise ValueError("Invalid manager and salesperson roster")
        return managers | reps
    finally:
        workbook.close()
