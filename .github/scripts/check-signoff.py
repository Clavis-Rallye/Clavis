#!/usr/bin/env python3
"""Check that every commit of a pull request carries the sign-off of its author (DCO).

Input on stdin: one JSON object per line, as
    gh api repos/<repo>/pulls/<n>/commits --paginate --jq '.[]'
prints the commits of a pull request. The commits are read as data only. Nothing from a commit
reaches a shell, and nothing the author controls is printed: the output holds the short sha and
fixed texts.

Environment:
    PR_AUTHOR_ID        id of the account that opened the pull request
    PR_COMMITS          number of commits of the pull request, from the event. When set, it must
                        equal the number of commits read from stdin, else the run fails (the API
                        lists at most 250 commits). When not set, the count is not checked.
    GITHUB_STEP_SUMMARY path of the job summary, when set

Run `check-signoff.py --self-test` to run the built-in cases.
Exit code: 0 when every commit passes, 1 when one fails, the input is empty or the input is bad.
"""

from __future__ import annotations

import json
import os
import re
import sys

DEPENDABOT_ID = 49699333
SIGNOFF = re.compile(r"^signed-off-by:[ \t]*(.*?)[ \t]*<([^<>]*)>[ \t]*$", re.IGNORECASE)
TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]+\S")

FIX_TEXT = (
    "Every commit needs the line 'Signed-off-by: Your Name <you@example.org>' with the name and "
    "email of its author. Fix it:\n"
    "  last commit:      git commit --amend --no-edit -s\n"
    "  all commits:      git rebase --signoff origin/main, then git push --force-with-lease\n"
    "Details: CONTRIBUTING.md, section 'Licence of your contribution'."
)


def _norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _trailer_block(message: str) -> list[str]:
    """Return the lines of the trailer block, or [] when the message has none.

    The block is the last paragraph, and every line of it has the form `Key: value`. The first
    paragraph is the subject and is never a trailer block.
    """
    lines = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line.strip() == "":
            if paragraphs[-1]:
                paragraphs.append([])
        else:
            paragraphs[-1].append(line)
    if not paragraphs[-1]:
        paragraphs.pop()
    if len(paragraphs) < 2:
        return []
    last = paragraphs[-1]
    if all(TRAILER.match(line) for line in last):
        return last
    return []


def _is_dependabot(commit: dict, pr_author_id: str) -> bool:
    author = commit.get("author")
    meta = commit.get("commit")
    if not isinstance(author, dict) or not isinstance(meta, dict):
        return False
    verification = meta.get("verification")
    return (
        author.get("id") == DEPENDABOT_ID
        and author.get("type") == "Bot"
        and isinstance(verification, dict)
        and verification.get("verified") is True
        and pr_author_id.strip() == str(DEPENDABOT_ID)
    )


def judge(commit: dict, pr_author_id: str) -> tuple[str, str]:
    """Return (verdict, reason). The verdict is 'ok', 'skip' or 'FAIL'."""
    parents = commit.get("parents")
    if isinstance(parents, list) and len(parents) > 1:
        return "skip", "merge commit"
    if _is_dependabot(commit, pr_author_id):
        return "ok", "Dependabot commit signed by GitHub"
    meta = commit.get("commit")
    if not isinstance(meta, dict):
        return "FAIL", "no commit data"
    author = meta.get("author")
    if not isinstance(author, dict):
        return "FAIL", "no commit author"
    a_name = author.get("name")
    a_email = author.get("email")
    message = meta.get("message")
    if not (isinstance(a_name, str) and isinstance(a_email, str) and isinstance(message, str)):
        return "FAIL", "no author name, email or message"
    block = _trailer_block(message)
    if not block:
        return "FAIL", "no Signed-off-by line in a trailer block"
    found = False
    for line in block:
        m = SIGNOFF.match(line)
        if not m:
            continue
        found = True
        name, email = m.group(1), m.group(2)
        if email.strip().casefold() == a_email.strip().casefold() and _norm_name(
            name
        ) == _norm_name(a_name):
            return "ok", "signed off by its author"
    if found:
        return "FAIL", "Signed-off-by names another person than the author"
    return "FAIL", "no Signed-off-by line in a trailer block"


def _short_sha(commit: dict) -> str:
    sha = commit.get("sha")
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
        return sha[:7]
    return "?"


