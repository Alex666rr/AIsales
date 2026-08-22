import { FormEvent, useState } from "react";

import { ApiError, login } from "../../shared/api/client";

type LoginFormProps = {
  onAuthenticated: () => Promise<void>;
};

type FieldIconProps = {
  kind: "email" | "password" | "verification";
};

function FieldIcon({ kind }: FieldIconProps) {
  if (kind === "email") {
    return <svg viewBox="0 0 24 24"><path d="M3.5 6.5h17v11h-17v-11Zm.8.7L12 12.5l7.7-5.3M4.3 16.8l5.9-5M19.7 16.8l-5.9-5" /></svg>;
  }
  if (kind === "password") {
    return <svg viewBox="0 0 24 24"><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v2" /></svg>;
  }
  return <svg viewBox="0 0 24 24"><path d="M12 3.5 19 6v5.4c0 4.1-2.8 7.7-7 9.1-4.2-1.4-7-5-7-9.1V6l7-2.5Z" /><path d="m8.8 12 2 2 4.4-4.4" /></svg>;
}

function PasswordVisibilityIcon() {
  return <svg viewBox="0 0 24 24" focusable="false"><path d="M2.8 12s3.4-5 9.2-5 9.2 5 9.2 5-3.4 5-9.2 5-9.2-5-9.2-5Z" /><circle cx="12" cy="12" r="2.35" /></svg>;
}

export function LoginForm({ onAuthenticated }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const [verification, setVerification] = useState("");
  const [isRecoveryCode, setIsRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login({
        email,
        password,
        ...(verification && (isRecoveryCode ? { recovery_code: verification } : { totp_code: verification })),
      });
      await onAuthenticated();
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 401 ? "Проверьте данные входа и второй фактор." : "Сервер временно недоступен. Попробуйте ещё раз.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function toggleVerificationMethod() {
    setIsRecoveryCode((current) => !current);
    setVerification("");
  }

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <header className="login-brand">
          <span className="login-monogram" data-testid="login-monogram" aria-hidden="true">
            <svg viewBox="0 0 72 72" focusable="false">
              <circle className="monogram-ring" cx="36" cy="36" r="29" />
              <path className="monogram-stroke" d="m21 49 15-27 15 27m-21.5-11h13" />
              <circle className="monogram-point" cx="36" cy="42" r="2.6" />
            </svg>
          </span>
          <h1 id="login-title">AIsales</h1>
          <p className="login-subtitle">Рабочее пространство продаж</p>
        </header>
        <form onSubmit={submit}>
          <label>
            Электронная почта
            <span className="login-input-shell">
              <span className="login-field-icon" data-testid="login-field-icon" aria-hidden="true"><FieldIcon kind="email" /></span>
              <input autoComplete="email" placeholder="name@company.ru" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
            </span>
          </label>
          <label>
            Пароль
            <span className="login-input-shell">
              <span className="login-field-icon" data-testid="login-field-icon" aria-hidden="true"><FieldIcon kind="password" /></span>
              <input autoComplete="current-password" placeholder="Введите пароль" type={isPasswordVisible ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} required />
              <button
                aria-label={isPasswordVisible ? "Скрыть пароль" : "Показать пароль"}
                className="password-visibility-indicator"
                data-testid="password-visibility-icon"
                onClick={() => setIsPasswordVisible((current) => !current)}
                type="button"
              >
                <PasswordVisibilityIcon />
              </button>
            </span>
          </label>
          <label>
            {isRecoveryCode ? "Код восстановления" : "Google Authenticator"}
            <span className="login-input-shell">
              <span className="login-field-icon" data-testid="login-field-icon" aria-hidden="true"><FieldIcon kind="verification" /></span>
              <input
                autoComplete={isRecoveryCode ? "off" : "one-time-code"}
                inputMode={isRecoveryCode ? "text" : "numeric"}
                maxLength={isRecoveryCode ? undefined : 6}
                placeholder={isRecoveryCode ? "Код восстановления" : "000 000"}
                value={verification}
                onChange={(event) => setVerification(isRecoveryCode ? event.target.value : event.target.value.replace(/\D/g, "").slice(0, 6))}
              />
            </span>
          </label>
          <p className="recovery-help">
            {!isRecoveryCode && "Нет доступа к приложению? "}
            <button className="verification-toggle" type="button" onClick={toggleVerificationMethod}>
              {isRecoveryCode ? "Вернуться к Google Authenticator" : "Использовать код восстановления"}
            </button>
          </p>
          {error && <p role="alert" className="error">{error}</p>}
          <button type="submit" disabled={isSubmitting}>{isSubmitting ? "Проверяем…" : "Войти"}</button>
        </form>
        <p className="login-security-note"><FieldIcon kind="verification" />Защищённый вход</p>
      </section>
    </main>
  );
}
