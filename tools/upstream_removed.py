#!/usr/bin/env python3
"""
Keep the files this fork deleted from Plex Uno deleted when Plex Uno is merged in.

upstream-removed.txt, at the top of the repository, lists every file or directory (ending in "/") the fork removed
from Plex Uno, one per line, with the reason after a "#". Lines starting with "#" and blank lines are skipped:

    docs/settings-audit.md  # Plex Uno's settings audit, not this add-on's

Entries are plain paths relative to the top of the repository, read literally: no patterns, no "." or "..".

Run from anywhere inside the repository:

    python3 tools/upstream_removed.py resolve
        After "git merge upstream/<branch>" stops on files the fork deleted: removes every listed path the merge
        brought back - a file upstream changed, or a new file under a removed directory - so "git commit" can
        finish the merge. Other conflicts are left alone.

    python3 tools/upstream_removed.py check
        Exits 1 when a listed path is tracked again or an entry is malformed, 0 otherwise.
"""

import os
import subprocess
import sys

LIST_NAME = 'upstream-removed.txt'


class ListError(Exception):
    pass


def git(root, *args, **kwargs):
    return subprocess.run(
        ['git', '--literal-pathspecs'] + list(args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        **kwargs,
    ).stdout


def repoRoot():
    return git(os.getcwd(), 'rev-parse', '--show-toplevel').strip()


def parseEntries(text):
    """The listed paths, in order; a ListError naming each bad line."""
    entries = []
    errors = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue

        path, hasReason, reason = line.partition('#')
        path = path.strip()
        if not hasReason or not reason.strip():
            errors.append(f'line {number}: no reason after "#": {raw}')

        parts = path[:-1].split('/') if path.endswith('/') else path.split('/')
        if path.startswith('/') or '\\' in path or any(part in ('', '.', '..') for part in parts):
            errors.append(f'line {number}: not a plain path inside the repository: {path}')
            continue

        entries.append(path)

    if errors:
        raise ListError('\n'.join(errors))
    return entries


def readEntries(root):
    path = os.path.join(root, LIST_NAME)
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return parseEntries(f.read())


def listed(entry, path):
    if entry.endswith('/'):
        return path.startswith(entry)
    return path == entry


def trackedListedPaths(root, entries):
    """Tracked paths (unmerged ones included) that an entry covers, sorted."""
    tracked = {p for p in git(root, 'ls-files', '-z').split('\0') if p}
    return sorted(p for p in tracked if any(listed(entry, p) for entry in entries))


def resolve(root, entries):
    paths = trackedListedPaths(root, entries)
    if not paths:
        print(f'Nothing listed in {LIST_NAME} is tracked.')
        return 0

    git(
        root,
        'rm',
        '-r',
        '-f',
        '-q',
        '--pathspec-from-file=-',
        '--pathspec-file-nul',
        input='\0'.join(paths),
    )
    print(f'Removed again, as {LIST_NAME} lists them:')
    for path in paths:
        print('  ' + path)
    return 0


def check(root, entries):
    paths = trackedListedPaths(root, entries)
    if not paths:
        return 0

    print(f'Tracked, but {LIST_NAME} lists them as removed (run "python3 tools/upstream_removed.py resolve"):')
    for path in paths:
        print('  ' + path)
    return 1


COMMANDS = {'resolve': resolve, 'check': check}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(
            f'usage: {os.path.basename(argv[0])} resolve|check',
            file=sys.stderr,
        )
        return 2

    try:
        root = repoRoot()
        entries = readEntries(root)
    except ListError as e:
        print(f'{LIST_NAME}:\n{e}')
        return 1
    except subprocess.CalledProcessError as e:
        print(e.stderr.strip() or str(e), file=sys.stderr)
        return 2

    try:
        return COMMANDS[argv[1]](root, entries)
    except subprocess.CalledProcessError as e:
        print(e.stderr.strip() or str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