def check(
    lines: list[str], pr_author_id: str, pr_commits: str | None = None
) -> tuple[list[tuple[str, str, str]], bool]:
    """Judge every line. Return the rows (sha, verdict, reason) and whether the input is sound."""
    rows: list[tuple[str, str, str]] = []
    sound = True
    read = 0
    for raw in lines:
        if not raw.strip():
            continue
        try:
            commit = json.loads(raw)
        except ValueError:
            rows.append(("?", "FAIL", "input line is not JSON"))
            sound = False
            continue
        if not isinstance(commit, dict):
            rows.append(("?", "FAIL", "input line is not a commit object"))
            sound = False
            continue
        read += 1
        verdict, reason = judge(commit, pr_author_id)
        rows.append((_short_sha(commit), verdict, reason))
    if pr_commits is not None:
        if not re.fullmatch(r"[0-9]+", pr_commits.strip()):
            rows.append(("-", "FAIL", "PR_COMMITS is not a number"))
            sound = False
        elif int(pr_commits.strip()) != read:
            rows.append(("-", "FAIL", f"the API listed {read} of {int(pr_commits.strip())} commits"))
            sound = False
    if not rows:
        rows.append(("-", "FAIL", "no commits on stdin"))
        sound = False
    return rows, sound


def run(
    lines: list[str],
    pr_author_id: str,
    summary_path: str | None,
    pr_commits: str | None = None,
) -> int:
    if pr_commits is None:
        print("commit count not checked: PR_COMMITS is not set")
    rows, sound = check(lines, pr_author_id, pr_commits)
    for sha, verdict, reason in rows:
        print(f"{sha} {verdict} {reason}")
    failed = (not sound) or any(v == "FAIL" for _, v, _ in rows)
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("### DCO sign-off\n\n| Commit | Verdict | Reason |\n|---|---|---|\n")
                for sha, verdict, reason in rows:
                    fh.write(f"| `{sha}` | {verdict} | {reason} |\n")
                fh.write("\n")
                if failed:
                    fh.write("```\n" + FIX_TEXT + "\n```\n")
        except OSError as exc:
            print(f"cannot write the job summary: {exc.__class__.__name__}", file=sys.stderr)
    if failed:
        print()
        print(FIX_TEXT)
        return 1
    return 0


def _commit(
    message: str,
    *,
    name: str = "Ada Example",
    email: str = "ada@example.org",
    parents: int = 1,
    gh_author: dict | None = None,
    verified: bool = False,
    sha: str = "0123456789abcdef0123456789abcdef01234567",
    extra: dict | None = None,
) -> dict:
    obj: dict = {
        "sha": sha,
        "parents": [{"sha": "f" * 40} for _ in range(parents)],
        "author": gh_author,
        "commit": {
            "author": {"name": name, "email": email},
            "message": message,
            "verification": {"verified": verified},
        },
    }
    if extra:
        obj.update(extra)
    return obj


