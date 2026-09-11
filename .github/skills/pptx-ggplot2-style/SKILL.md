---
name: pptx-ggplot2-style
description: "Transform any uploaded PPTX into a detailed, presentation-ready web deck with a bright ggplot2-inspired visual style. Use when the user asks to upload, explain, restyle, redesign, visualize, or convert a PowerPoint presentation into ggplot2 style, including finance, quantitative finance, analytics, code walkthroughs, and data-heavy presentations."
argument-hint: "Describe the PPTX to restyle and the desired audience or level of detail"
user-invocable: true
disable-model-invocation: false
---

# PPTX to ggplot2 Style

## Purpose

Turn an uploaded PowerPoint deck into a coherent web presentation that preserves the source deck's meaning while replacing its visual language with an intentional ggplot2-inspired system. The default theme is bright, analytical, and editorial: warm paper background, visible plotting grid, restrained panels, coral/teal/blue data accents, and typography that supports scanning.

The result should be a usable presentation, not a generic landing page. Keep the original narrative, facts, equations, labels, and ordering unless the user explicitly asks for editorial changes.

## When to Use

Use this skill when the user asks to:

- restyle or redesign an uploaded `.pptx` file;
- convert a PowerPoint into ggplot2 style;
- explain a technical, financial, quantitative, scientific, or analytics deck on the web;
- turn PPTX content into a slide-by-slide web presentation;
- preserve the source slides while improving hierarchy, charts, equations, and visual consistency.

## Workflow

### 1. Inspect the source deck

1. Confirm that the input is a readable `.pptx` file.
2. Extract slide count, slide order, titles, visible text, notes if available, and embedded media metadata.
3. Build a compact content inventory before designing:
   - slide number and title;
   - purpose of the slide;
   - key claim or takeaway;
   - equations, tables, charts, and code blocks;
   - source references and required labels.
4. Do not invent numerical results. Mark missing values as unavailable or use clearly labeled illustrative placeholders only when the user approves.

In the current web app, the existing endpoint `/api/pptx/inspect` can provide slide text and counts. If the uploaded deck needs richer extraction, use a PPTX parser or unzip the OOXML package and inspect slide XML, relationships, notes, and media files.

### 2. Decide the presentation architecture

Choose the smallest structure that preserves the source narrative:

- overview/title;
- context or inputs;
- method/model;
- data or schedule;
- calculations and transformations;
- comparison or decision logic;
- outputs, diagnostics, and conclusion.

For technical and financial decks, prefer a process narrative such as:

`inputs -> model -> paths/data -> cash flows -> decision -> aggregation -> diagnostics`

Do not collapse a complex source into a few shallow marketing slides. It is acceptable to add explanatory slides when they clarify a real calculation or dependency.

### 3. Apply the bright ggplot2 visual system

Use these defaults unless the user specifies another palette:

- background: warm paper `#f5f2eb`;
- primary ink: charcoal `#27313a`;
- muted text: gray `#68727a`;
- plotting grid: light gray `#d8d5cd`;
- primary accent: coral `#e76f51`;
- secondary accent: teal `#2a9d8f`;
- comparison accent: blue `#457b9d`;
- highlight: yellow `#e9c46a`;
- panel: off-white `#fbfaf6`.

Use visible plotting grids, direct labels, restrained borders, and chart-like annotations. Avoid purple-on-white defaults, excessive rounded cards, decorative blobs, fake dashboards, and large empty hero areas. Keep cards flat and rectangular with small radii or no radius.

Typography should be expressive but readable. Prefer `IBM Plex Sans KR`, `Space Grotesk`, and `DM Mono` or a comparable local fallback. Use monospace for equations, code, parameter names, and axis-like labels.

### 4. Convert content, do not merely paste it

For each slide:

- preserve the source title and core message;
- rewrite dense paragraphs into a clear hierarchy without changing meaning;
- represent relationships as timelines, small multiples, matrices, flow diagrams, or plots;
- show equations in a dedicated formula block;
- show code as compact excerpts with the function name and role;
- use direct labels instead of legends when this improves readability;
- explain what a chart means immediately below or beside it.

For quantitative finance, explicitly distinguish:

- market inputs versus model assumptions;
- deterministic terms versus stochastic factors;
- pathwise values versus expected values;
- exercise value versus continuation value;
- model outputs versus validation diagnostics.

### 5. Implement the web deck

For an existing Flask app:

1. Add a dedicated route and template for the generated deck.
2. Add a dedicated stylesheet rather than altering unrelated screens.
3. Use one active slide at a time with keyboard and button navigation.
4. Include a progress indicator and slide counter.
5. Ensure the deck works at desktop and mobile widths.
6. Keep internal links relative or route-aware.
7. If the user requests external hosting, also create a static bundle such as `docs/index.html` and a colocated stylesheet that does not depend on Flask's `url_for`.

For static hosting such as GitHub Pages:

- use relative asset paths;
- remove Flask/Jinja expressions;
- keep all presentation behavior client-side;
- put the entry point in `docs/index.html` when deploying from the `main` branch `/docs` folder;
- add cache-busting query strings to versioned CSS/JS when a stale deployment is likely.

### 6. Validate before presenting

Run focused checks:

- verify the PPTX was read and slide order is preserved;
- verify every generated slide has a title or clear visual purpose;
- check HTML/CSS paths and template syntax;
- check the route returns successfully when dependencies are installed;
- open the deck in a browser and test next/previous navigation, keyboard navigation, responsive layout, and the first/last slide wraparound;
- confirm no source claims, equations, or important labels disappeared;
- for GitHub Pages, request the deployed HTML and stylesheet and verify the deployed theme variables and relative links.

If browser automation is available, inspect both desktop and mobile viewports and capture screenshots. Report any dependency or authentication blocker instead of claiming that the deck was deployed.

## Output Contract

Return:

1. the local route or static entry file;
2. a brief summary of the visual system and slide structure;
3. validation performed and any blockers;
4. deployment URL only after the hosting service confirms the deployment.

Do not claim that a PPTX was converted or published unless the generated files and the serving URL were actually checked.
