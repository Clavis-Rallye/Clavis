#!/usr/bin/env python3
"""Check that every relative link of the tracked Markdown files leads somewhere.

Run from the repository root:

    python3 scripts/check-links.py              # check the repository
    python3 scripts/check-links.py --self-test  # run the script's own cases

Python standard library only, no network call. The files are the `*.md` files
`git ls-files` lists. A link is an inline link or image `[text](target)`, a
reference definition `[name]: target`, or the `href`/`src` of an HTML `a`,
`img` or `source` tag. A link inside a fenced code block or an inline code
span is not a link. A target with a scheme (`https:`, `mailto:`, ...) or one
that starts with `//` is not checked.

A path must name a tracked file or a folder that holds a tracked file. An
anchor must name a heading (GitHub's id rules) or an HTML `id`/`name` of the
Markdown file it points to.

Output: one line `path:line: reason: target` per broken link, then one summary
line. Exit 1 when a link is broken, else 0.
"""

import html
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote

REASON_MISSING_PATH = "no such file or folder"
REASON_MISSING_ANCHOR = "no such heading or id"
REASON_OUTSIDE = "leaves the repository"

SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
CODE_SPAN_RE = re.compile(
    r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n)[\s\S])+?)(?<!`)\1(?!`)"
)
REF_DEF_RE = re.compile(
    r"^ {0,3}\[(?!\^)(?:[^\]\\\n]|\\.)+\]:[ \t]*(<[^>\n]*>|\S+)", re.MULTILINE
)
HTML_TAG_RE = re.compile(r"<(a|img|source)\b([^>]*)>", re.IGNORECASE)
HTML_LINK_ATTR_RE = re.compile(
    r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE
)
HTML_ANY_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")
HTML_ID_ATTR_RE = re.compile(
    r"""\s(?:id|name)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE
)
ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def clean_env():
    """The environment without GIT_* variables (a hook would point them elsewhere)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, env=clean_env(), check=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    ).stdout


def tracked_files(root):
    out = git(root, "ls-files", "-z")
    return sorted(p for p in out.split("\0") if p)


def blank(s):
    """Replace every character but the newlines with a space (keeps offsets)."""
    return re.sub(r"[^\n]", " ", s)


def blank_fences(text):
    """Blank the fenced code blocks, fence lines included."""
    lines = text.split("\n")
    fence = None  # (char, length) while inside a block
    for i, line in enumerate(lines):
        if fence is None:
            m = FENCE_OPEN_RE.match(line)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = (m.group(1)[0], len(m.group(1)))
                lines[i] = blank(line)
        else:
            lines[i] = blank(line)
            m = re.match(r"^\s*(`{3,}|~{3,})\s*$", line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
    return "\n".join(lines)


def blank_code_spans(text):
    return CODE_SPAN_RE.sub(lambda m: blank(m.group(0)), text)


def inline_targets(text):
    """Yield (offset, target) of every `](target)` of the text."""
    n = len(text)
    i = 0
    while True:
        j = text.find("](", i)
        if j < 0:
            return
        k = j + 2
        while k < n and text[k] in " \t\n":
            k += 1
        if k < n and text[k] == "<":
            e = text.find(">", k + 1)
            nl = text.find("\n", k + 1)
            if e < 0 or 0 <= nl < e:
                i = j + 2
                continue
            target = text[k + 1:e]
            i = e + 1
        else:
            depth = 0
            e = k
            while e < n:
                c = text[e]
                if c == "\\" and e + 1 < n:
                    e += 2
                    continue
                if c in " \t\n":
                    break
                if c == "(":
                    depth += 1
                elif c == ")":
                    if depth == 0:
                        break
                    depth -= 1
                e += 1
            target = text[k:e]
            i = max(e, j + 2)
        yield k, re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", target)


def extract_links(text):
    """Return [(line, target)] of the links of a Markdown text."""
    text = blank_code_spans(blank_fences(text))
    found = []
    for off, target in inline_targets(text):
        found.append((off, target))
    for m in REF_DEF_RE.finditer(text):
        t = m.group(1)
        if t.startswith("<") and t.endswith(">"):
            t = t[1:-1]
        found.append((m.start(1), t))
    for tag in HTML_TAG_RE.finditer(text):
        base = tag.start(2)
        for a in HTML_LINK_ATTR_RE.finditer(tag.group(2)):
            value = next(g for g in a.groups() if g is not None)
            found.append((base + a.start(), html.unescape(value)))
    found.sort()
    return [(text.count("\n", 0, off) + 1, target) for off, target in found]


def heading_text(raw):
    """Reduce the source of a heading to its rendered text."""
    codes = []

    def stash(m):
        codes.append(m.group(2).strip())
        return "\x00%d\x00" % (len(codes) - 1)

    s = CODE_SPAN_RE.sub(stash, raw)
    s = re.sub(r"<[^>]*>", "", s)
    for _ in range(2):
        s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", s)
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"(?<![A-Za-z0-9_])_(.+?)_(?![A-Za-z0-9_])", r"\1", s)
    s = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)
    return html.unescape(s).strip()


