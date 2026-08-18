# Premium Login Screen Design

## Goal

Refine the AIsales sign-in surface into a calm, premium and friendly product
experience without changing the authentication protocol or weakening its
security controls.

## Approved Direction

Use the calm blue-graphite composition from the first visual exploration:
generous negative space, a restrained dark card, one violet action and no
dashboard chrome. Use a compact rounded-square `A` monogram inspired by the
second exploration as a temporary product mark. It must be implemented as a
simple local SVG/CSS mark, not as a generated raster asset or a real-brand
logo.

The header is centred; form labels and controls remain left-aligned for fast
scanning and reliable error association.

## Content

The screen shows, in order:

1. centred AIsales monogram;
2. centred `AIsales` product name;
3. centred `Рабочее пространство продаж` subtitle;
4. `Электронная почта` field;
5. `Пароль` field;
6. `Google Authenticator` one-time-code field;
7. a secondary text action, `Использовать код восстановления`;
8. one full-width violet primary action, `Войти`;
9. a small `Защищённый вход` reassurance line.

Selecting the recovery-code action switches only the third-field label and
input purpose to `Код восстановления`. A reciprocal action returns to the
Google Authenticator code. It continues to use the existing server endpoint;
no token, password or recovery code is exposed in URLs, logs or local storage.

## Rationale

Recovery codes remain necessary even when email recovery is introduced later:
they are an independent, single-use fallback when the authenticator or email
account is unavailable. Email recovery will be an additional protected flow,
not a replacement for the second-factor fallback.

## Visual Rules

- Keep the existing dark blue-graphite palette and violet accent, but reduce
  hard borders and visual density.
- Use one neutral sans-serif family with deliberate weight and spacing rather
  than a decorative serif wordmark.
- Centre the identity block but do not centre field labels, validation errors or
  input values.
- Keep keyboard focus highly visible and contrast accessible.
- On narrow screens, use a near-full-width card and preserve 16px minimum side
  padding; do not hide any authentication option.

## States and Non-goals

The existing loading, invalid-credential and service-unavailable states remain
supported and must use the same visual hierarchy. This slice does not add email
delivery, password reset, social sign-in, a light theme or changes to API
contracts.

## Verification

- React tests cover normal authenticator and recovery-code form states.
- Existing authentication tests remain green.
- Desktop and mobile visual inspection confirms spacing, focus and legibility.
- Build output contains no secret, setup token or recovery code.
