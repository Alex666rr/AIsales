import { FormEvent, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

import {
  ApiError,
  confirmTelegramPassword,
  confirmTelegramPhoneCode,
  getTelegramQrStatus,
  startTelegramPhone,
  startTelegramQr,
  TelegramConnectionAttempt,
  TelegramQrStart,
} from "../../shared/api/client";

type ConnectionMode = "phone" | "qr";

function errorCopy(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "Подключать аккаунты может только владелец организации.";
  }
  return "Не удалось продолжить подключение. Попробуйте ещё раз.";
}

export function TelegramConnectionPanel() {
  const [mode, setMode] = useState<ConnectionMode>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [attempt, setAttempt] = useState<TelegramConnectionAttempt | null>(null);
  const [qr, setQr] = useState<TelegramQrStart | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startPhone(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAttempt(await startTelegramPhone(phone));
      setCode("");
      setPassword("");
      setQr(null);
    } catch (reason) {
      setError(errorCopy(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitPhoneStep(event: FormEvent) {
    event.preventDefault();
    if (!attempt) return;
    setBusy(true);
    setError(null);
    try {
      const next = attempt.status === "password_required"
        ? await confirmTelegramPassword(attempt.attempt_id, password)
        : await confirmTelegramPhoneCode(attempt.attempt_id, code);
      setAttempt(next);
    } catch (reason) {
      setError(errorCopy(reason));
    } finally {
      setBusy(false);
    }
  }

  async function startQr() {
    setBusy(true);
    setError(null);
    try {
      const started = await startTelegramQr();
      setMode("qr");
      setQr(started);
      setAttempt(started);
    } catch (reason) {
      setError(errorCopy(reason));
    } finally {
      setBusy(false);
    }
  }

  async function checkQr() {
    if (!attempt) return;
    setBusy(true);
    setError(null);
    try {
      setAttempt(await getTelegramQrStatus(attempt.attempt_id));
    } catch (reason) {
      setError(errorCopy(reason));
    } finally {
      setBusy(false);
    }
  }

  const completed = attempt?.status === "authorized";
  const requiresPassword = attempt?.status === "password_required";
  const needsCode = attempt?.status === "code_requested";

  return (
    <section className="telegram-connection" aria-labelledby="connection-heading">
      <div>
        <h2 id="connection-heading">Подключить Telegram</h2>
        <p className="muted">Подключение подтверждается в Telegram. Пароль и коды не сохраняются в браузере.</p>
      </div>
      <div className="connection-actions" aria-label="Способ подключения">
        <button type="button" className={mode === "phone" ? "secondary active" : "secondary"} onClick={() => { setMode("phone"); setError(null); }}>
          По номеру
        </button>
        <button type="button" className="secondary" onClick={() => void startQr()} disabled={busy}>
          Подключить по QR
        </button>
      </div>
      {mode === "phone" && !attempt && <form onSubmit={startPhone} className="connection-form">
        <label>Номер Telegram
          <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+79990000000" inputMode="tel" autoComplete="tel" required />
        </label>
        <button type="submit" disabled={busy}>Подключить по номеру</button>
      </form>}
      {mode === "phone" && attempt && !completed && <form onSubmit={submitPhoneStep} className="connection-form">
        {needsCode && <label>Код из Telegram
          <input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" autoComplete="one-time-code" maxLength={8} required />
        </label>}
        {requiresPassword && <label>Пароль Telegram
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" required />
        </label>}
        <button type="submit" disabled={busy}>{requiresPassword ? "Подтвердить пароль" : "Подтвердить код"}</button>
      </form>}
      {mode === "qr" && qr && !completed && <div className="qr-connect">
        <QRCodeSVG value={qr.qr_url} size={184} level="M" includeMargin />
        <p className="muted">Откройте Telegram на телефоне, отсканируйте код и вернитесь сюда.</p>
        <button type="button" onClick={() => void checkQr()} disabled={busy}>Проверить вход</button>
      </div>}
      {completed && <p className="connection-success" role="status">Аккаунт подключён и отправлен в карантин на проверку.</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
