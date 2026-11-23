"""
tools/upstream_removed.py - keeping files this fork deleted from Plex Uno deleted when Plex Uno is merged in.

Each test builds a throwaway "upstream" repository and a fork cloned from it, then runs the tool as a command,
the way a maintainer would.
"""

import os
import subprocess
import sys
import tempfile
import unittest

TOOL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'tools',
    'upstream_removed.py',
)

GIT_ENV = dict(
    os.environ,
    GIT_CONFIG_NOSYSTEM='1',
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_AUTHOR_NAME='Test',
    GIT_AUTHOR_EMAIL='test@example.invalid',
    GIT_COMMITTER_NAME='Test',
    GIT_COMMITTER_EMAIL='test@example.invalid',
)


def git(cwd, *args, check=False):
    return subprocess.run(['git'] + list(args), cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=check)


def write(root, path, text):
    full = os.path.join(root, path)
    parent = os.path.dirname(full)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(full, 'w') as f:
        f.write(text)


def tool(cwd, command):
    return subprocess.run(
        [sys.executable, TOOL, command], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=False
    )


def tracked(cwd):
    return set(git(cwd, 'ls-files').stdout.split())


class UpstreamRemovedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.upstream = os.path.join(self._tmp.name, 'upstream')
        self.fork = os.path.join(self._tmp.name, 'fork')
        os.makedirs(self.upstream)
        git(self.upstream, 'init', '-q', '-b', 'main', check=True)
        for path in (
            'keep.txt',
            'notes.md',
            'docs/a.md',
            'docs-extra.txt',
            'readme.md',
        ):
            write(self.upstream, path, path + ' v1\n')
        git(self.upstream, 'add', '-A', check=True)
        git(self.upstream, 'commit', '-q', '-m', 'upstream v1', check=True)
        git(self._tmp.name, 'clone', '-q', self.upstream, self.fork, check=True)

    def forkRemoves(self, listing, *paths):
        git(self.fork, 'rm', '-r', '-q', *paths, check=True)
        write(self.fork, 'upstream-removed.txt', listing)
        git(self.fork, 'add', '-A', check=True)
        git(self.fork, 'commit', '-q', '-m', 'fork removes files', check=True)

    def test_resolve_finishes_a_merge_that_brings_removed_files_back(self):
        self.forkRemoves(
            '# removed from upstream\ndocs/  # its design notes\nnotes.md  # its notes\n',
            'docs',
            'notes.md',
        )
        write(self.upstream, 'docs/a.md', 'docs/a.md v2\n')
        write(self.upstream, 'docs/b.md', 'new upstream doc\n')
        write(self.upstream, 'notes.md', 'notes.md v2\n')
        write(self.upstream, 'keep.txt', 'keep.txt v2\n')
        write(self.upstream, 'docs-extra.txt', 'docs-extra.txt v2\n')
        git(self.upstream, 'commit', '-q', '-am', 'upstream v2', check=True)
        git(self.upstream, 'add', '-A', check=True)
        git(self.upstream, 'commit', '-q', '-m', 'upstream adds a doc', check=True)
        git(self.fork, 'fetch', '-q', 'origin', check=True)
        merge = git(self.fork, 'merge', '--no-edit', 'origin/main')
        self.assertNotEqual(merge.returncode, 0, 'the merge should stop on the deleted files')

        result = tool(self.fork, 'resolve')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(git(self.fork, 'diff', '--name-only', '--diff-filter=U').stdout, '')
        files = tracked(self.fork)
        self.assertNotIn('docs/a.md', files)
        self.assertNotIn('docs/b.md', files)
        self.assertNotIn('notes.md', files)
        self.assertIn(
            'docs-extra.txt',
            files,
            'docs/ must not match a sibling that only shares the prefix',
        )
        self.assertFalse(os.path.exists(os.path.join(self.fork, 'docs', 'b.md')))
        git(self.fork, 'commit', '-q', '--no-edit', check=True)
        with open(os.path.join(self.fork, 'keep.txt')) as f:
            self.assertEqual(f.read(), 'keep.txt v2\n')
        self.assertEqual(tool(self.fork, 'check').returncode, 0)

    def test_check_passes_when_every_listed_path_is_gone(self):
        self.forkRemoves('notes.md  # its notes\n', 'notes.md')

        result = tool(self.fork, 'check')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_fails_when_a_listed_path_is_tracked(self):
        self.forkRemoves('notes.md  # its notes\ndocs/  # its design notes\n', 'notes.md')

        result = tool(self.fork, 'check')

        self.assertEqual(result.returncode, 1)
        self.assertIn('docs/a.md', result.stdout + result.stderr)

    def test_check_fails_on_an_entry_without_a_reason(self):
        self.forkRemoves('notes.md\n', 'notes.md')

        result = tool(self.fork, 'check')

        self.assertEqual(result.returncode, 1)
        self.assertIn('line 1', result.stdout + result.stderr)

    def test_check_fails_on_paths_outside_the_repository(self):
        for entry in (
            '../outside.txt',
            '/etc/hostname',
            'docs/../keep.txt',
            './keep.txt',
            'docs//a.md',
        ):
            write(self.fork, 'upstream-removed.txt', entry + '  # not allowed\n')

            result = tool(self.fork, 'check')

            self.assertEqual(result.returncode, 1, entry)
            self.assertIn('line 1', result.stdout + result.stderr, entry)

    def test_resolve_reads_entries_literally_not_as_patterns(self):
        self.forkRemoves('*.md  # a literal name, not a pattern\n', 'notes.md')

        result = tool(self.fork, 'resolve')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('readme.md', tracked(self.fork))
        self.assertIn('docs/a.md', tracked(self.fork))

    def test_check_passes_without_a_list(self):
        self.assertEqual(tool(self.fork, 'check').returncode, 0)


if __name__ == '__main__':
    unittest.main()
