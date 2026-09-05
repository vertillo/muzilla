import { test, expect } from "@playwright/test";
import net from "node:net";
import {
  claimDistinctPair,
  claimFreePort,
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
});
