#!/usr/bin/env python3
"""Write the router table of docs/development/api-structure.md from the code.

It reads every app.include_router(<module>.<router>, prefix=..., tags=[...]) call
of src/backend/app/main.py and writes a Markdown table with the columns Module,
Prefix and Tags, in the order of main.py. The table stands between the two marker
lines of the document.

    python3 scripts/docs-router-table.py             write the table into the document
    python3 scripts/docs-router-table.py --check     change nothing, exit 1 if it differs
    python3 scripts/docs-router-table.py --self-test run two cases on small sources, touch no file

Every mode prints one line. The script refuses a main.py with 0 include_router
calls: it writes nothing and exits 1, with and without --check.

Python 3, standard library only.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "src" / "backend" / "app" / "main.py"
DOC = ROOT / "docs" / "development" / "api-structure.md"
START = "<!-- router-table:start -->"
END = "<!-- router-table:end -->"


def _literal(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
        return ast.unparse(node)


def router_calls(source):
    """Return (module, attribute, prefix, tags) for each include_router call, in file order."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Attribute):
            continue
        target = node.args[0]
        module = ast.unparse(target.value)
        keywords = {kw.arg: kw.value for kw in node.keywords}
        prefix = _literal(keywords["prefix"]) if "prefix" in keywords else ""
        tags = _literal(keywords["tags"]) if "tags" in keywords else []
        calls.append((node.lineno, node.col_offset, module, target.attr, prefix, tags))
    calls.sort()
    return [c[2:] for c in calls]


def render(calls):
    rows = ["| Module | Prefix | Tags |", "|--------|--------|------|"]
    for module, attr, prefix, tags in calls:
        name = f"`{module}.py`" if attr == "router" else f"`{module}.py` (`{attr}`)"
        shown_prefix = f"`{prefix}`" if prefix else ""
        rows.append(f"| {name} | {shown_prefix} | {', '.join(tags)} |")
    return "\n".join(rows)


def splice(text, table):
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"{DOC.relative_to(ROOT)}: the markers {START} and {END} are missing")
    return text[: start + len(START)] + "\n" + table + "\n" + text[end:]


def process(source, text, check):
    """Return (exit code, message, new document text) for one main.py source and one document."""
    main_name = MAIN.relative_to(ROOT)
    doc_name = DOC.relative_to(ROOT)
    calls = router_calls(source)
    if not calls:
        return 1, f"{main_name}: found 0 include_router calls, nothing written", text
    new = splice(text, render(calls))
    count = len(calls)
    if check:
        if new != text:
            return (
                1,
                f"{doc_name}: the router table differs from main.py. "
                "Run python3 scripts/docs-router-table.py",
                text,
            )
        return 0, f"{doc_name}: the router table matches main.py, {count} routers", text
    if new != text:
        return 0, f"{doc_name}: wrote the router table, {count} routers", new
    return 0, f"{doc_name}: the router table is unchanged, {count} routers", text


SELF_TEST_DOC = f"# Doc\n{START}\nold\n{END}\n"
SELF_TEST_TWO = (
    "app.include_router(auth.router, prefix='/api/auth', tags=['auth'])\n"
    "app.include_router(runs.router, prefix='/api/runs', tags=['runs'])\n"
)
SELF_TEST_NONE = "app = object()\n"


def self_test():
    """Run the two cases on sources held here. Read and write no file."""
    ok = True
    code, message, new = process(SELF_TEST_TWO, SELF_TEST_DOC, check=False)
    rows = [line for line in new.splitlines() if line.startswith("| `")]
    two = code == 0 and len(rows) == 2 and "2 routers" in message
    print(f"self-test two include_router calls: {'ok' if two else 'FAILED'}: {message}")
    ok = ok and two
    code, message, new = process(SELF_TEST_NONE, SELF_TEST_DOC, check=False)
    refused = code == 1 and new == SELF_TEST_DOC and "found 0" in message
    print(f"self-test no include_router call: {'ok' if refused else 'FAILED'}: {message}")
    return 0 if ok and refused else 1


def main(argv):
    if "--self-test" in argv[1:]:
        return self_test()
    check = "--check" in argv[1:]
    code, message, new = process(
        MAIN.read_text(encoding="utf-8"), DOC.read_text(encoding="utf-8"), check
    )
    print(message)
    if not check and code == 0 and new != DOC.read_text(encoding="utf-8"):
        DOC.write_text(new, encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
