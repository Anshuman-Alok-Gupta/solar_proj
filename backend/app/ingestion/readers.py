"""File readers for CSV / XLSX / XLS exports with header-row detection.

Monitoring-app exports frequently contain title rows, merged headers or trailing
summary rows. We therefore load the raw grid first, locate the most plausible header
row, and only then build the DataFrame.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".csv", ".txt", ".xlsx", ".xlsm", ".xls"}
MAX_HEADER_SCAN_ROWS = 30


class UnsupportedFileError(ValueError):
    pass


@dataclass(slots=True)
class LoadedTable:
    df: pd.DataFrame
    file_format: str
    sheet_name: str | None
    header_row: int
    sheets: list[str] = field(default_factory=list)
    encoding: str | None = None
    delimiter: str | None = None
    notes: list[str] = field(default_factory=list)
    layout: str | None = None  # None = plain table, or e.g. "sems_wide_operation_report"
    meta: dict = field(default_factory=dict)


def detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise UnsupportedFileError(f"Unsupported file type '{ext}'. Supported: CSV, XLSX, XLS")
    return ext.lstrip(".")


def _looks_numeric(value) -> bool:
    try:
        float(str(value).replace(",", ""))
        return True
    except ValueError:
        return False


def _score_header_row(row: list) -> float:
    """Heuristic: header rows are mostly non-empty short strings and contain no numbers."""
    cells = [c for c in row if c is not None and str(c).strip() not in ("", "nan", "None")]
    if len(cells) < 2:
        return 0.0
    strings = sum(1 for c in cells if isinstance(c, str) and not _looks_numeric(c))
    return strings / len(cells) * min(len(cells), 12)


def _find_header_row(grid: list[list]) -> int:
    best_idx, best_score = 0, -1.0
    for i, row in enumerate(grid[:MAX_HEADER_SCAN_ROWS]):
        score = _score_header_row(row)
        if i + 1 < len(grid):
            nxt = grid[i + 1]
            if any(_looks_numeric(c) for c in nxt if c is not None):
                score += 1.0
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def _grid_to_frame(grid: list[list], header_row: int) -> pd.DataFrame:
    header = [
        str(c).strip() if c is not None and str(c).strip() not in ("", "nan", "None") else f"column_{i + 1}"
        for i, c in enumerate(grid[header_row])
    ]
    seen: dict[str, int] = {}
    for i, h in enumerate(header):
        if h in seen:
            seen[h] += 1
            header[i] = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
    body = grid[header_row + 1 :]
    width = len(header)
    rows = [list(r[:width]) + [None] * (width - len(r)) for r in body]
    df = pd.DataFrame(rows, columns=header)
    df = df.replace({"": None})
    df = df.dropna(how="all")
    keep = [c for c in df.columns if not c.startswith("column_") or df[c].notna().any()]
    return df[keep].reset_index(drop=True)


def _read_csv_grid(path: Path) -> tuple[list[list], str, str]:
    raw = path.read_bytes()
    text, encoding = None, "utf-8-sig"
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:  # pragma: no cover
        text = raw.decode("utf-8", errors="replace")
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    grid = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return grid, encoding, delimiter


def _maybe_wide(
    grid: list[list], fmt: str, sheet: str | None, sheets: list[str], notes: list[str], **kw
) -> LoadedTable | None:
    from app.ingestion.wide_reports import detect_wide_report, unpivot_wide_report

    hit = detect_wide_report(grid)
    if not hit:
        return None
    kind, hdr = hit
    wide = unpivot_wide_report(grid, kind, hdr)
    return LoadedTable(
        df=wide.df,
        file_format=fmt,
        sheet_name=sheet,
        header_row=hdr,
        sheets=sheets,
        notes=notes + wide.notes,
        layout=wide.meta.get("layout"),
        meta=wide.meta,
        **kw,
    )


def load_table(path: Path, *, sheet_name: str | None = None, header_row: int | None = None) -> LoadedTable:
    fmt = detect_format(path.name)
    notes: list[str] = []
    if fmt in ("csv", "txt"):
        grid, encoding, delimiter = _read_csv_grid(path)
        wide = _maybe_wide(grid, "csv", None, [], notes, encoding=encoding, delimiter=delimiter)
        if wide:
            return wide
        hdr = header_row if header_row is not None else _find_header_row(grid)
        if hdr > 0:
            notes.append(f"Skipped {hdr} title row(s) before the header")
        df = _grid_to_frame(grid, hdr)
        return LoadedTable(
            df=df,
            file_format="csv",
            sheet_name=None,
            header_row=hdr,
            encoding=encoding,
            delimiter=delimiter,
            notes=notes,
        )

    engine = "xlrd" if fmt == "xls" else "openpyxl"
    with pd.ExcelFile(path, engine=engine) as xf:
        sheets = [str(s) for s in xf.sheet_names]
        target = sheet_name or sheets[0]
        if target not in sheets:
            raise UnsupportedFileError(f"Sheet '{target}' not found. Available: {sheets}")
        raw = xf.parse(target, header=None, dtype=object)
    grid = raw.astype(object).where(pd.notna(raw), None).values.tolist()
    wide = _maybe_wide(grid, fmt, target, sheets, notes)
    if wide:
        return wide
    hdr = header_row if header_row is not None else _find_header_row(grid)
    if hdr > 0:
        notes.append(f"Skipped {hdr} title row(s) before the header")
    df = _grid_to_frame(grid, hdr)
    return LoadedTable(df=df, file_format=fmt, sheet_name=target, header_row=hdr, sheets=sheets, notes=notes)
