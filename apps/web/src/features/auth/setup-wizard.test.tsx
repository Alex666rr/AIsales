import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupWizard } from "./setup-wizard";

describe("SetupWizard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses the one-time link token to begin password and TOTP setup", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      enrollment_token: "enrollment-token",
      totp_uri: "otpauth://totp/AIsales:manager@example.test?secret=ABCDEF",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SetupWizard setupToken="one-time-setup-token" />);
    fireEvent.change(screen.getByLabelText("Новый пароль"), { target: { value: "safe password 123" } });
    fireEvent.change(screen.getByLabelText("Повторите пароль"), { target: { value: "safe password 123" } });
    fireEvent.submit(screen.getByRole("button", { name: "Продолжить к TOTP" }).closest("form")!);

    expect(await screen.findByRole("heading", { name: "Подключите приложение-аутентификатор" })).toBeInTheDocument();
    expect(screen.getByDisplayValue(/otpauth:\/\/totp/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/auth/setup",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