def slug(text):
    s = re.sub(r"[^\w\- ]", "", text.lower())
    return s.replace(" ", "-")


def anchors_of(text):
    """The set of anchors (heading ids and HTML ids/names) of a Markdown text."""
    nofence = blank_fences(text)
    lines = nofence.split("\n")
    ids = set()
    occurrences = {}

    def add(heading):
        base = slug(heading_text(heading))
        s = base
        if s in occurrences:
            while s in occurrences:
                occurrences[base] += 1
                s = "%s-%d" % (base, occurrences[base])
        occurrences[s] = 0
        ids.add(s)

    for i, line in enumerate(lines):
        m = ATX_RE.match(line)
        if m:
            add(m.group(1) or "")
        elif SETEXT_RE.match(line) and i > 0 and lines[i - 1].strip():
            if ATX_RE.match(lines[i - 1]):
                continue
            j = i - 1
            while j > 0 and lines[j - 1].strip() and not ATX_RE.match(lines[j - 1]):
                j -= 1
            add(" ".join(x.strip() for x in lines[j:i]))
    for tag in HTML_ANY_TAG_RE.finditer(blank_code_spans(nofence)):
        for a in HTML_ID_ATTR_RE.finditer(tag.group(0)):
            ids.add(a.group(1) if a.group(1) is not None else a.group(2))
    return ids


def check(root):
    """Check the repository at root. Return (broken, n_files, n_links)."""
    files = tracked_files(root)
    tracked = set(files)
    folders = {"."}
    for f in files:
        d = posixpath.dirname(f)
        while d:
            folders.add(d)
            d = posixpath.dirname(d)
    anchor_cache = {}

    def anchors(path):
        if path not in anchor_cache:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                anchor_cache[path] = anchors_of(fh.read().replace("\r\n", "\n"))
        return anchor_cache[path]

    broken = []
    n_files = n_links = 0
    for src in files:
        if not src.endswith(".md"):
            continue
        n_files += 1
        with open(os.path.join(root, src), encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\r\n", "\n")
        for line, raw in extract_links(text):
            target = raw.strip()
            if not target or SCHEME_RE.match(target) or target.startswith("//"):
                continue
            n_links += 1
            path, _, fragment = target.partition("#")
            path = unquote(path.partition("?")[0])
            fragment = unquote(fragment)
            if not path:
                resolved = src
            else:
                if path.startswith("/"):
                    resolved = posixpath.normpath(path.lstrip("/") or ".")
                else:
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname(src), path)
                    )
                if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
                    broken.append((src, line, REASON_OUTSIDE, raw))
                    continue
                if resolved not in tracked and resolved not in folders:
                    broken.append((src, line, REASON_MISSING_PATH, raw))
                    continue
            if fragment and resolved in tracked and resolved.endswith(".md"):
                if fragment not in anchors(resolved):
                    broken.append((src, line, REASON_MISSING_ANCHOR, raw))
    return broken, n_files, n_links


def run(root):
    broken, n_files, n_links = check(root)
    for src, line, reason, target in broken:
        print("%s:%d: %s: %s" % (src, line, reason, target))
    print(
        "check-links: %d Markdown files, %d relative links, %d broken"
        % (n_files, n_links, len(broken))
    )
    return 1 if broken else 0


# --- self-test ---------------------------------------------------------------

TWO_HEADINGS = "# Same\n\n# Same\n"

