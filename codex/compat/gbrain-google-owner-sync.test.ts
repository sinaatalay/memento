// Copy into the pinned GBrain worktree's test/ directory and run with Bun.
// Uses only a temporary PGLite database and synthetic, intercepted HTTP.
import { afterAll, beforeAll, expect, spyOn, test } from 'bun:test';
import { runAuthenticatedSyncSlice } from '../src/core/persistence/sync-administration.ts';
import { registerLocalWriter, withVerifiedLocalRegistration } from '../src/core/persistence/identity.ts';
import { createConnectorFixture, json } from './helpers/connector-fixture.ts';
import { withEnv } from './helpers/with-env.ts';

const { home, engines, env, boundSource, setup, teardown } = createConnectorFixture();
beforeAll(setup, 120_000);
afterAll(teardown);

test('registered owner sync dispatches Google without Git and retains CLI trust checks', async () => withEnv(env, async () => {
  for (const engine of engines) {
    const config = { kind: 'google', g_account: 'owner@example.invalid',
      g_services: 'calendar', g_access: 'env', g_token_env: 'CONNECTOR_TEST_TOKEN' };
    const fixture = await boundSource(engine, config);
    const params = { options: { sourceId: fixture.id, noPull: true,
      noEmbed: true, noExtract: true }, cwd: home, timeoutSeconds: 30 };
    let calls = 0;
    const intercepted = spyOn(globalThis, 'fetch').mockImplementation((async (input: string | URL | Request) => {
      calls++;
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.pathname.includes('/calendar/')) return json({ items: [{
        id: 'synthetic-owner-dispatch', summary: 'Synthetic calendar observation',
        status: 'confirmed', start: { dateTime: '2027-01-15T12:00:00Z' },
        end: { dateTime: '2027-01-15T13:00:00Z' },
      }], nextSyncToken: 'synthetic-next-token' });
      throw new Error('Unexpected synthetic connector route');
    }) as typeof fetch);
    try {
      await expect(runAuthenticatedSyncSlice(engine, params))
        .rejects.toMatchObject({ code: 'permission_denied' });
      const stdio = await registerLocalWriter(engine, 'stdio');
      await expect(withVerifiedLocalRegistration(engine, stdio,
        () => runAuthenticatedSyncSlice(engine, params)))
        .rejects.toMatchObject({ code: 'permission_denied' });
      expect(calls).toBe(0);
      const cli = await registerLocalWriter(engine, 'cli');
      const result = await withVerifiedLocalRegistration(engine, cli,
        () => runAuthenticatedSyncSlice(engine, params));
      expect(result.status).not.toBe('partial');
      expect(result.added).toBe(1);
      expect(calls).toBeGreaterThan(0);
      const pages = await engine.executeRaw(
        'SELECT id FROM pages WHERE source_id=$1 AND deleted_at IS NULL', [fixture.id]);
      expect(pages).toHaveLength(1);
    } finally { intercepted.mockRestore(); }
  }
}), 120_000);
