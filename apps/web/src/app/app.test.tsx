import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./app";

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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
});
