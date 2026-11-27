"""Turn any polled JSON into spreadsheet rows, whatever its shape.

The largest list of objects in the response becomes the rows (for rev5 payloads that is ForecastData or
MMFData, so each point is a row). Every other scalar in the response is repeated on each row as context
(e.g. ForecastResponse.PlantName). Values are kept exactly as the plant sent them: "0.0" stays a string.
Responses that are not spec-shaped (e.g. KMLS/KALS MMF) flatten the same way, so every poll is exportable.
"""
import json
from typing import Any

ROW_KEY = "_row"  # 1-based position of the row in the response's record list


def _leaves(obj: Any, prefix: str, out: dict, skip: int | None = None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _leaves(v, f"{prefix}.{k}" if prefix else str(k), out, skip)
    elif isinstance(obj, list):
        if skip is not None and id(obj) == skip:
            return
        if all(not isinstance(x, (dict, list)) for x in obj):
            out[prefix] = json.dumps(obj, ensure_ascii=False)
        else:
            for i, x in enumerate(obj):
                _leaves(x, f"{prefix}[{i}]", out, skip)
    else:
        out[prefix] = obj


def _record_lists(obj: Any, path: str = ""):
    """Every list whose items are all objects, with its JSON path."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _record_lists(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            yield path, obj
        for i, x in enumerate(obj):
            yield from _record_lists(x, f"{path}[{i}]")


def flatten(body: Any) -> tuple[str | None, list[dict]]:
    """Returns (path of the list used as rows, rows). Each row maps column name to value."""
    lists = list(_record_lists(body))
    if not lists:
        out: dict = {}
        _leaves(body, "", out)
        return None, [out or {"value": body}]
    path, records = max(lists, key=lambda pl: len(pl[1]))  # first largest wins on ties
    context: dict = {}
    _leaves(body, "", context, skip=id(records))
    rows = []
    for i, rec in enumerate(records, 1):
        r: dict = {ROW_KEY: i}
        _leaves(rec, "", r)
        rows.append({**context, **r})
    return path, rows


def flatten_text(body_text: str | None) -> tuple[str | None, list[dict]]:
    """Flatten the response exactly as received (key order preserved). Non-JSON comes back as one row."""
    if body_text is None:
        return None, []
    try:
        body = json.loads(body_text)
    except ValueError:
        return None, [{"not_json": body_text[:32000]}]
    return flatten(body)
