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
    expect(screen.getByTestId("password-visibility-icon")).toHaveAttribute("aria-hidden", "true");
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
