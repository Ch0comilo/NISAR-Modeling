"""Stop the Modal preview apps whose pull request is closed, and delete their job Dicts.

A preview is named nisar-hazard-watch-ml-pr-<number>. Only those names are ever touched, so the
production app, the dev app and every other app of the workspace are out of reach.
Environment: GITHUB_REPOSITORY and GH_TOKEN; DRY_RUN=1 only lists what would be removed.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

PREVIEW = re.compile(r"^nisar-hazard-watch-ml-pr-(\d+)$")
MODAL_ENVIRONMENT = "main"
REQUEST_TIMEOUT_SECONDS = 30


def active_previews(apps):
    """Pairs (app name, pull request number) of the previews that are still deployed."""
    found = []
    for app in apps:
        match = PREVIEW.match(app.get("description") or "")
        if match and "stopped" not in str(app.get("state", "")).lower():
            found.append((app["description"], int(match.group(1))))
    return found


def stale_previews(apps, pull_request_states):
    """Previews whose pull request is closed or no longer exists; open ones are kept."""
    return [(name, number) for name, number in active_previews(apps)
            if pull_request_states.get(number, "missing") != "open"]


def modal(*arguments, check=True):
    return subprocess.run(["modal", *arguments, "--env", MODAL_ENVIRONMENT], check=check,
                          capture_output=True, text=True)


def pull_request_state(repository, number, token):
    request = urllib.request.Request(f"https://api.github.com/repos/{repository}/pulls/{number}",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)["state"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return "missing"
        raise


def main():
    repository, token = os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]
    apps = json.loads(modal("app", "list", "--json").stdout)
    states = {number: pull_request_state(repository, number, token)
              for _, number in active_previews(apps)}
    stale = stale_previews(apps, states)
    print(f"{len(states)} preview apps deployed, {len(stale)} to remove")
    dry_run = bool(os.environ.get("DRY_RUN"))
    for name, number in stale:
        print(f"{'would remove' if dry_run else 'removing'} {name} (pull request {number} is {states[number]})")
        if dry_run:
            continue
        modal("app", "stop", name, "--yes")
        modal("dict", "delete", f"{name}-jobs", "--allow-missing", "--yes")


if __name__ == "__main__":
    main()
