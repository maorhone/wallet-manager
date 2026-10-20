import type { ErrorEnvelope } from './types';

export class ApiError extends Error {
  constructor(
    public status: number,
    public envelope: ErrorEnvelope,
  ) {
    super(envelope.message ?? envelope.code);
  }
  get code() {
    return this.envelope.code;
  }
}

export interface WriteOptions {
  reason: string;
  /** One key per confirm; pass the same key when retrying the same confirm. */
  idempotencyKey?: string;
}

const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)wm_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { write?: WriteOptions } = {},
): Promise<T> {
  const { write, ...rest } = init;
  const method = (rest.method ?? 'GET').toUpperCase();
  const headers = new Headers(rest.headers);
  if (rest.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (WRITE_METHODS.has(method)) {
    headers.set('X-CSRF-Token', csrfToken());
    headers.set('Idempotency-Key', write?.idempotencyKey ?? crypto.randomUUID());
    if (write) headers.set('X-Change-Reason', encodeURIComponent(write.reason));
  }
  const res = await fetch(path, { ...rest, method, headers, credentials: 'include' });
  if (res.status === 401 && !location.pathname.startsWith('/login')) {
    location.assign('/login');
  }
  if (!res.ok) {
    const envelope = (await res
      .json()
      .catch(() => ({ code: 'HTTP_' + res.status }))) as ErrorEnvelope;
    throw new ApiError(res.status, envelope);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const apiPut = <T>(path: string, body: unknown, write: WriteOptions) =>
  apiFetch<T>(path, { method: 'PUT', body: JSON.stringify(body), write });

export const apiPatch = <T>(path: string, body: unknown, write: WriteOptions) =>
  apiFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body), write });

export const apiPost = <T>(path: string, body: unknown, write?: WriteOptions) =>
  apiFetch<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    write,
  });