# name -> (files, expected [(path, reason)]); `a.md` is the file under test.
CASES = {
    "link to a file": ({"a.md": "[x](b.md)\n", "b.md": "b\n"}, []),
    "link to a folder": ({"a.md": "[x](d/)\n", "d/f.txt": "f\n"}, []),
    "link to a heading of another file": (
        {"a.md": "[x](b.md#some-title)\n", "b.md": "# Some Title\n"}, []),
    "link to a heading in the same file": (
        {"a.md": "# Top\n\n[x](#top)\n"}, []),
    "link to the second of two equal headings": (
        {"a.md": "[x](b.md#same-1)\n", "b.md": TWO_HEADINGS}, []),
    "link to a heading with code and bold": (
        {"a.md": "[x](b.md#the-foo_bar-call-now)\n",
         "b.md": "## The `foo_bar()` call **now**\n"}, []),
    "link to a setext heading": (
        {"a.md": "[x](b.md#title-two)\n", "b.md": "Title Two\n=========\n"}, []),
    "link to an HTML id": (
        {"a.md": "[x](b.md#custom)\n", "b.md": '<a id="custom"></a>\n'}, []),
    "image with a title": (
        {"a.md": '![alt](img.png "A title")\n', "img.png": "x"}, []),
    "target in angle brackets": (
        {"a.md": "[x](<my doc.md>)\n", "my doc.md": "x\n"}, []),
    "percent-encoded path and query": (
        {"a.md": "[x](my%20doc.md?raw=1)\n", "my doc.md": "x\n"}, []),
    "rooted path": (
        {"d/a.md": "[x](/b.md)\n", "b.md": "x\n"}, []),
    "https and mailto targets": (
        {"a.md": "[x](https://example.invalid/x) [y](mailto:a@example.invalid) "
                 "[z](//example.invalid/z)\n"}, []),
    "missing file": (
        {"a.md": "[x](nope.md)\n"}, [("a.md", REASON_MISSING_PATH)]),
    "missing anchor in another file": (
        {"a.md": "[x](b.md#nope)\n", "b.md": "# Yes\n"},
        [("a.md", REASON_MISSING_ANCHOR)]),
    "missing anchor in the same file": (
        {"a.md": "# Yes\n\n[x](#nope)\n"}, [("a.md", REASON_MISSING_ANCHOR)]),
    "missing image": (
        {"a.md": "![alt](missing.png)\n"}, [("a.md", REASON_MISSING_PATH)]),
    "path that leaves the repository": (
        {"a.md": "[x](../outside.md)\n"}, [("a.md", REASON_OUTSIDE)]),
    "reference definition to a missing file": (
        {"a.md": "[x][r]\n\n[r]: nope.md\n"}, [("a.md", REASON_MISSING_PATH)]),
    "img src to a missing file": (
        {"a.md": '<img src="nope.png" alt="x">\n'}, [("a.md", REASON_MISSING_PATH)]),
    "a href to a missing file": (
        {"a.md": "<a href='nope.md'>x</a>\n"}, [("a.md", REASON_MISSING_PATH)]),
    "link in a fenced block": (
        {"a.md": "```\n[x](nope.md)\n```\n\n~~~md\n[y](nope.md)\n~~~\n"}, []),
    "link in an inline code span": (
        {"a.md": "Write `[x](nope.md)` like this.\n"}, []),
}


def self_test():
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, (name, (files, expected)) in enumerate(CASES.items()):
            repo = os.path.join(tmp, "case%d" % i, "repo")
            os.makedirs(repo)
            git(repo, "init", "-q")
            for path, content in files.items():
                full = os.path.join(repo, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(content)
            git(repo, "add", "-A")
            broken, _, _ = check(repo)
            got = sorted((src, reason) for src, _, reason, _ in broken)
            want = sorted(expected)
            ok = got == want
            print("%s %s" % ("ok  " if ok else "FAIL", name))
            if not ok:
                failed += 1
                print("     expected %r, got %r" % (want, got))
    print("check-links self-test: %d cases, %d failed" % (len(CASES), failed))
    return 1 if failed else 0


def main(argv):
    if argv[1:] == ["--self-test"]:
        return self_test()
    if len(argv) > 1:
        print("usage: check-links.py [--self-test]", file=sys.stderr)
        return 2
    return run(".")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
