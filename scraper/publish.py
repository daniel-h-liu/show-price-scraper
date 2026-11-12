"""The public, read-only copy of the tracker page, for GitHub Pages.

    python -m scraper.publish            # build .site/ and push it to the repo's gh-pages branch
    python -m scraper.publish --no-push  # just build .site/ (open .site/index.html via a local server to preview)

The site is the same index.html in read-only mode, reading pre-built JSON instead of the local API. No
controls, no poster images (they're TodayTix's), no recipients. Visitors only ever load these static
files, so traffic to the site never reaches the ticket sites. Each publish force-pushes one fresh
commit to gh-pages, so the branch doesn't grow a commit per day. Runs after the daily digest when
PUBLIC_URL is set in .env."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from scraper import analysis, db, emailer
from scraper.fetch import ROOT
from scraper.models import now_local

OUT = ROOT / ".site"
STATIC = Path(__file__).resolve().parent / "static" / "index.html"
BRANCH = "gh-pages"


def build(con, out: Path = OUT, signup_url: str | None = None) -> int:
    """Write the read-only site into `out`. Returns the number of shows published."""
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    shows = db.shows(con)
    summaries = []
    for sh in shows:
        s = analysis.summarize(con, sh).to_dict()
        s["image_url"] = None
        summaries.append(s)
        d = analysis.detail(con, sh)
        d["image_url"] = None
        (data / f"show-{sh['id']}.json").write_text(json.dumps(d))
    (data / "state.json").write_text(json.dumps(dict(shows=summaries, running=[], ended=[], generated_at=now_local())))
    try:
        repo_url = _https_remote().removesuffix(".git")
    except RuntimeError:
        repo_url = None
    config = json.dumps({"signup_url": signup_url or None, "repo_url": repo_url}).replace("<", "\\u003c")
    page = STATIC.read_text().replace('<html lang="en">', '<html lang="en" data-mode="static">', 1)
    page = page.replace("<script>\nconst $", f"<script>window.STATIC = {config};</script>\n<script>\nconst $", 1)
    if "window.STATIC" not in page:
        raise RuntimeError("couldn't inject read-only config into index.html")
    (out / "index.html").write_text(page)
    (out / ".nojekyll").write_text("")
    return len(summaries)


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def _https_remote() -> str:
    url = _git(ROOT, "remote", "get-url", "origin")
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise RuntimeError(f"origin isn't a GitHub repo: {url}")
    return f"https://github.com/{m.group(1)}.git"


def push(out: Path = OUT) -> None:
    """Commit `out` as a single fresh commit and force-push it to gh-pages. Uses the gh CLI's stored
    login over HTTPS, which works from launchd (no ssh-agent needed)."""
    gh = shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if Path(p).exists()), None)
    if not gh:
        raise RuntimeError("GitHub CLI (gh) not found")
    shutil.rmtree(out / ".git", ignore_errors=True)
    _git(out, "init", "-q", "-b", BRANCH)
    _git(out, "add", "-A")
    # commit as the GitHub account's private no-reply address, never the personal email in git config
    who = subprocess.run([gh, "api", "user", "--jq", '"\\(.id)+\\(.login)@users.noreply.github.com"'],
                         capture_output=True, text=True).stdout.strip() or "show-price-scraper@users.noreply.github.com"
    _git(out, "-c", "user.name=show-price-scraper", "-c", f"user.email={who}",
         "commit", "-q", "-m", f"Prices as of {now_local()}")
    _git(out, "-c", "credential.helper=", "-c", f"credential.helper=!{gh} auth git-credential",
         "push", "-q", "--force", _https_remote(), f"{BRANCH}:{BRANCH}")


def publish(con, do_push: bool = True) -> str:
    n = build(con, signup_url=os.environ.get("SIGNUP_URL"))
    if do_push:
        push()
        return f"published {n} show(s) to {os.environ.get('PUBLIC_URL') or BRANCH}"
    return f"built {n} show(s) in {OUT}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    emailer.load_dotenv(ROOT / ".env")
    with db.connect() as con:
        print(publish(con, not args.no_push))
    return 0


if __name__ == "__main__":
    sys.exit(main())
