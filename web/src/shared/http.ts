export interface ApiEnvelope<T> {
  success: boolean
  data: T
  error: string | null
}

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'HttpError'
  }
}

export async function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { cache: 'no-store' })
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
}

export async function delJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: 'DELETE', cache: 'no-store' })
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const statusSuffix = response.status ? ` (${response.status})` : ''
  const text = await response.text()

  if (!text.trim()) {
    throw new HttpError(`Request failed: ${path} returned an empty response${statusSuffix}`, response.status)
  }

  let envelope: ApiEnvelope<T>
  try {
    envelope = JSON.parse(text) as ApiEnvelope<T>
  } catch {
    throw new HttpError(`Request failed: ${path} returned invalid JSON${statusSuffix}`, response.status)
  }

  if (!response.ok || !envelope.success) {
    throw new HttpError(envelope.error ?? `Request failed: ${path}${statusSuffix}`, response.status)
  }
  return envelope.data
}
