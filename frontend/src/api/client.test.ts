import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, errorDetail, isNotFound, readErrorMessage, recordExists } from "./client";

describe("errorDetail", () => {
  it("returns a plain string detail", () => {
    expect(errorDetail({ detail: "Network not found" })).toBe("Network not found");
  });

  it("flattens FastAPI validation issues instead of printing [object Object]", () => {
    const body = {
      detail: [
        { loc: ["body", "initial_failures"], msg: "List should have at least 1 item", type: "too_short" },
        { loc: ["body", "modifications", 0, "node_id"], msg: "Input should be a valid UUID" },
      ],
    };
    expect(errorDetail(body)).toBe(
      "initial_failures: List should have at least 1 item; modifications.0.node_id: Input should be a valid UUID"
    );
  });

  it("returns null when there is nothing readable", () => {
    expect(errorDetail(null)).toBeNull();
    expect(errorDetail("oops")).toBeNull();
    expect(errorDetail({ detail: "   " })).toBeNull();
    expect(errorDetail({ detail: [{ nope: true }] })).toBeNull();
    expect(errorDetail({ detail: { status: "degraded" } })).toBeNull();
  });
});

describe("readErrorMessage", () => {
  it("prefers the server's reason", async () => {
    const res = new Response(JSON.stringify({ detail: "Rate limit exceeded" }), { status: 429 });
    expect(await readErrorMessage(res, "Failed")).toBe("Rate limit exceeded");
  });

  it("survives a non-JSON body from a proxy", async () => {
    const res = new Response("<html>Bad Gateway</html>", { status: 502 });
    expect(await readErrorMessage(res, "Failed to start simulation")).toBe(
      "Failed to start simulation (HTTP 502)"
    );
  });

  it("survives an empty body", async () => {
    const res = new Response(null, { status: 503 });
    expect(await readErrorMessage(res, "Failed")).toBe("Failed (HTTP 503)");
  });
});

afterEach(() => vi.unstubAllGlobals());

const respond = (status: number, body: unknown = {}) =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));

describe("API errors", () => {
  it("carry the HTTP status, so a 404 can be told apart", async () => {
    respond(404, { detail: "Simulation not found" });
    const err = await apiFetch("/api/simulations/x", "Failed").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("Simulation not found");
    expect(isNotFound(err)).toBe(true);
  });

  it("does not treat other failures as not found", async () => {
    respond(500);
    const err = await apiFetch("/api/simulations/x", "Failed").catch((e: unknown) => e);
    expect(isNotFound(err)).toBe(false);
    expect(isNotFound(new Error("network down"))).toBe(false);
  });
});

describe("recordExists", () => {
  it("is false only on a 404", async () => {
    respond(404);
    expect(await recordExists("/api/scenarios/x")).toBe(false);
    respond(200);
    expect(await recordExists("/api/scenarios/x")).toBe(true);
    respond(503);
    expect(await recordExists("/api/scenarios/x")).toBe(true);
  });

  it("assumes the record exists when the request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    expect(await recordExists("/api/scenarios/x")).toBe(true);
  });
});
