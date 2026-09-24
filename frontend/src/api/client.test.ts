import { describe, expect, it } from "vitest";
import { errorDetail, readErrorMessage } from "./client";

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
