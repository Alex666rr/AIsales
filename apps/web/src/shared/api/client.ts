export type SessionContext = {
  actor_id: string;
  organization_id: string;
  roles: string[];
};

export type StaffInvitation = {
  user_id: string;
  email: string;
  role: "administrator" | "manager";
  setup_token: string;
};

export type TelegramConnectionAttempt = {
  attempt_id: string;
  method: "phone" | "qr";
  status: "pending" | "code_requested" | "password_required" | "authorized" | "expired" | "failed";
  expires_at: string;
  account_id?: string | null;
};

export type TelegramQrStart = TelegramConnectionAttempt & {
  qr_url: string;
};

export type TelegramAccountStatus = {
  account_id: string;
  state: string;
  last_seen_at: string | null;
  error_code: string | null;
};

export type OrganizationProfile = { organization_id: string; name: string };
export type WorkspaceMember = { user_id: string; email: string; role: "administrator" | "manager"; is_active: boolean };

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

export async function createStaffInvitation(input: {
  email: string;
  role: "administrator" | "manager";
}): Promise<StaffInvitation> {
  const response = await request("/organizations/members/invitations", {
    method: "POST",
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<StaffInvitation>;
}

export async function getOrganizationProfile(): Promise<OrganizationProfile> {
  const response = await request("/workspace/organization");
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<OrganizationProfile>;
}

export async function renameOrganization(name: string): Promise<OrganizationProfile> {
  const response = await request("/workspace/organization", { method: "PATCH", body: JSON.stringify({ name }) });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<OrganizationProfile>;
}

export async function listWorkspaceMembers(): Promise<WorkspaceMember[]> {
  const response = await request("/workspace/members");
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<WorkspaceMember[]>;
}

export async function deactivateWorkspaceMember(userId: string): Promise<WorkspaceMember> {
  const response = await request(`/workspace/members/${userId}/deactivate`, { method: "POST" });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<WorkspaceMember>;
}

export async function activateSetup(setupToken: string, password: string): Promise<{
  enrollment_token: string;
  totp_uri: string;
}> {
  const response = await request("/auth/setup", {
    method: "POST",
    body: JSON.stringify({ setup_token: setupToken, password }),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<{ enrollment_token: string; totp_uri: string }>;
}

export async function confirmTotp(enrollmentToken: string, code: string): Promise<string[]> {
  const response = await request("/auth/totp/confirm", {
    method: "POST",
    body: JSON.stringify({ enrollment_token: enrollmentToken, code }),
  });
  if (!response.ok) throw new ApiError(response.status);
  const body = await response.json() as { recovery_codes: string[] };
  return body.recovery_codes;
}

export async function startTelegramPhone(phone: string): Promise<TelegramConnectionAttempt> {
  const response = await request("/workspace/telegram/connections/phone/start", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramConnectionAttempt>;
}

export async function confirmTelegramPhoneCode(
  attemptId: string,
  code: string,
): Promise<TelegramConnectionAttempt> {
  const response = await request(`/workspace/telegram/connections/${attemptId}/phone/confirm`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramConnectionAttempt>;
}

export async function confirmTelegramPassword(
  attemptId: string,
  password: string,
): Promise<TelegramConnectionAttempt> {
  const response = await request(`/workspace/telegram/connections/${attemptId}/phone/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramConnectionAttempt>;
}

export async function startTelegramQr(): Promise<TelegramQrStart> {
  const response = await request("/workspace/telegram/connections/qr/start", { method: "POST" });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramQrStart>;
}

export async function getTelegramQrStatus(attemptId: string): Promise<TelegramConnectionAttempt> {
  const response = await request(`/workspace/telegram/connections/${attemptId}/qr/status`);
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramConnectionAttempt>;
}

export async function listTelegramAccounts(): Promise<TelegramAccountStatus[]> {
  const response = await request("/workspace/telegram/accounts");
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramAccountStatus[]>;
}

export type TelegramAccountAction = "pause" | "resume" | "archive";

export async function transitionTelegramAccount(
  accountId: string,
  action: TelegramAccountAction,
): Promise<TelegramAccountStatus> {
  const response = await request(`/workspace/telegram/accounts/${accountId}/${action}`, { method: "POST" });
  if (!response.ok) throw new ApiError(response.status);
  return response.json() as Promise<TelegramAccountStatus>;
}
