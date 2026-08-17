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
  });

  it("shows the login form when there is no active server session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Вход в AIsales" })).toBeInTheDocument();
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
