# GBrain resident-owner Google sync compatibility patch

The pinned GBrain revision is
`e78f1c38b947b053f3a46881340f74f316be855a` (0.59.0.0).
Its authenticated resident-owner sync handler sends every managed source into
Git working-tree discovery. Google sources need the connector dispatch already
implemented by `performSync`, so a non-Git Google source fails with
`storage_error` while ordinary MCP reads and writes continue to work.

`gbrain-google-owner-sync.patch` changes only that dispatcher. It retains the
current trusted CLI registration, source resolution, archived-source rejection,
submission authority, timeout, disconnect, and no-embedding checks. Google
passes through the same managed connector implementation used by standalone
CLI sync. Other managed sources retain the bounded Git sync path.

Apply it in a separate worktree at the exact pin, preserving the original
checkout. From that worktree:

```sh
git apply /path/to/memento/codex/compat/gbrain-google-owner-sync.patch
cp /path/to/memento/codex/compat/gbrain-google-owner-sync.test.ts \
  test/memento-google-owner-sync.test.ts
bun test test/memento-google-owner-sync.test.ts
bun test test/persistence-sync-stdio-owner.serial.test.ts
```

The compatibility test creates a temporary PGLite database and intercepts
Calendar HTTP with synthetic data. It proves the registered CLI can import a
Calendar page without a Git repository, while unregistered and stdio callers
are rejected before external access. It makes no real Google requests.
The existing upstream owner suite checks source filtering, pending writes,
revocation, cross-home isolation, and rejection of remote administration.
Both suites passed: **11 tests, 78 assertions**.

The local compatibility owner was then tested against the already authorized
real Google source: **6.341 seconds**, **15 new pages**, **zero embeddings**,
**zero failures**. Default memory sync also passed in **5.132 seconds**.
No messages were sent or additional Google scopes requested by this check.

The optional local owner uses the patched worktree's `src/cli.ts` with the same
private GBrain home, owner credential, and OAuth handoff. Only one process may
own that PGLite database. Stop the existing owner cleanly before switching.
No Google OAuth scope changes, database rewrites, or public exposure are needed.

For ordinary memory files, the default source must be a Git working tree.
The local demo uses an empty baseline commit; personal Markdown files were
not committed or pushed. Sync uses `--working-tree --exclude 'skills/**'`,
because GBrain's bundled skills require their separate publication mechanism.
See [the empirical report](../research/gbrain.md) for live observations and
limitations of the pinned release.
