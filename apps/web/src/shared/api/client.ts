export type SessionContext = {
  actor_id: string;
  organization_id: string;
  roles: string[];
};

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super("The server did not accept the request.");
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, {
    credentials: "include",
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
}

export async function getCurrentSession(): Promise<SessionContext | null> {
  const response = await request("/auth/session");
  if (response.status === 401) return null;
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<SessionContext>;
}

export async function login(input: {
  email: string;
  password: string;
  totp_code?: string;
  recovery_code?: string;
}): Promise<void> {
  const response = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new ApiError(response.status);
}
