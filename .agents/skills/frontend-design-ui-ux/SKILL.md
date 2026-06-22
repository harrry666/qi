---
name: frontend-design-ui-ux
version: 2.0.0
description: |
  Produce implementation-ready UX and UI specifications: user flows, states, component briefs,
  design tokens, accessibility constraints, and handoff guidance for frontend engineering agents.
  Use for new features, redesigns, or design-system work where implementation should follow a clear
  product and interaction spec.
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
argument-hint: "[feature, screen, or design problem]"
arguments:
  - request
when_to_use: |
  Use when the user wants interface design, flow design, component specification, or design-system
  work rather than direct implementation. Examples: "design this dashboard", "spec the onboarding
  flow", "create a handoff for this feature", "define tokens and states for this component".
effort: high
---

<EXTREMELY-IMPORTANT>
This is a design-spec skill, not an implementation skill.

Non-negotiable rules:
1. Understand users, context, and states before proposing screens.
2. Specify flows, states, accessibility, and responsiveness, not just visuals.
3. Reuse the templates in `references/` instead of inventing new handoff formats.
4. Keep implementation handoff concrete enough that an engineering agent can build from it.
5. Do not start coding the UI from this skill.
</EXTREMELY-IMPORTANT>

# frontend-design-ui-ux

## Inputs

- `$request`: Feature, page, component, or UX problem to design

## Goal

Produce a design handoff that covers:

- user goals
- flow and state model
- component structure
- design tokens
- accessibility and responsive behavior
- target engineering handoff

## Step 0: Discovery

Resolve:

- target users
- product goal
- device and usage context
- existing design-system or product constraints
- accessibility expectations

If the problem statement is underspecified, ask before designing.

**Success criteria**: The design problem is framed in user and product terms, not just screens.

## Step 1: Model flows and states

Document:

- primary user journey
- edge cases
- loading, empty, partial, success, and error states
- key decisions or branching points

Use `references/user-flow-template.md` when writing flow documentation.

**Success criteria**: The design covers behavior across states, not just the happy path.

## Step 2: Specify components and interaction rules

For the needed components, define:

- purpose
- variants
- props or data needs
- states
- interaction feedback
- responsive behavior
- accessibility requirements

Use `references/component-spec-template.md` for structured component briefs.

**Success criteria**: The engineering handoff is concrete enough to implement without guessing.

## Step 3: Define tokens and system-level decisions

Specify only the tokens and conventions the feature actually needs:

- color roles
- typography hierarchy
- spacing and layout rhythm
- motion and feedback rules
- semantic states such as success, warning, danger, disabled

Use `references/design-tokens-template.md` when a token artifact is required.

**Success criteria**: The spec encodes reusable system decisions, not just one-off styling notes.

## Step 4: Handoff to the right engineering surface

### Agent Selection

| Criteria | Target Agent |
|----------|-------------|
| Server-side rendering needed | `nextjs-senior-engineer` |
| SEO critical | `nextjs-senior-engineer` |
| App Router / Server Components | `nextjs-senior-engineer` |
| API routes / Server Actions | `nextjs-senior-engineer` |
| Pure SPA / client-only | `react-vite-tailwind-engineer` |
| CLI with web UI | `react-vite-tailwind-engineer` |
| Electron/Tauri app | `react-vite-tailwind-engineer` |
| Static site with no SSR | Either (ask user) |
| Unclear | Ask user |

State:

- which implementation target and why it fits
- the acceptance criteria for engineering handoff

**Success criteria**: Another agent can pick up the design package and implement it directly.

## Guardrails

- Do not implement production UI code from this skill.
- Do not skip accessibility, state coverage, or responsive behavior.
- Do not produce vague design prose when a concrete artifact is needed.
- Do not ignore existing product patterns unless the user asked for a redesign.

## When To Load References

- `references/user-flow-template.md`
  Use for user journeys and acceptance flow documentation.

- `references/component-spec-template.md`
  Use for component briefs and interface contracts.

- `references/design-tokens-template.md`
  Use when the task requires tokenized styling decisions or system handoff.

## Output Contract

Report or write:

1. user and context summary
2. flows and states
3. component specs
4. design tokens or styling rules if needed
5. handoff target and implementation acceptance criteria
