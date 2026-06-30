/**
 * Authenticated fetch inside Feige page context (browser sends cookies).
 */
async (payload) => {
  const { url, method = "GET", body = null, headers = {} } = payload || {};
  if (!url) return { ok: false, status: 0, error: "no_url" };
  try {
    const resp = await fetch(url, {
      method,
      credentials: "include",
      headers: {
        "content-type": "application/json;charset=UTF-8",
        ...headers,
      },
      body: body != null ? JSON.stringify(body) : undefined,
    });
    const text = await resp.text();
    return {
      ok: resp.ok,
      status: resp.status,
      contentType: resp.headers.get("content-type") || "",
      text: text.slice(0, 800000),
    };
  } catch (error) {
    return { ok: false, status: 0, error: String(error) };
  }
}
