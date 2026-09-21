"""Publish the reviewed current main after the private key repair completed.

Only two fixed GitHub API requests. No credential retrieval or browser access.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

REPOSITORY = "skharkov1246/kvant-sourcing-dashboard"
API = "https://api.github.com/repos/" + REPOSITORY


class PublishError(Exception):
    """Constant error identifiers only."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dispatch(env, opener=None):
    token = env.get("GH_TOKEN", "")
    sha = env.get("GITHUB_SHA", "")
    if (env.get("GITHUB_REPOSITORY") != REPOSITORY or
            env.get("GITHUB_REF") != "refs/heads/main" or
            env.get("ARCHIVE_KEY_REPAIRED") != "true" or
            not re.fullmatch(r"[a-f0-9]{40}", sha) or not token):
        raise PublishError("PUBLICATION_CONTEXT_REJECTED")
    opener = opener or urllib.request.build_opener(NoRedirect())
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(API + "/branches/main", headers=headers)
        with opener.open(req, timeout=30) as response:
            if response.status != 200:
                raise PublishError("MAIN_VERIFICATION_FAILED")
            raw = response.read(131073)
        if len(raw) > 131072:
            raise PublishError("MAIN_RESPONSE_LIMIT")
        body = json.loads(raw)
        if body.get("commit", {}).get("sha") != sha:
            raise PublishError("MAIN_CHANGED_SINCE_REVIEW")
        req = urllib.request.Request(
            API + "/actions/workflows/deploy.yml/dispatches",
            headers=headers, method="POST", data=b'{"ref":"main"}',
        )
        with opener.open(req, timeout=30) as response:
            if response.status != 204:
                raise PublishError("PUBLICATION_NOT_ACCEPTED")
    except urllib.error.HTTPError as error:
        error.close()
        raise PublishError("GITHUB_REQUEST_FAILED") from None
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        raise PublishError("PUBLICATION_CHECK_OR_DISPATCH_FAILED") from None
    return {"ok": True, "publication_requested": True, "ref": "main", "sha": sha,
            "portal_end_to_end_verified": False}


def main(env=None):
    try:
        result = dispatch(os.environ if env is None else env)
    except PublishError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": "PUBLICATION_FAILED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
