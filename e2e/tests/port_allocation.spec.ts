import { test, expect } from "@playwright/test";
import net from "node:net";
import {
  claimDistinctPair,
  claimFreePort,
  claimPortExcluding,
  isAddrInUseTail,
} from "./fixtures";

test("OS-allocated ports are distinct and bindable at once", async () => {
  const [first, second] = await claimDistinctPair();
  expect(first).not.toBe(second);

  const listen = (port: number) =>
    new Promise<import("node:net").Server>((resolve, reject) => {
      const server = net.createServer();
      server.on("error", reject);
      server.listen(port, "127.0.0.1", () => resolve(server));
    });
  const single = await claimFreePort();
  expect(single).toBeGreaterThan(0);
  const a = await listen(first);
  const b = await listen(second);
  const c = await listen(single).catch(() => null);
  // single may equal first/second from a prior claim; only require bindable
  // when distinct.
  try {
    expect(a.listening).toBe(true);
    expect(b.listening).toBe(true);
    if (single !== first && single !== second) {
      expect(c?.listening).toBe(true);
    }
  } finally {
    await Promise.all([
      new Promise<void>((r) => a.close(() => r())),
      new Promise<void>((r) => b.close(() => r())),
      c ? new Promise<void>((r) => c.close(() => r())) : Promise.resolve(),
    ]);
  }
});

test("EADDRINUSE detector only matches bind collisions", () => {
  expect(
    isAddrInUseTail("ERROR: [Errno 98] error while attempting to bind"),
  ).toBe(false);
  expect(
    isAddrInUseTail(
      "ERROR: [Errno 98] error while attempting to bind on address ('127.0.0.1', 38064): address already in use",
    ),
  ).toBe(true);
  expect(isAddrInUseTail("listen EADDRINUSE: address already in use")).toBe(
    true,
  );
  // A bare phrase from unrelated output (config text, app error) is not a
  // bind collision: the broad detector retried and reported these as port
  // collisions, masking the real startup failure.
  expect(
    isAddrInUseTail(
      "startup failed: address already in use in config file, fix the setting",
    ),
  ).toBe(false);
  // A bare EADDRINUSE token without a bind/listen signature is not a bind
  // collision either: merged log text may mention the code for an unrelated
  // failure, and retrying it as a port collision masks the real cause.
  expect(
    isAddrInUseTail("worker crashed: EADDRINUSE noted in status, check output"),
  ).toBe(false);
});

test("claimPortExcluding skips excluded ports instead of retrying them", async () => {
  const seq = [50001, 50001, 50002];
  let calls = 0;
  const fresh = await claimPortExcluding(
    new Set([50001]),
    async () => seq[calls++],
  );
  expect(fresh).toBe(50002);
  expect(calls).toBe(3);
});

test("restart collision path moves to a distinct bindable port", async () => {
  const [mockPort, appPort] = await claimDistinctPair();
  const blocker = net.createServer();
  await new Promise<void>((resolve, reject) => {
    blocker.on("error", reject);
    blocker.listen(appPort, "127.0.0.1", () => resolve());
  });
  try {
    // Same logic restartApp uses after EADDRINUSE: exclude the collided
    // and mock ports, so a real post-stop claim race resolves instead of
    // failing three times on the same released port.
    const fresh = await claimPortExcluding(new Set([appPort, mockPort]));
    expect(fresh).not.toBe(appPort);
    expect(fresh).not.toBe(mockPort);
    const probe = net.createServer();
    await new Promise<void>((resolve, reject) => {
      probe.on("error", reject);
      probe.listen(fresh, "127.0.0.1", () => resolve());
    });
    await new Promise<void>((r) => probe.close(() => r()));
  } finally {
    await new Promise<void>((r) => blocker.close(() => r()));
  }
});
