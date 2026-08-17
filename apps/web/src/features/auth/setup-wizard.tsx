import { FormEvent, useEffect, useState } from "react";

import { activateSetup, ApiError, confirmTotp } from "../../shared/api/client";

type Enrollment = { enrollmentToken: string; totpUri: string };

export function SetupWizard({ setupToken }: { setupToken: string }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    window.history.replaceState({}, "", "/setup");
  }, []);

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("Пароли не совпадают.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const pending = await activateSetup(setupToken, password);
      setEnrollment({ enrollmentToken: pending.enrollment_token, totpUri: pending.totp_uri });
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 401
        ? "Ссылка недействительна, уже использована или истекла."
        : "Не удалось завершить настройку. Повторите попытку позже.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitTotp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enrollment) return;
    setError(null);
    setSubmitting(true);
    try {
      setRecoveryCodes(await confirmTotp(enrollment.enrollmentToken, code));
    } catch {
      setError("Код приложения-аутентификатора не принят.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!setupToken) return <main className="centered">Ссылка для настройки недействительна.</main>;
  if (recoveryCodes) {
    return <main className="login-layout"><section className="login-card">
      <p className="eyebrow">AIsales</p><h1>Сохраните recovery-коды</h1>
      <p className="muted">Каждый код работает один раз. Сохраните их в защищённом месте — после ухода со страницы они больше не покажутся.</p>
      <ul className="recovery-codes">{recoveryCodes.map((value) => <li key={value}>{value}</li>)}</ul>
      <a href="/">Перейти ко входу</a>
    </section></main>;
  }
  if (enrollment) {
    return <main className="login-layout"><section className="login-card">
      <p className="eyebrow">AIsales</p><h1>Подключите приложение-аутентификатор</h1>
      <p className="muted">Добавьте этот TOTP URI в приложение-аутентификатор вручную, затем введите шестизначный код.</p>
      <input aria-label="TOTP URI" readOnly value={enrollment.totpUri} />
      <form onSubmit={submitTotp}><label>Код приложения-аутентификатора
        <input autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} required />
      </label>{error && <p role="alert" className="error">{error}</p>}<button type="submit" disabled={submitting}>{submitting ? "Проверяем…" : "Подтвердить TOTP"}</button></form>
    </section></main>;
  }
  return <main className="login-layout"><section className="login-card">
    <p className="eyebrow">AIsales</p><h1>Настройте доступ</h1><p className="muted">Создайте пароль. На следующем шаге понадобится приложение-аутентификатор.</p>
    <form onSubmit={submitPassword}>
      <label>Новый пароль<input autoComplete="new-password" type="password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
      <label>Повторите пароль<input autoComplete="new-password" type="password" minLength={12} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
      {error && <p role="alert" className="error">{error}</p>}<button type="submit" disabled={submitting}>{submitting ? "Сохраняем…" : "Продолжить к TOTP"}</button>
    </form>
  </section></main>;
}
