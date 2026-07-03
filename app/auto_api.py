import inspect
import re
from typing import Any, get_type_hints

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse


def extract_docs(fn):
    doc = inspect.getdoc(fn) or ""
    lines = doc.splitlines()

    description_lines = []
    param_docs = {}
    in_args = False

    for line in lines:
        s = line.strip()

        if s in {"Args:", "Arguments:", "Parameters:"}:
            in_args = True
            continue

        if not in_args:
            if s:
                description_lines.append(s)
            continue

        if not s:
            continue

        m = re.match(r"^(\w+)(?:\s*\([^)]*\))?\s*:\s*(.+)$", s)
        if m:
            param_docs[m.group(1)] = m.group(2).strip()

    description = " ".join(description_lines).strip()
    return description, param_docs


def register_get(
    router: APIRouter,
    *,
    path: str,
    fn,
    summary: str,
    response_key: str = "result",
):
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    description, param_docs = extract_docs(fn)

    endpoint_params = []

    for name, param in sig.parameters.items():
        ann = hints.get(name, Any)
        desc = param_docs.get(name, name)

        if param.default is inspect._empty:
            default = Query(..., description=desc)
        else:
            default = Query(param.default, description=desc)

        endpoint_params.append(
            inspect.Parameter(
                name=name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=ann,
            )
        )

    def endpoint(**kwargs):
        result = fn(**kwargs)
        return JSONResponse(content={response_key: result})

    endpoint.__name__ = f"ep_{fn.__name__}_{path.strip('/').replace('/', '_') or 'root'}"
    endpoint.__signature__ = inspect.Signature(parameters=endpoint_params)

    router.get(path, summary=summary, description=description)(endpoint)