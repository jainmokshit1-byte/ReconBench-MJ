import createClient from "openapi-fetch";

import { notifySessionExpired } from "../auth/session";
import type { paths } from "./generated";

export const apiClient = createClient<paths>({
  baseUrl: import.meta.env["VITE_API_BASE_URL"] ?? window.location.origin,
  credentials: "include",
  fetch: (...args) => globalThis.fetch(...args),
});

apiClient.use({
  onResponse({ response }) {
    if (response.status === 401) notifySessionExpired();
    return response;
  },
});
