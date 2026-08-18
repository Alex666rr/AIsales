# Premium Login Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the AIsales login form into the approved premium dark product screen without changing the authentication API.

**Architecture:** `LoginForm` remains the sole UI boundary for `login()`. Local view state switches the third field between TOTP and recovery code; the existing server payload remains unchanged. CSS is confined to the existing web stylesheet.

**Tech Stack:** React, TypeScript, Vitest, Testing Library, Vite, CSS.

## Global Constraints

- Keep `POST /auth/login` semantics and server-side session handling unchanged.
- Use a local SVG/CSS monogram, never a generated raster asset or external logo.
- Header identity is centred; labels, values and errors are left-aligned.
- Use approved Russian copy and never put credentials, codes or tokens in URLs, logs or local storage.
- Preserve visible focus and mobile padding of at least 16px.

---

## File Structure

- Modify `apps/web/src/features/auth/login-form.tsx`: approved copy, local recovery mode and semantic brand markup.
- Modify `apps/web/src/app/app.test.tsx`: default and recovery-mode visible-state contracts.
- Modify `apps/web/src/styles.css`: premium login-only layout, mark, focus and narrow-screen rules.

### Task 1: Create the accessible authentication-mode UI

**Files:**
- Modify: `apps/web/src/features/auth/login-form.tsx:9-59`
- Modify: `apps/web/src/app/app.test.tsx:1-70`

**Interfaces:**
- Consumes: `login({ email, password, totp_code?, recovery_code? })` from `shared/api/client.ts`.
- Produces: default `Google Authenticator` mode and recovery mode that submit exactly one second-factor field.

- [ ] **Step 1: Write failing tests**

Add assertions for the approved default state:

```tsx
expect(await screen.findByRole("heading", { name: "AIsales" })).toBeInTheDocument();
expect(screen.getByText("Рабочее пространство продаж")).toBeInTheDocument();
expect(screen.getByLabelText("Google Authenticator")).toBeInTheDocument();
expect(screen.getByRole("button", { name: "Использовать код восстановления" })).toBeInTheDocument();
```

Add a test that clicks the recovery action and expects `Код восстановления`, `Вернуться к Google Authenticator`, and no checkbox.

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/web test -- app/app.test.tsx`

Expected: FAIL because the old component says `Вход в AIsales` and uses a checkbox.

- [ ] **Step 3: Implement the minimum form change**

Replace the old eyebrow/title/subtitle with a centred mark, `AIsales`, and `Рабочее пространство продаж`. Rename labels to `Электронная почта` and `Google Authenticator`. Replace the checkbox with:

```tsx
<button className="recovery-toggle" type="button" onClick={() => setIsRecoveryCode((value) => !value)}>
  {isRecoveryCode ? "Вернуться к Google Authenticator" : "Использовать код восстановления"}
</button>
```

Keep the existing object spread so recovery mode sends only `recovery_code` and default mode sends only `totp_code`. Add `Защищённый вход` below the submit button.

- [ ] **Step 4: Verify GREEN and commit**

Run: `pnpm --dir apps/web test -- app/app.test.tsx`

Expected: PASS.

```bash
git add apps/web/src/features/auth/login-form.tsx apps/web/src/app/app.test.tsx
git commit -m "feat: refine login authentication choices"
```

### Task 2: Apply the approved premium visual system

**Files:**
- Modify: `apps/web/src/styles.css:1-71`
- Modify: `apps/web/src/features/auth/login-form.tsx`
- Modify: `apps/web/src/app/app.test.tsx`

**Interfaces:**
- Consumes: the Task 1 semantic controls.
- Produces: a centred identity block, left-aligned form, local temporary mark and responsive dark screen.

- [ ] **Step 1: Write failing mark-structure test**

```tsx
expect(screen.getByTestId("login-monogram")).toHaveAttribute("aria-hidden", "true");
expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/web test -- app/app.test.tsx`

Expected: FAIL before the local mark exists.

- [ ] **Step 3: Implement constrained CSS**

Create `.login-brand`, `.login-monogram`, `.login-subtitle`, `.recovery-toggle`, and `.login-reassurance`. Use existing blue-graphite/violet tokens, one primary button, high-contrast `:focus-visible`, and a narrow-screen rule with 16px side padding. Do not add remote fonts, gradients, animation, icon libraries or new dependencies.

- [ ] **Step 4: Verify GREEN and commit**

Run: `pnpm --dir apps/web test -- app/app.test.tsx`

Expected: PASS.

```bash
git add apps/web/src/styles.css apps/web/src/features/auth/login-form.tsx apps/web/src/app/app.test.tsx
git commit -m "style: polish premium AIsales login"
```

### Task 3: Verify production behavior

**Files:**
- Modify only if verification identifies a regression.
- Test: `apps/web/src/app/app.test.tsx`, existing web tests and `apps/api/tests/modules/auth`.

**Interfaces:**
- Consumes: completed UI and existing authentication API.
- Produces: evidence that the new visuals preserve the login contract.

- [ ] **Step 1: Run web tests and build**

```bash
pnpm --dir apps/web test
pnpm --dir apps/web build
```

Expected: both commands exit 0.

- [ ] **Step 2: Run backend auth regression tests**

```bash
python -m pytest apps/api/tests/modules/auth -q
```

Expected: PASS; this slice changes no backend contract.

- [ ] **Step 3: Perform bounded visual inspection**

Inspect anonymous login at desktop and approximately 390px width. Confirm centred identity, left-aligned labels/errors, visible focus, all controls visible, recovery action changes only the third field, and no secret appears in the URL.

- [ ] **Step 4: Verify diff and commit**

```bash
git diff --check
git status --short
git add apps/web/src/features/auth/login-form.tsx apps/web/src/styles.css apps/web/src/app/app.test.tsx
git commit -m "test: verify premium login flow"
```