def self_test() -> int:
    ok_msg = "fix(x): y\n\nSigned-off-by: Ada Example <ada@example.org>"
    bot = {"login": "dependabot[bot]", "id": DEPENDABOT_ID, "type": "Bot"}
    dep_msg = "chore(deps): bump x from 1 to 2"
    meta = "$(id) `id` \"; exit 0; \" | cat && echo pwned"
    cases: list[tuple[str, dict, str, str]] = [
        ("signed commit", _commit(ok_msg), "1", "ok"),
        ("missing line", _commit("fix(x): y\n\nBody text."), "1", "FAIL"),
        (
            "another email",
            _commit("fix: y\n\nSigned-off-by: Ada Example <eve@example.org>"),
            "1",
            "FAIL",
        ),
        (
            "another name",
            _commit("fix: y\n\nSigned-off-by: Eve Other <ada@example.org>"),
            "1",
            "FAIL",
        ),
        (
            "email in other case",
            _commit("fix: y\n\nSigned-off-by: ada example <ADA@Example.ORG>"),
            "1",
            "ok",
        ),
        (
            "name with repeated spaces",
            _commit("fix: y\n\nSigned-off-by: Ada   Example <ada@example.org>"),
            "1",
            "ok",
        ),
        ("merge commit", _commit("Merge branch 'main'", parents=2), "1", "skip"),
        (
            "co-author without a line",
            _commit("fix: y\n\nCo-authored-by: Bo Co <bo@example.org>"),
            "1",
            "FAIL",
        ),
        (
            "sign-off plus co-author",
            _commit(ok_msg + "\nCo-authored-by: Bo Co <bo@example.org>"),
            "1",
            "ok",
        ),
        (
            "real Dependabot commit",
            _commit(
                dep_msg,
                name="dependabot[bot]",
                email="49699333+dependabot[bot]@users.noreply.github.com",
                gh_author=bot,
                verified=True,
            ),
            str(DEPENDABOT_ID),
            "ok",
        ),
        (
            "Dependabot email, not signed by GitHub",
            _commit(
                dep_msg,
                name="dependabot[bot]",
                email="49699333+dependabot[bot]@users.noreply.github.com",
                gh_author=bot,
                verified=False,
            ),
            str(DEPENDABOT_ID),
            "FAIL",
        ),
        (
            "Dependabot commit in a pull request opened by a person",
            _commit(
                dep_msg,
                name="dependabot[bot]",
                email="49699333+dependabot[bot]@users.noreply.github.com",
                gh_author=bot,
                verified=True,
            ),
            "12345",
            "FAIL",
        ),
        (
            "Dependabot name, GitHub author null",
            _commit(
                dep_msg,
                name="dependabot[bot]",
                email="49699333+dependabot[bot]@users.noreply.github.com",
                gh_author=None,
                verified=True,
            ),
            str(DEPENDABOT_ID),
            "FAIL",
        ),
        (
            "Dependabot id on a user account",
            _commit(
                dep_msg,
                gh_author={"login": "x", "id": DEPENDABOT_ID, "type": "User"},
                verified=True,
            ),
            str(DEPENDABOT_ID),
            "FAIL",
        ),
        (
            "sign-off in a fenced block, no trailer",
            _commit(
                "fix: y\n\n```\nSigned-off-by: Ada Example <ada@example.org>\n```\n\nMore text."
            ),
            "1",
            "FAIL",
        ),
        (
            "sign-off as the last paragraph, fenced",
            _commit("fix: y\n\n```\nSigned-off-by: Ada Example <ada@example.org>\n```"),
            "1",
            "FAIL",
        ),
        (
            "sign-off in a quoted line",
            _commit("fix: y\n\n> Signed-off-by: Ada Example <ada@example.org>"),
            "1",
            "FAIL",
        ),
        (
            "sign-off in the middle, prose after it",
            _commit(
                "fix: y\n\nSigned-off-by: Ada Example <ada@example.org>\n\nSome closing words."
            ),
            "1",
            "FAIL",
        ),
        (
            "sign-off in a block that is not all trailers",
            _commit("fix: y\n\nSigned-off-by: Ada Example <ada@example.org>\nnot a trailer"),
            "1",
            "FAIL",
        ),
        (
            "subject only",
            _commit("Signed-off-by: Ada Example <ada@example.org>"),
            "1",
            "FAIL",
        ),
        (
            "CRLF message",
            _commit("fix: y\r\n\r\nSigned-off-by: Ada Example <ada@example.org>\r\n"),
            "1",
            "ok",
        ),
        (
            "shell metacharacters, signed",
            _commit(
                f"fix: {meta}\n\n{meta}\n\nSigned-off-by: Ada Example <ada@example.org>",
                extra={"head_ref": meta, "title": meta},
            ),
            "1",
            "ok",
        ),
        (
            "shell metacharacters, unsigned",
            _commit(f"fix: {meta}\n\n{meta}", extra={"head_ref": meta, "title": meta}),
            "1",
            "FAIL",
        ),
        (
            "shell metacharacters in the sign-off name",
            _commit(
                f"fix: y\n\nSigned-off-by: {meta} <ada@example.org>",
            ),
            "1",
            "FAIL",
        ),
        (
            "shell metacharacters in the author, signed",
            _commit(
                "fix: y\n\nSigned-off-by: $(id) `id` <a&&b@example.org>",
                name="$(id) `id`",
                email="a&&b@example.org",
            ),
            "1",
            "ok",
        ),
    ]
    bad = 0
    for label, commit, pr_author, want in cases:
        got, _ = judge(commit, pr_author)
        status = "ok  " if got == want else "FAIL"
        if got != want:
            bad += 1
        print(f"self-test {status} {label}: expected {want}, got {got}")

    # Whole-run cases: exit code and input soundness.
    one = [json.dumps(_commit(ok_msg))]
    run_cases: list[tuple[str, list[str], int, str | None]] = [
        ("one signed commit", one, 0, None),
        ("one failing commit", [json.dumps(_commit("fix: y\n\nBody."))], 1, None),
        (
            "a failing commit among good ones",
            [json.dumps(_commit(ok_msg)), json.dumps(_commit("fix: y\n\nBody.")), ""],
            1,
            None,
        ),
        ("only a merge commit", [json.dumps(_commit("Merge", parents=2))], 0, None),
        ("empty input", [], 1, None),
        ("input that is not JSON", ["not json"], 1, None),
        ("PR_COMMITS equals the commits read", one, 0, "1"),
        ("PR_COMMITS one higher than the commits read", one, 1, "2"),
        ("PR_COMMITS is not a number", one, 1, "abc"),
    ]
    for label, lines, want_code, pr_commits in run_cases:
        saved = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            code = run(lines, "1", None, pr_commits)
        finally:
            sys.stdout.close()
            sys.stdout = saved
        status = "ok  " if code == want_code else "FAIL"
        if code != want_code:
            bad += 1
        print(f"self-test {status} run: {label}: expected exit {want_code}, got {code}")
    if bad:
        print(f"self-test: {bad} case(s) failed")
        return 1
    print("self-test: all cases passed")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--self-test"]:
        return self_test()
    if len(argv) > 1:
        print("usage: check-signoff.py [--self-test]   (commits as JSON lines on stdin)")
        return 2
    return run(
        sys.stdin.read().splitlines(),
        os.environ.get("PR_AUTHOR_ID", ""),
        os.environ.get("GITHUB_STEP_SUMMARY") or None,
        os.environ.get("PR_COMMITS"),
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
