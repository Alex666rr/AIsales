import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./app";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the administration shell after the server confirms the browser session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            actor_id: "70000000-0000-0000-0000-000000000001",
            organization_id: "60000000-0000-0000-0000-000000000001",
            roles: ["company_owner"],
          }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Администрирование" })).toBeInTheDocument();
    expect(screen.getByText("company_owner")).toBeInTheDocument();
    expect(screen.getByTestId("application-shell")).toHaveAttribute("data-theme", "control-room");
    expect(screen.getByRole("heading", { name: "Telegram-аккаунты" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Доступ команды" })).toBeInTheDocument();
  });

  it("opens the accounts workspace from the main navigation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            actor_id: "70000000-0000-0000-0000-000000000001",
            organization_id: "60000000-0000-0000-0000-000000000001",
            roles: ["company_owner"],
          }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });

    fireEvent.click(screen.getByRole("button", { name: "Аккаунты" }));

    expect(screen.getByRole("heading", { name: "Telegram-аккаунты" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подключить по номеру" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подключить по QR" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Доступ команды" })).not.toBeInTheDocument();
  });

  it("shows connected Telegram account states from the current organization only", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        actor_id: "70000000-0000-0000-0000-000000000001",
        organization_id: "60000000-0000-0000-0000-000000000001",
        roles: ["manager"],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{
        account_id: "90000000-0000-0000-0000-000000000001",
        state: "quarantine",
        last_seen_at: "2026-08-22T10:00:00Z",
        error_code: null,
      }]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });
    fireEvent.click(screen.getByRole("button", { name: "Аккаунты" }));

    expect(await screen.findByText("Карантин")).toBeInTheDocument();
    expect(screen.getByText("Аккаунт #90000000")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/workspace/telegram/accounts",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("starts a phone connection using only the browser session", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        actor_id: "70000000-0000-0000-0000-000000000001",
        organization_id: "60000000-0000-0000-0000-000000000001",
        roles: ["company_owner"],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attempt_id: "90000000-0000-0000-0000-000000000001",
        method: "phone",
        status: "code_requested",
        expires_at: "2026-08-22T10:00:00Z",
      }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });
    fireEvent.click(screen.getByRole("button", { name: "Аккаунты" }));
    fireEvent.change(screen.getByLabelText("Номер Telegram"), { target: { value: "+12025550123" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить по номеру" }));

    expect(await screen.findByLabelText("Код из Telegram")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/workspace/telegram/connections/phone/start",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("switches from Telegram code to the 2FA password without retaining the code", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        actor_id: "70000000-0000-0000-0000-000000000001",
        organization_id: "60000000-0000-0000-0000-000000000001",
        roles: ["company_owner"],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attempt_id: "90000000-0000-0000-0000-000000000001", method: "phone", status: "code_requested", expires_at: "2026-08-22T10:00:00Z",
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attempt_id: "90000000-0000-0000-0000-000000000001", method: "phone", status: "password_required", expires_at: "2026-08-22T10:00:00Z",
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });
    fireEvent.click(screen.getByRole("button", { name: "Аккаунты" }));
    fireEvent.change(screen.getByLabelText("Номер Telegram"), { target: { value: "+12025550123" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить по номеру" }));
    fireEvent.change(await screen.findByLabelText("Код из Telegram"), { target: { value: "12345" } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить код" }));

    expect(await screen.findByLabelText("Пароль Telegram")).toBeInTheDocument();
    expect(screen.queryByLabelText("Код из Telegram")).not.toBeInTheDocument();
  });

  it("requests a QR connection only from the workspace route", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        actor_id: "70000000-0000-0000-0000-000000000001",
        organization_id: "60000000-0000-0000-0000-000000000001",
        roles: ["company_owner"],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attempt_id: "90000000-0000-0000-0000-000000000002", method: "qr", status: "pending", expires_at: "2026-08-22T10:00:00Z", qr_url: "tg://login?token=test-only",
      }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });
    fireEvent.click(screen.getByRole("button", { name: "Аккаунты" }));
    fireEvent.click(screen.getByRole("button", { name: "Подключить по QR" }));

    expect(await screen.findByRole("button", { name: "Проверить вход" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/workspace/telegram/connections/qr/start",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows the premium login form when there is no active server session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "AIsales" })).toBeInTheDocument();
    expect(screen.getByText("Рабочее пространство продаж")).toBeInTheDocument();
    expect(screen.getByTestId("login-monogram")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByLabelText("Электронная почта")).toBeInTheDocument();
    expect(screen.getByLabelText("Google Authenticator")).toBeInTheDocument();
    expect(screen.getByText("Нет доступа к приложению?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Использовать код восстановления" })).toBeInTheDocument();
    expect(screen.getAllByTestId("login-field-icon")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Показать пароль" })).toBeInTheDocument();
  });

  it("shows and hides the password from the visibility control", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    render(<App />);

    const password = await screen.findByLabelText("Пароль");
    expect(password).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Показать пароль" }));

    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Скрыть пароль" })).toBeInTheDocument();
  });

  it("accepts only six digits for the Google Authenticator code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    render(<App />);

    const verification = await screen.findByLabelText("Google Authenticator");
    fireEvent.change(verification, { target: { value: "12a3456789" } });

    expect(verification).toHaveValue("123456");
    expect(verification).toHaveAttribute("maxLength", "6");
  });

  it("switches the second-factor field to a recovery code without a checkbox", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    render(<App />);

    const recoveryAction = await screen.findByRole("button", { name: "Использовать код восстановления" });
    fireEvent.click(recoveryAction);

    expect(screen.getByLabelText("Код восстановления")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вернуться к Google Authenticator" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("does not expose staff invitations to a non-owner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            actor_id: "70000000-0000-0000-0000-000000000002",
            organization_id: "60000000-0000-0000-0000-000000000001",
            roles: ["manager"],
          }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Telegram-аккаунты" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Создать приглашение" })).not.toBeInTheDocument();
  });

  it("lets a company owner issue a one-time staff setup link", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        actor_id: "70000000-0000-0000-0000-000000000001",
        organization_id: "60000000-0000-0000-0000-000000000001",
        roles: ["company_owner"],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user_id: "80000000-0000-0000-0000-000000000001",
        email: "manager@example.test",
        role: "manager",
        setup_token: "one-time-setup-token",
      }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("heading", { name: "Администрирование" });
    fireEvent.change(screen.getByLabelText("Email сотрудника"), { target: { value: "manager@example.test" } });
    fireEvent.submit(screen.getByRole("button", { name: "Создать приглашение" }).closest("form")!);

    expect(await screen.findByText("Одноразовая ссылка для сотрудника")).toBeInTheDocument();
    expect(screen.getByDisplayValue(/one-time-setup-token/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/organizations/members/invitations",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
