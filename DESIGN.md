---
name: AIsales
description: Спокойная операционная платформа для безопасной работы с Telegram-аккаунтами.
colors:
  accent: "#665CFF"
  canvas: "#101827"
  surface: "#182334"
  surface-raised: "#223147"
  text-primary: "#F4F7FB"
  text-secondary: "#9CABBE"
  border: "#2B3B52"
  success: "#6FE2AD"
  warning: "#F8C56A"
  danger: "#FF8D8D"
typography:
  display:
    fontFamily: "Aptos, Segoe UI, system-ui, sans-serif"
    fontWeight: 750
    lineHeight: 1.15
  body:
    fontFamily: "Aptos, Segoe UI, system-ui, sans-serif"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  control: "8px"
  surface: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  surface-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.surface}"
    padding: "{spacing.md}"
---

# Design System: AIsales

## Overview

**Creative North Star: "The Calm Control Room"**

AIsales is quiet, precise and always legible. Darkness comes from deep
blue-graphite surfaces, never pure black, so long work sessions stay comfortable.

**Key Characteristics:**

- Dense operational information with a clear scan path.
- One violet action voice; risk colours retain semantic meaning.
- Tonal layers and crisp borders create hierarchy instead of heavy shadows.
- Motion explains state changes and never decorates an idle screen.

## Colors

The palette is low-saturation so account health and warnings remain the strongest signals.

### Primary

- **Command Violet:** one primary action per local context and selected navigation.

### Neutral

- **Night Canvas:** page background without the contrast fatigue of black.
- **Operational Surfaces:** blue-graphite panels for controls and tables.

**The One Voice Rule.** Violet denotes action and selection only. Success, warning and danger never become decorative accents.

## Typography

**Display Font:** Aptos (with Segoe UI and system UI fallbacks)

**Body Font:** Aptos (with Segoe UI and system UI fallbacks)

**Character:** direct, contemporary and compact. Headings identify work surfaces, not marketing claims.

### Hierarchy

- **Display:** 28–32px, 750 weight, page-level work title.
- **Title:** 18–20px, 700 weight, panel or task group.
- **Body:** 14–16px, 400–500 weight, explanatory text and values.
- **Label:** 11–12px, 650–700 weight, metadata and table headings.

## Layout

Desktop has persistent compact left navigation and a flexible workspace. At narrow widths navigation becomes horizontally scrollable and tables become labelled stacked rows.

## Elevation & Depth

Depth is tonal first: canvas, surface and raised surface. Borders show containment; shadows are limited to overlays, menus and active drag states.

**The Quiet Depth Rule.** A resting panel never floats merely for decoration.

## Shapes

Controls use gently rounded 8px corners; larger containers use 12px. Pills are reserved for compact state labels, never every control.

## Components

### Buttons

- **Primary:** violet, concise verb, one per local action group.
- **Secondary:** surface-raised with visible border.
- **Destructive:** danger tint only after a confirmation context.
- **Focus:** high-contrast violet ring visible against every surface.

### Cards / Containers

- **Background:** operational surface with a quiet border.
- **Internal Padding:** medium by default; compact in dense tables.

### Inputs / Fields

- **Style:** surface-raised background, visible border and explicit label.
- **Focus:** violet ring plus border; never colour alone.
- **Error:** label, field message and danger border.

### Navigation

Default items are muted; the active item has violet fill and white text.

## Do's and Don'ts

### Do:

- **Do** make status, last activity and next action scannable in one line.
- **Do** use semantic status colours only with an accompanying text label.
- **Do** preserve clear empty, loading, failure and permission-denied states.
- **Do** keep sensitive tokens, recovery codes and QR data out of screenshots.

### Don't:

- **Don't** use pure black, neon gradients, glassmorphism or crypto motifs.
- **Don't** make every fact a rounded card.
- **Don't** use red or green as the only way to communicate meaning.
- **Don't** add decorative animation to dense operational views.
