import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, endpoints, getToken, setToken } from "./api";
import { errorMessage } from "./use-api";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("api client", () => {
  const fetchMock = vi.fn();
  const originalLocation = window.location;

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    // make location.href assignable so the 401 redirect can be observed
    Object.defineProperty(window, "location", { configurable: true, value: { pathname: "/analytics", href: "/analytics" } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  });

  it("sends the bearer token, JSON body and drops empty query params", async () => {
    setToken("tok-1");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    await api("/plants/1/reports/generate", { method: "POST", query: { kind: "weekly", anchor: undefined, x: "" }, body: { a: 1 } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/plants\/1\/reports\/generate\?kind=weekly$/);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok-1", "Content-Type": "application/json" });
    expect(init.body).toBe('{"a":1}');
  });

  it("raises ApiError with the FastAPI detail", async () => {
    fetchMock.mockResolvedValue(jsonResponse(403, { detail: "View-only access: ask the plant owner to make changes" }));
    const err = (await api("/plants/1", { method: "PATCH", body: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(errorMessage(err)).toBe("View-only access: ask the plant owner to make changes");
  });

  it("joins pydantic validation errors into one message", () => {
    const err = new ApiError(422, [{ msg: "value is not a valid email address" }, { msg: "field required" }]);
    expect(errorMessage(err)).toBe("value is not a valid email address; field required");
    expect(errorMessage(new Error("boom"))).toBe("boom");
  });

  it("clears the session and redirects to /login on 401", async () => {
    setToken("expired");
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "Token expired" }));
    await expect(api("/plants")).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(window.location.href).toBe("/login");
  });

  it("does not redirect when an invite is accepted with a wrong password", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "An account with this e-mail already exists: enter its current password" }));
    const err = await endpoints.acceptInvitation("abc/def", "wrong").catch((e) => e);
    expect(errorMessage(err)).toMatch(/already exists/);
    expect(window.location.href).toBe("/analytics");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/invitations\/abc%2Fdef\/accept$/);
  });

  it("returns undefined for 204 and a Blob for downloads", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(endpoints.revokeInvite(1, 2)).resolves.toBeUndefined();
    fetchMock.mockResolvedValueOnce(new Response("%PDF-1.4", { status: 200, headers: { "Content-Type": "application/pdf" } }));
    const blob = await api<Blob>("/plants/1/reports/3/pdf", { blob: true });
    expect(blob.type).toBe("application/pdf"); // realm-safe (jsdom vs Node Blob)
    expect(await blob.text()).toBe("%PDF-1.4");
  });
});
