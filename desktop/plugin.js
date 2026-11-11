import { useState, useEffect, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import {
  host,
  Button,
  Input,
  Badge,
  Separator,
  Codicon,
  writeClipboardText
} from '@hermes/plugin-sdk'

export const PLUGIN_ID = 'defuzz-componentize'
export const PLUGIN_VERSION = '0.2.0'
export const DEFAULT_CHECKER_VERSION = '1.3.0'
export const DEFAULT_TIMEOUT_SECONDS = 900
export const ISSUE_TRACKER_URL = 'https://github.com/defuzzr/defuzz-componentize/issues'

// Generated from vendor/kits/*/kit.json by scripts/gen_kit_fallback.py (--check to verify).
export const BUNDLED_KITS_FALLBACK = [
  {
    id: 'client-job',
    name: 'Client / freelance job',
    summary: 'Client or freelance job workspace: read-only brief, contract, references and intake, working files, review rounds, frozen deliveries, finance and licences.',
    tags: ['client', 'job', 'freelance', 'agency', 'contract', 'invoice', 'commission', 'clients'],
    availability: 'available',
    version: 1
  },
  {
    id: 'comic-storyboard',
    name: 'Comic and storyboard',
    summary: 'Comic and storyboard workspace with script, character sheets, per-page stage folders, animatics, review rounds and Git LFS rules.',
    tags: ['comic', 'storyboard', 'manga', 'panels', 'illustration', 'comics', 'storyboards', 'graphic', 'novel', 'animatic', 'character'],
    availability: 'available',
    version: 1
  },
  {
    id: 'component-library',
    name: 'Reusable Python code library',
    summary: 'Reusable Python code library: snake_case.py modules with a COMPONENTS.md index and tests, thin runner scripts and componentize specs; not a UI or design-system component library.',
    tags: ['python', 'code', 'modules', 'components', 'reuse', 'componentize'],
    availability: 'available',
    version: 3
  },
  {
    id: 'game-assets',
    name: 'Game workspace: Unreal + Blender + Houdini',
    summary: 'Game workspace with one folder per source asset, an explicit source-to-Unreal manifest, and Git LFS rules for Blender, Houdini and texture files.',
    tags: ['game', 'unreal', 'ue5', 'blender', 'houdini', 'assets', 'lfs'],
    availability: 'available',
    version: 2
  },
  {
    id: 'godot-game',
    name: 'Godot solo game',
    summary: 'Solo Godot game workspace with game-ready assets, scenes, GDScript, resources, source art hidden via .gdignore, and Git LFS rules.',
    tags: ['godot', 'game', 'gdscript', 'scenes', 'shaders', 'indie', 'games'],
    availability: 'available',
    version: 1
  },
  {
    id: 'homelab',
    name: 'Homelab infrastructure',
    summary: 'Homelab and infrastructure workspace: host inventory, per-host configs, shared Compose stacks, network rules, runbooks, backup records and changelog.',
    tags: ['homelab', 'infra', 'infrastructure', 'selfhosted', 'servers', 'network', 'sysadmin', 'nas', 'host', 'hosts', 'inventory', 'runbooks', 'lab'],
    availability: 'available',
    version: 1
  },
  {
    id: 'image-set',
    name: 'Image generation set',
    summary: 'AI still-image generation workspace: read-only references, prompts, ComfyUI workflows, model notes, staged outputs from raw to approved finals, and contact sheets; not LoRA training or a render queue.',
    tags: ['image', 'images', 'stills', 'comfyui', 'diffusion', 'generative', 'prompts', 'upscale', 'inpaint'],
    availability: 'available',
    version: 1
  },
  {
    id: 'lora-training',
    name: 'LoRA and model training',
    summary: 'LoRA adapter training workspace for image or text models: staged dataset and captions, datasheets, configs, isolated run folders, fixed evaluation sets and versioned releases; not prompt-based image generation.',
    tags: ['lora', 'training', 'dataset', 'captions', 'finetune', 'checkpoint', 'eval', 'weights'],
    availability: 'available',
    version: 1
  },
  {
    id: 'music-production',
    name: 'Music and audio production',
    summary: 'Music and audio production workspace with a shared library, per-song folders for generations, recordings, stems, mixes and masters, and releases.',
    tags: ['music', 'production', 'audio', 'song', 'songs', 'track', 'album', 'ep', 'daw', 'mixing', 'mastering', 'stems'],
    availability: 'available',
    version: 1
  },
  {
    id: 'research-writing',
    name: 'Research and writing',
    summary: 'Research and writing workspace: read-only sources, reading and synthesis notes, versioned outlines, section drafts, figures, citations and frozen releases.',
    tags: ['research', 'writing', 'paper', 'report', 'citations', 'bibtex', 'thesis', 'literature', 'essay', 'academic', 'outline', 'dissertation'],
    availability: 'available',
    version: 1
  },
  {
    id: 'studio-3d',
    name: '3D design studio: Blender, Houdini, UE5, Godot & web',
    summary: 'Multi-tool 3D design studio workspace: per-asset sources/exports, Blender and Houdini DCC trees, UE5, Godot and Three.js targets, shots and deliveries; not a single-engine game project or an Unreal asset pipeline.',
    tags: ['3d', 'studio', 'dcc', 'blender', 'houdini', 'unreal', 'ue5', 'godot', 'three', 'threejs', 'webgl', 'web', 'assets', 'shots', 'lfs'],
    availability: 'available',
    version: 1
  },
  {
    id: 'video-channel',
    name: 'YouTube and short-form video channel',
    summary: 'YouTube and short-form channel workspace with brand assets, reusable library, idea backlog, episode template, shorts, analytics and Git LFS rules.',
    tags: ['video', 'channel', 'youtube', 'shorts', 'episodes', 'editing', 'captions', 'thumbnails', 'vlog'],
    availability: 'available',
    version: 1
  },
  {
    id: 'video-production',
    name: 'Video production',
    summary: 'AI-assisted video production workspace: locked canon references, generated candidates, approved assets, storyboards, footage, audio, renders and numbered deliverables; not a YouTube channel or a comic.',
    tags: ['video', 'ai', 'storyboard', 'footage', 'production', 'generative', 'lfs'],
    availability: 'available',
    version: 2
  },
  {
    id: 'web-app',
    name: 'Web app + design assets',
    summary: 'Web application workspace with handed-over design sources, design tokens, raw and optimised assets, app code, unit and e2e tests, and Git LFS rules.',
    tags: ['frontend', 'website', 'figma', 'tokens', 'css', 'handoff', 'ui', 'github'],
    availability: 'available',
    version: 1
  }
]

export const KIT_FILTER_GROUPS = [
  { id: 'all', label: 'All (14)', matchTags: [] },
  { id: 'python', label: 'Python', matchTags: ['python', 'components'] },
  { id: 'web', label: 'Web & UI', matchTags: ['web', 'frontend', 'website', 'ui'] },
  { id: 'game', label: 'Games', matchTags: ['game', 'games', 'godot', 'unreal'] },
  { id: '3d', label: '3D & Render', matchTags: ['3d', 'blender', 'houdini', 'render'] },
  { id: 'video', label: 'Video & Storyboard', matchTags: ['video', 'storyboard', 'comic'] },
  { id: 'audio', label: 'Audio', matchTags: ['audio', 'music', 'daw'] },
  { id: 'ai-ml', label: 'AI & Training', matchTags: ['lora', 'training', 'diffusion', 'generative', 'gpu'] },
  { id: 'writing', label: 'Writing & Comics', matchTags: ['writing', 'research', 'comic', 'manga'] },
  { id: 'ops-client', label: 'Infra & Client', matchTags: ['homelab', 'infra', 'client', 'freelance'] }
]

const PAGE_CSS = `
.cz-page {
  --cz-stroke-strong: color-mix(in srgb, var(--ui-text-primary) 56%, var(--ui-bg-editor));
  --cz-write-bg: color-mix(in srgb, var(--ui-accent) 14%, var(--ui-bg-editor));
  --cz-write-border: color-mix(in srgb, var(--ui-accent) 56%, var(--ui-text-primary));
  --cz-write-ink: color-mix(in srgb, var(--ui-accent) 46%, var(--ui-text-primary));
  --cz-ok-bg: color-mix(in srgb, var(--ui-green) 14%, var(--ui-bg-editor));
  --cz-ok-border: color-mix(in srgb, var(--ui-green) 56%, var(--ui-text-primary));
  --cz-ok-ink: color-mix(in srgb, var(--ui-green) 46%, var(--ui-text-primary));
  --cz-warn-bg: color-mix(in srgb, var(--ui-yellow) 15%, var(--ui-bg-editor));
  --cz-warn-border: color-mix(in srgb, var(--ui-yellow) 56%, var(--ui-text-primary));
  --cz-warn-ink: color-mix(in srgb, var(--ui-yellow) 46%, var(--ui-text-primary));
  --cz-danger-bg: color-mix(in srgb, var(--ui-red) 14%, var(--ui-bg-editor));
  --cz-danger-border: color-mix(in srgb, var(--ui-red) 56%, var(--ui-text-primary));
  --cz-danger-ink: color-mix(in srgb, var(--ui-red) 46%, var(--ui-text-primary));
  max-width: 880px;
  margin: 0 auto;
  padding: 16px 16px 48px;
  color: var(--ui-text-primary);
  font-family: var(--dt-font-sans);
  line-height: 1.5;
  box-sizing: border-box;
}
.cz-page *, .cz-page *::before, .cz-page *::after {
  box-sizing: border-box;
}
@media (max-width: 480px) {
  .cz-page {
    padding: 12px 10px 36px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .cz-page *, .cz-page *::before, .cz-page *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
.cz-page :focus-visible {
  outline: 2px solid var(--dt-ring);
  outline-offset: 2px;
}
.cz-skip-link {
  position: absolute;
  left: -9999px;
  top: 8px;
  z-index: 20;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background-color: var(--dt-primary-solid);
  color: var(--dt-primary-solid-foreground);
  font-size: var(--conversation-text-font-size);
  font-weight: 600;
  text-decoration: none;
}
.cz-skip-link:focus {
  left: 12px;
}
.cz-mono, .cz-page code, .cz-page pre {
  font-family: var(--dt-font-mono);
}
.cz-page code {
  font-size: 0.775rem;
  padding: 1px 5px;
  border-radius: 4px;
  background-color: var(--ui-inline-code-background);
  color: var(--ui-inline-code-foreground);
  border: 1px solid var(--ui-stroke-tertiary);
  overflow-wrap: anywhere;
}
.cz-topbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin-bottom: 12px;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
}
.cz-breadcrumb {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.cz-breadcrumb strong {
  color: var(--ui-text-primary);
  font-weight: 600;
}
.cz-scope-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--ui-stroke-secondary);
  background-color: var(--ui-bg-editor);
  color: var(--ui-text-secondary);
  font-size: 0.72rem;
}
.cz-heading-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--ui-stroke-tertiary);
}
.cz-title-block h1 {
  margin: 0;
  font-size: 1.1875rem;
  line-height: 1.3;
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-subtitle {
  margin: 4px 0 0;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
}
.cz-heading-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cz-btn {
  appearance: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-family: inherit;
  font-size: var(--conversation-caption-font-size);
  font-weight: 600;
  line-height: 1.2;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--ui-stroke-secondary);
  background-color: var(--ui-bg-editor);
  color: var(--ui-text-primary);
  cursor: pointer;
  min-height: 30px;
  text-decoration: none;
}
.cz-btn:hover:not(:disabled) {
  background-color: var(--ui-control-hover-background);
}
.cz-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.cz-btn-primary {
  background-color: var(--dt-primary-solid);
  border-color: var(--dt-primary-solid);
  color: var(--dt-primary-solid-foreground);
}
.cz-btn-primary:hover:not(:disabled) {
  filter: brightness(1.08);
  background-color: var(--dt-primary-solid);
}
.cz-section-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  padding: 10px 0;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--ui-stroke-tertiary);
}
.cz-section-nav a {
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  text-decoration: none;
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid transparent;
}
.cz-section-nav a:hover {
  background-color: var(--ui-row-hover-background);
  color: var(--ui-text-primary);
  border-color: var(--ui-stroke-tertiary);
}
.cz-section {
  margin-top: 22px;
  background-color: var(--ui-bg-editor);
  border: 1px solid var(--ui-stroke-tertiary);
  border-radius: var(--radius-md);
  padding: 18px;
}
@media (max-width: 480px) {
  .cz-section {
    padding: 14px 12px;
    margin-top: 16px;
  }
}
.cz-section-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 12px;
  margin-bottom: 10px;
}
.cz-section-header h2 {
  margin: 0;
  font-size: 0.9375rem;
  line-height: 1.35;
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-kicker {
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--ui-text-tertiary);
}
.cz-prose {
  margin: 0 0 10px;
  font-size: var(--conversation-text-font-size);
  line-height: 1.55;
  color: var(--ui-text-secondary);
}
.cz-prose:last-child {
  margin-bottom: 0;
}
.cz-two-col {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
@media (max-width: 620px) {
  .cz-two-col {
    grid-template-columns: minmax(0, 1fr);
  }
}
.cz-cap-card {
  background-color: var(--ui-bg-tertiary);
  border: 1px solid var(--ui-stroke-tertiary);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
}
.cz-cap-card h3 {
  margin: 0 0 6px;
  font-size: var(--conversation-text-font-size);
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-cap-card p {
  margin: 0 0 8px;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  line-height: 1.5;
}
.cz-cap-card p:last-child {
  margin-bottom: 0;
}
.cz-term-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0 14px;
}
@media (max-width: 620px) {
  .cz-term-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.cz-term-item {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-tertiary);
  border: 1px solid var(--ui-stroke-tertiary);
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
}
.cz-term-item strong {
  color: var(--ui-text-primary);
}
.cz-flowchart-wrap {
  margin-top: 12px;
  border: 1px solid var(--ui-stroke-secondary);
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-editor);
  padding: 8px;
  overflow-x: auto;
}
.cz-flowchart-svg {
  display: block;
  width: 100%;
  max-width: 720px;
  min-width: 520px;
  height: auto;
  margin: 0 auto;
  background-color: var(--ui-bg-editor);
  color: var(--ui-text-primary);
  font-family: var(--dt-font-sans);
}
.cz-fc-title { font-size: 13.5px; font-weight: 600; fill: var(--ui-text-primary); }
.cz-fc-cmd { font-size: 13px; font-weight: 600; fill: var(--ui-text-primary); font-family: var(--dt-font-mono); }
.cz-fc-sub { font-size: 11.5px; font-weight: 400; fill: var(--ui-text-secondary); }
.cz-fc-note { font-size: 11px; font-weight: 500; fill: var(--ui-text-tertiary); }
.cz-fc-tag { font-size: 10px; font-weight: 700; letter-spacing: 0.04em; }
.cz-fc-edge { font-size: 10.5px; font-weight: 600; fill: var(--ui-text-secondary); }
.cz-narrow-hint {
  display: none;
  margin: 6px 0 0;
  font-size: 0.7rem;
  color: var(--ui-text-tertiary);
}
@media (max-width: 580px) {
  .cz-narrow-hint {
    display: block;
  }
}
.cz-text-equiv {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-tertiary);
  border: 1px solid var(--ui-stroke-tertiary);
}
.cz-text-equiv-title {
  margin: 0 0 8px;
  font-size: var(--conversation-caption-font-size);
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-step-list {
  margin: 0;
  padding-left: 20px;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  display: grid;
  gap: 6px;
}
.cz-step-list li {
  line-height: 1.5;
}
.cz-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.6875rem;
  font-weight: 650;
  line-height: 1.2;
  padding: 2px 7px;
  border-radius: 4px;
  vertical-align: middle;
  white-space: nowrap;
  margin-right: 4px;
}
.cz-badge-read {
  background-color: var(--ui-bg-secondary);
  border: 1px solid var(--cz-stroke-strong);
  color: var(--ui-text-secondary);
}
.cz-badge-write {
  background-color: var(--cz-write-bg);
  border: 1.5px dashed var(--cz-write-border);
  color: var(--cz-write-ink);
}
.cz-badge-exec {
  background-color: var(--cz-warn-bg);
  border: 1.5px solid var(--cz-warn-border);
  color: var(--cz-warn-ink);
}
.cz-badge-ok {
  background-color: var(--cz-ok-bg);
  border: 1px solid var(--cz-ok-border);
  color: var(--cz-ok-ink);
}
.cz-badge-warn {
  background-color: var(--cz-warn-bg);
  border: 1.5px dashed var(--cz-warn-border);
  color: var(--cz-warn-ink);
}
.cz-badge-err {
  background-color: var(--cz-danger-bg);
  border: 1.5px solid var(--cz-danger-border);
  color: var(--cz-danger-ink);
}
.cz-callout {
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  margin-bottom: 14px;
  font-size: var(--conversation-text-font-size);
}
.cz-callout-title {
  margin: 0 0 4px;
  font-size: var(--conversation-text-font-size);
  font-weight: 650;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.cz-callout p {
  margin: 4px 0 0;
  font-size: var(--conversation-caption-font-size);
  line-height: 1.5;
}
.cz-callout-empty {
  background-color: var(--cz-write-bg);
  border: 1.5px dashed var(--cz-write-border);
  color: var(--ui-text-primary);
}
.cz-callout-error {
  background-color: var(--cz-danger-bg);
  border: 1.5px solid var(--cz-danger-border);
  color: var(--ui-text-primary);
}
.cz-callout-warn {
  background-color: var(--cz-warn-bg);
  border: 1.5px solid var(--cz-warn-border);
  color: var(--ui-text-primary);
}
.cz-cmd-bar {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-editor);
  border: 1px solid var(--ui-stroke-secondary);
}
.cz-cmd-bar code {
  background-color: transparent;
  border: 0;
  padding: 0;
  font-size: 0.775rem;
}
.cz-status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px;
  margin-bottom: 16px;
}
.cz-status-cell {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-tertiary);
  border: 1px solid var(--ui-stroke-tertiary);
}
.cz-status-label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--ui-text-tertiary);
  margin-bottom: 4px;
}
.cz-status-value {
  font-size: var(--conversation-text-font-size);
  font-weight: 600;
  color: var(--ui-text-primary);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  overflow-wrap: anywhere;
}
.cz-status-meta {
  margin-top: 4px;
  font-size: 0.7rem;
  color: var(--ui-text-tertiary);
  line-height: 1.4;
}
.cz-setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(220px, 1fr);
  gap: 12px 20px;
  padding: 14px 0;
  border-top: 1px solid var(--ui-stroke-tertiary);
  align-items: start;
}
@media (max-width: 640px) {
  .cz-setting-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
}
.cz-setting-label {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--conversation-text-font-size);
  font-weight: 600;
  color: var(--ui-text-primary);
}
.cz-setting-help {
  margin: 4px 0 0;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  line-height: 1.5;
}
.cz-rule-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 0.72rem;
  color: var(--ui-text-tertiary);
  line-height: 1.45;
}
.cz-input {
  width: 100%;
  min-height: 34px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--cz-stroke-strong);
  background-color: var(--ui-bg-editor);
  color: var(--ui-text-primary);
  font-size: var(--conversation-text-font-size);
  font-family: inherit;
}
.cz-input.cz-mono {
  font-family: var(--dt-font-mono);
  font-size: 0.775rem;
}
.cz-input[aria-invalid="true"] {
  border-color: var(--cz-danger-border);
  border-width: 2px;
}
.cz-input-suffix-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cz-input-suffix {
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
  white-space: nowrap;
}
.cz-field-msg {
  margin-top: 6px;
  font-size: var(--conversation-caption-font-size);
  line-height: 1.4;
}
.cz-field-msg-err {
  color: var(--cz-danger-ink);
  font-weight: 500;
}
.cz-field-msg-warn {
  color: var(--cz-warn-ink);
}
.cz-storage-footnote {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--ui-stroke-tertiary);
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cz-skeleton-line {
  height: 14px;
  border-radius: 4px;
  background-color: var(--ui-bg-secondary);
  border: 1px solid var(--ui-stroke-tertiary);
  margin-bottom: 8px;
}
.cz-disclosure-table {
  display: grid;
  border: 1px solid var(--ui-stroke-secondary);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-top: 10px;
}
.cz-disclosure-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 8px 14px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--ui-stroke-tertiary);
  font-size: var(--conversation-caption-font-size);
  align-items: baseline;
}
.cz-disclosure-row:last-child {
  border-bottom: 0;
}
@media (max-width: 520px) {
  .cz-disclosure-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
  }
}
.cz-disclosure-key {
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-disclosure-val {
  color: var(--ui-text-secondary);
  line-height: 1.5;
}
.cz-kits-controls {
  display: grid;
  gap: 10px;
  margin: 12px 0;
}
.cz-kits-search-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cz-kits-search-row input {
  flex: 1 1 220px;
}
.cz-chip-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cz-chip {
  appearance: none;
  border: 1px solid var(--ui-stroke-secondary);
  background-color: var(--ui-bg-tertiary);
  color: var(--ui-text-secondary);
  border-radius: 999px;
  padding: 3px 10px;
  font-family: inherit;
  font-size: 0.72rem;
  font-weight: 500;
  cursor: pointer;
}
.cz-chip:hover {
  background-color: var(--ui-row-hover-background);
  color: var(--ui-text-primary);
}
.cz-chip[aria-pressed="true"] {
  background-color: var(--cz-write-bg);
  border-color: var(--cz-write-border);
  color: var(--cz-write-ink);
  font-weight: 650;
}
.cz-kits-count {
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
}
.cz-kit-list {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}
.cz-kit-item {
  border: 1px solid var(--ui-stroke-secondary);
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-editor);
  overflow: hidden;
}
.cz-kit-item[data-open="true"] {
  border-color: var(--cz-write-border);
}
.cz-kit-row-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px 14px;
  padding: 11px 12px;
}
.cz-kit-row-main {
  flex: 1 1 220px;
  min-width: 0;
}
.cz-kit-unavailable, .cz-kit-unavailable .cz-kit-id, .cz-kit-unavailable .cz-kit-name {
  color: var(--ui-text-tertiary);
}
.cz-kit-unavailable {
  background-color: var(--ui-bg-editor);
  opacity: 0.6;
}
.cz-kit-id-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.cz-kit-id {
  font-family: var(--dt-font-mono);
  font-size: 0.8125rem;
  font-weight: 650;
  color: var(--ui-text-primary);
}
.cz-kit-name {
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-tertiary);
}
.cz-kit-summary {
  margin: 0 0 6px;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  line-height: 1.45;
}
.cz-kit-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.cz-kit-tag {
  font-size: 0.675rem;
  padding: 1px 6px;
  border-radius: 4px;
  background-color: var(--ui-bg-secondary);
  color: var(--ui-text-tertiary);
  border: 1px solid var(--ui-stroke-tertiary);
}
.cz-kit-preview-panel {
  border-top: 1px solid var(--ui-stroke-tertiary);
  background-color: var(--ui-bg-tertiary);
  padding: 12px;
}
.cz-kit-preview-top {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
.cz-kit-preview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 10px;
}
@media (max-width: 660px) {
  .cz-kit-preview-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.cz-tree-box {
  margin: 0;
  padding: 10px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-editor);
  border: 1px solid var(--ui-stroke-secondary);
  max-height: 240px;
  overflow: auto;
  font-size: 0.74rem;
  line-height: 1.45;
  color: var(--ui-text-primary);
}
.cz-dir-list {
  margin: 0;
  padding: 10px 10px 10px 24px;
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-editor);
  border: 1px solid var(--ui-stroke-secondary);
  max-height: 240px;
  overflow: auto;
  font-size: 0.74rem;
  line-height: 1.45;
  color: var(--ui-text-secondary);
}
.cz-dir-list li {
  margin-bottom: 4px;
}
.cz-preview-col-label {
  font-size: 0.72rem;
  font-weight: 650;
  color: var(--ui-text-primary);
  margin: 0 0 6px;
}
.cz-faq-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}
.cz-faq-item {
  border: 1px solid var(--ui-stroke-secondary);
  border-radius: var(--radius-sm);
  background-color: var(--ui-bg-tertiary);
}
.cz-faq-item summary {
  cursor: pointer;
  padding: 10px 12px;
  font-size: var(--conversation-text-font-size);
  font-weight: 600;
  color: var(--ui-text-primary);
  list-style-position: inside;
}
.cz-faq-item summary:hover {
  background-color: var(--ui-row-hover-background);
}
.cz-faq-body {
  padding: 0 12px 12px;
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
  line-height: 1.55;
}
.cz-report-box {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--ui-stroke-secondary);
  background-color: var(--ui-bg-secondary);
  font-size: var(--conversation-caption-font-size);
  color: var(--ui-text-secondary);
}
.cz-report-box strong {
  color: var(--ui-text-primary);
}
.cz-report-box a {
  color: var(--ui-accent);
  text-decoration: underline;
}
.cz-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
`

// host.request and ctx.rest already resolve the active connection per call, so
// no id is passed anywhere. The id is read only to notice a connection switch
// between loading and saving settings. The SDK documents no accessor for the
// active connection id (host.profileRoutes() lists every source), so this reads
// the host.state.connectionId atom when present and falls back to profile-only
// change detection when it is absent or not callable.
export function activeConnectionScope() {
  const atom = host.state?.connectionId
  if (!atom || typeof atom.get !== 'function') return null
  try {
    const value = atom.get()
    return typeof value === 'string' ? value : null
  } catch {
    return null
  }
}

export function createScopedRest(ctx) {
  const scope = () => JSON.stringify([activeConnectionScope(), host.state?.profile?.get?.() ?? null])
  const owner = scope()
  const check = () => {
    if (scope() !== owner) {
      throw new Error('Connection or profile changed. Reopen plugin settings before continuing.')
    }
  }
  const rest = async (path, options) => {
    check()
    const result = await ctx.rest(path, options)
    check()
    return result
  }
  rest.check = check
  rest.owner = owner
  return rest
}

async function callGateway(method, params) {
  if (typeof host.request === 'function') {
    return host.request(method, params)
  }
  throw new Error('Hermes gateway is unavailable.')
}

// Same wording as settings_rule.py. The server (POST /settings/validate) is the
// rule; this mirror only catches the obvious cases before a round trip.
const LIB_MSG = {
  nul: 'Invalid path: remove NUL characters and line breaks.',
  abs: 'Enter an absolute path starting with /, such as /home/user/components.',
  dotdot: 'Remove ".." segments: enter the full path of the folder.',
  root: 'Refused: pick a dedicated folder, not the filesystem root (/).',
  home: 'Refused: pick a dedicated folder inside your home folder, not the home folder itself.',
  system: 'Refused: pick a dedicated folder, not a system folder such as /etc or /usr.',
  hermes: 'Refused: pick a dedicated folder outside Hermes home (such as ~/.hermes).'
}
const SYSTEM_SUBTREE_ROOTS = ['/etc', '/usr', '/bin', '/sbin', '/boot', '/dev', '/proc', '/sys']
const SYSTEM_EXACT_ROOTS = ['/var', '/opt', '/home']
const MAX_TIMEOUT_SECONDS = 86400
const MAX_TIMEOUT_TEXT = 1024
const TIMEOUT_DECIMAL_RE = /^\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,6})?$/

function isSystemFolder(p) {
  const trimmed = p.length > 1 ? p.replace(/\/+$/, '') : p
  if (SYSTEM_EXACT_ROOTS.includes(trimmed)) return true
  if (/^\/lib[^/]*$/.test(trimmed)) return true
  return SYSTEM_SUBTREE_ROOTS.some(root => trimmed === root || trimmed.startsWith(root + '/'))
}

export function validateSettingsDraft(libraryRoot, timeoutRaw) {
  const rawLib = String(libraryRoot ?? '').trim()
  const rawTime = String(timeoutRaw ?? '').replace(/^[ \t]+|[ \t]+$/g, '')
  let libErr = ''
  let libWarn = ''
  let timeErr = ''
  let timeWarn = ''

  if (rawLib !== '') {
    if (/[\0\r\n]/.test(rawLib)) {
      libErr = LIB_MSG.nul
    } else if (rawLib.split('/').includes('..')) {
      libErr = LIB_MSG.dotdot
    } else if (rawLib === '~') {
      libErr = LIB_MSG.home
    } else if (!rawLib.startsWith('/') && !rawLib.startsWith('~/')) {
      libErr = LIB_MSG.abs
    } else if (/^\/+$/.test(rawLib)) {
      libErr = LIB_MSG.root
    } else if (rawLib.includes('//') || /(?:^|\/)\.(?:\/|$)/.test(rawLib)) {
      libErr = 'Remove "." segments and repeated slashes so the path is normalized.'
    } else if (isSystemFolder(rawLib)) {
      libErr = LIB_MSG.system
    } else if (/(?:^|\/)\.hermes(?:\/|$)/i.test(rawLib)) {
      libErr = LIB_MSG.hermes
    } else {
      libWarn = `If this folder has not been initialized yet, run "hermes componentize init ${rawLib}".`
    }
  }

  // Same rule as settings_rule.check_timeout: a plain decimal number only
  // (no sign but '+', no hex/binary, no '_', no NaN/Infinity), > 0, <= 86400.
  const plain = rawTime.length <= MAX_TIMEOUT_TEXT && TIMEOUT_DECIMAL_RE.test(rawTime)
  const num = plain ? Number(rawTime) : NaN
  if (!plain || Number.isNaN(num) || num <= 0) {
    timeErr = 'Invalid timeout: enter a finite number greater than 0.'
  } else if (!Number.isFinite(num) || num > MAX_TIMEOUT_SECONDS) {
    timeErr = 'Invalid timeout: enter at most 86400 seconds (24 hours).'
  } else if (num < 60 || num > 3600) {
    timeWarn = 'Advisory: values between 60 and 3600 seconds are recommended (default 900).'
  }

  return {
    valid: !libErr && !timeErr,
    libErr,
    libWarn,
    timeErr,
    timeWarn,
    normalizedLibraryRoot: rawLib,
    normalizedTimeout: !timeErr ? num : DEFAULT_TIMEOUT_SECONDS
  }
}

export function kitMatchesFilter(kit, groupId, query) {
  if (groupId && groupId !== 'all') {
    const group = KIT_FILTER_GROUPS.find(item => item.id === groupId)
    if (group && group.matchTags.length > 0) {
      const tags = Array.isArray(kit.tags) ? kit.tags : []
      if (!group.matchTags.some(tag => tags.includes(tag))) {
        return false
      }
    }
  }
  const q = String(query ?? '').trim().toLowerCase()
  if (!q) {
    return true
  }
  const haystack = [kit.id, kit.name, kit.summary, ...(kit.tags || [])].join(' ').toLowerCase()
  return haystack.includes(q)
}

function isTimeoutError(err) {
  const msg = String(err?.message ?? err ?? '')
  return err?.name === 'TimeoutError' || /timed?\s*out|deadline|408|504/i.test(msg)
}

function isNotFoundError(err) {
  const status = err?.status ?? err?.statusCode ?? err?.response?.status
  const msg = String(err?.message ?? '')
  return status === 404 || /\b404\b|not found|not enabled/i.test(msg)
}

export function createInitialState() {
  return {
    phase: 'loading',
    saving: false,
    profile: String(host.state?.profile?.get?.() || 'default'),
    pluginVersion: PLUGIN_VERSION,
    checkerVersion: DEFAULT_CHECKER_VERSION,
    kitsCount: BUNDLED_KITS_FALLBACK.length,
    defuzzStatus: 'not_installed',
    backendInactive: false,
    savedSettings: {
      library_root: '',
      timeout: String(DEFAULT_TIMEOUT_SECONDS)
    },
    draft: {
      library_root: '',
      timeout: String(DEFAULT_TIMEOUT_SECONDS)
    },
    library: {
      configured: false,
      ok: false,
      state: 'not_set',
      count: null,
      index_rel: null,
      message: 'No component library folder is configured.'
    },
    errorBanner: '',
    serverErrors: { library_root: '', timeout: '' },
    saveFeedback: '',
    announcement: '',
    kits: BUNDLED_KITS_FALLBACK,
    kitSearch: '',
    activeKitGroup: 'all',
    openKitId: 'web-app',
    kitPreviews: {},
    kitPreviewLoading: '',
    kitPreviewError: '',
    copiedKey: ''
  }
}

export function createSettingsPageModel(ctx, onChange = () => {}) {
  const scopedRest = createScopedRest(ctx)
  let state = createInitialState()
  let disposed = false
  let generation = 0
  let copyTimerDispose = null

  const emit = patch => {
    if (disposed) return
    state = { ...state, ...patch }
    onChange(state)
  }

  const dispose = () => {
    disposed = true
    generation += 1
    if (typeof copyTimerDispose === 'function') {
      copyTimerDispose()
      copyTimerDispose = null
    }
  }
  ctx?.onDispose?.(dispose)

  async function loadKitPreview(kitId) {
    if (!kitId || disposed || state.kitPreviews[kitId]) {
      return
    }
    const gen = generation
    emit({ kitPreviewLoading: kitId, kitPreviewError: '' })
    try {
      const detail = await scopedRest(`/kits/${encodeURIComponent(kitId)}`, { timeoutMs: 10000 })
      if (disposed || gen !== generation) return
      if (detail && detail.kit && Array.isArray(detail.tree)) {
        emit({
          kitPreviewLoading: '',
          kitPreviews: { ...state.kitPreviews, [kitId]: detail }
        })
      } else {
        emit({ kitPreviewLoading: '', kitPreviewError: 'Project kit preview is unavailable.' })
      }
    } catch (err) {
      if (disposed || gen !== generation) return
      if (/Connection or profile changed/.test(String(err?.message ?? ''))) {
        emit({ kitPreviewLoading: '', errorBanner: err.message })
        return
      }
      emit({
        kitPreviewLoading: '',
        kitPreviewError: isNotFoundError(err)
          ? 'Enable the Python plugin half (hermes plugins enable defuzz-componentize) to load full folder trees.'
          : 'Project kit preview could not be loaded.'
      })
    }
  }

  async function load() {
    if (disposed) return state
    const gen = ++generation
    emit({ phase: 'loading', errorBanner: '', saveFeedback: '' })

    try {
      scopedRest.check()
    } catch (err) {
      emit({ phase: 'error', errorBanner: err.message })
      return state
    }

    let rpcLibraryRoot = null
    let rpcTimeout = null
    let rpcVersion = PLUGIN_VERSION
    let defuzzStatus = 'not_installed'

    try {
      const listRes = await callGateway('plugins.manage', { action: 'list' })
      scopedRest.check()
      const rows = Array.isArray(listRes?.plugins) ? listRes.plugins : []
      const selfRow = rows.find(row => row?.key === PLUGIN_ID || row?.name === PLUGIN_ID)
      if (selfRow) {
        if (selfRow.version) rpcVersion = String(selfRow.version)
        const fields = Array.isArray(selfRow.settings_schema) ? selfRow.settings_schema : []
        const libField = fields.find(f => f?.key === 'library_root')
        const timeField = fields.find(f => f?.key === 'timeout')
        if (libField && typeof libField.value === 'string') {
          rpcLibraryRoot = libField.value
        }
        if (timeField && (typeof timeField.value === 'number' || typeof timeField.value === 'string')) {
          rpcTimeout = String(timeField.value)
        }
      }
      const defuzzRow = rows.find(row => row?.key === 'defuzz' || row?.name === 'defuzz')
      if (defuzzRow) {
        defuzzStatus = defuzzRow.status === 'enabled' ? 'enabled' : 'disabled'
      }
    } catch (err) {
      if (disposed || gen !== generation) return state
      if (/Connection or profile changed/.test(String(err?.message ?? ''))) {
        emit({ phase: 'error', errorBanner: err.message })
        return state
      }
    }

    let statusPayload = null
    let timedOut = false
    let backendInactive = false
    let statusError = ''

    try {
      statusPayload = await scopedRest('/status', { timeoutMs: 10000 })
    } catch (err) {
      if (disposed || gen !== generation) return state
      if (/Connection or profile changed/.test(String(err?.message ?? ''))) {
        emit({ phase: 'error', errorBanner: err.message })
        return state
      }
      if (isTimeoutError(err)) {
        timedOut = true
      } else if (isNotFoundError(err)) {
        backendInactive = true
      } else {
        statusError = 'Componentize status route could not be reached.'
      }
    }

    let kitsList = state.kits
    if (!backendInactive && !timedOut) {
      try {
        const kitsRes = await scopedRest('/kits', { timeoutMs: 10000 })
        if (Array.isArray(kitsRes?.kits) && kitsRes.kits.length > 0) {
          kitsList = kitsRes.kits
        }
      } catch (err) {
        if (disposed || gen !== generation) return state
        if (/Connection or profile changed/.test(String(err?.message ?? ''))) {
          emit({ phase: 'error', errorBanner: err.message })
          return state
        }
      }
    }

    const resolvedLibRoot =
      rpcLibraryRoot !== null
        ? rpcLibraryRoot
        : typeof statusPayload?.settings?.library_root === 'string'
          ? statusPayload.settings.library_root
          : ''
    const resolvedTimeout =
      rpcTimeout !== null
        ? rpcTimeout
        : statusPayload?.settings?.timeout !== undefined
          ? String(statusPayload.settings.timeout)
          : String(DEFAULT_TIMEOUT_SECONDS)

    const saved = {
      library_root: resolvedLibRoot.trim(),
      timeout: resolvedTimeout.trim() || String(DEFAULT_TIMEOUT_SECONDS)
    }

    if (timedOut) {
      emit({
        phase: 'timeout',
        profile: String(host.state?.profile?.get?.() || 'default'),
        pluginVersion: rpcVersion,
        defuzzStatus,
        backendInactive: false,
        savedSettings: saved,
        draft: { ...saved },
        kits: kitsList,
        library: {
          configured: Boolean(saved.library_root),
          ok: false,
          state: 'timeout',
          count: null,
          index_rel: null,
          message: 'Status probe timed out before finishing.'
        }
      })
      return state
    }

    const libBlock = statusPayload?.library ?? {
      configured: Boolean(saved.library_root),
      ok: false,
      state: saved.library_root ? 'unknown' : 'not_set',
      count: null,
      index_rel: null,
      message: backendInactive
        ? 'Python status route is inactive until defuzz-componentize is enabled.'
        : 'No component library folder is configured.'
    }

    let nextPhase = 'configured'
    if (!saved.library_root || libBlock.state === 'not_set') {
      nextPhase = 'empty'
    } else if (!backendInactive && !libBlock.ok) {
      nextPhase = libBlock.state === 'missing' || libBlock.state === 'missing_index' ? 'missing-folder' : 'library-error'
    }

    emit({
      phase: nextPhase,
      profile: String(host.state?.profile?.get?.() || 'default'),
      pluginVersion: String(statusPayload?.version || rpcVersion || PLUGIN_VERSION),
      checkerVersion: String(statusPayload?.checker_version || DEFAULT_CHECKER_VERSION),
      kitsCount: Number(statusPayload?.kits_count || kitsList.length || BUNDLED_KITS_FALLBACK.length),
      defuzzStatus,
      backendInactive,
      errorBanner: statusError,
      savedSettings: saved,
      draft: { ...saved },
      kits: kitsList,
      library: libBlock
    })

    if (!backendInactive && state.openKitId) {
      await loadKitPreview(state.openKitId)
    }
    return state
  }

  function editDraft(patch) {
    emit({
      draft: { ...state.draft, ...patch },
      serverErrors: { library_root: '', timeout: '' },
      saveFeedback: '',
      errorBanner: ''
    })
  }

  function discardDraft() {
    emit({
      draft: { ...state.savedSettings },
      saveFeedback: 'Draft changes discarded.',
      announcement: 'Draft changes discarded.',
      errorBanner: ''
    })
  }

  async function save() {
    if (disposed || state.saving || state.phase === 'loading') return false
    const check = validateSettingsDraft(state.draft.library_root, state.draft.timeout)
    if (!check.valid) {
      emit({ announcement: 'Fix validation errors before saving.' })
      return false
    }
    try {
      scopedRest.check()
    } catch (err) {
      emit({ errorBanner: err.message })
      return false
    }

    emit({ saving: true, saveFeedback: '', errorBanner: '' })
    // The plugin backend applies the CLI's folder rule; Hermes saves only what it accepted.
    let verdict = null
    try {
      verdict = await scopedRest('/settings/validate', {
        method: 'POST',
        body: { library_root: check.normalizedLibraryRoot, timeout: check.normalizedTimeout },
        timeoutMs: 10000
      })
    } catch (err) {
      if (disposed) return false
      emit({
        saving: false,
        errorBanner: /Connection or profile changed/.test(String(err?.message ?? ''))
          ? err.message
          : 'Could not check these settings with the plugin backend, so nothing was saved. Enable the plugin backend and try again.'
      })
      return false
    }
    if (disposed) return false
    if (!verdict || verdict.ok !== true || !verdict.values || typeof verdict.values.library_root !== 'string') {
      const errors = verdict && typeof verdict.errors === 'object' && verdict.errors ? verdict.errors : {}
      emit({
        saving: false,
        serverErrors: {
          library_root: typeof errors.library_root === 'string' ? errors.library_root : '',
          timeout: typeof errors.timeout === 'string' ? errors.timeout : ''
        },
        errorBanner: errors.library_root || errors.timeout ? '' : 'The plugin backend refused these settings, so nothing was saved.',
        announcement: 'Fix validation errors before saving.'
      })
      return false
    }
    const accepted = { library_root: verdict.values.library_root, timeout: Number(verdict.values.timeout) }
    try {
      await callGateway('plugins.manage', {
        action: 'settings',
        key: PLUGIN_ID,
        values: accepted
      })
      scopedRest.check()
      const nextSaved = {
        library_root: accepted.library_root,
        timeout: String(accepted.timeout)
      }
      let nextLibrary = state.library
      let nextPhase = nextSaved.library_root ? 'configured' : 'empty'
      if (!state.backendInactive) {
        try {
          const freshStatus = await scopedRest('/status', { timeoutMs: 10000 })
          if (freshStatus?.library) {
            nextLibrary = freshStatus.library
            if (!nextSaved.library_root || nextLibrary.state === 'not_set') {
              nextPhase = 'empty'
            } else if (!nextLibrary.ok) {
              nextPhase =
                nextLibrary.state === 'missing' || nextLibrary.state === 'missing_index'
                  ? 'missing-folder'
                  : 'library-error'
            } else {
              nextPhase = 'configured'
            }
          }
        } catch {
          // Saved via gateway; status refresh is best-effort.
        }
      }
      emit({
        saving: false,
        phase: nextPhase,
        savedSettings: nextSaved,
        draft: { ...nextSaved },
        library: nextLibrary,
        saveFeedback: 'Saved to plugins.entries.defuzz-componentize.settings.',
        announcement: 'Settings saved.'
      })
      return true
    } catch (err) {
      if (disposed) return false
      emit({
        saving: false,
        errorBanner: /Connection or profile changed/.test(String(err?.message ?? ''))
          ? err.message
          : 'Could not save plugin settings through Hermes gateway.'
      })
      return false
    }
  }

  function setKitSearch(value) {
    emit({ kitSearch: String(value ?? '') })
  }

  function setKitGroup(groupId) {
    emit({ activeKitGroup: groupId })
  }

  async function toggleKit(kitId) {
    if (state.kits.find(kit => kit.id === kitId)?.availability === 'coming_soon') return
    const nextOpen = state.openKitId === kitId ? '' : kitId
    emit({ openKitId: nextOpen, kitPreviewError: '' })
    if (nextOpen && !state.backendInactive) {
      await loadKitPreview(nextOpen)
    }
  }

  async function copyCommand(key, text) {
    try {
      const ok = await ctx?.os?.writeClipboard?.(text)
      if (!ok && typeof writeClipboardText === 'function') {
        await writeClipboardText(text)
      }
    } catch {
      // Clipboard feedback still announces the text in non-Electron test hosts.
    }
    emit({
      copiedKey: key,
      announcement: `Copied command to clipboard: ${text}`
    })
    if (typeof copyTimerDispose === 'function') {
      copyTimerDispose()
      copyTimerDispose = null
    }
    if (typeof ctx?.setTimeout === 'function') {
      copyTimerDispose = ctx.setTimeout(() => {
        copyTimerDispose = null
        if (!disposed && state.copiedKey === key) {
          emit({ copiedKey: '' })
        }
      }, 1400)
    }
  }

  void Button
  void Input
  void Badge
  void Separator
  void Codicon

  return {
    get: () => state,
    load,
    loadKitPreview,
    editDraft,
    discardDraft,
    save,
    setKitSearch,
    setKitGroup,
    toggleKit,
    copyCommand,
    dispose
  }
}

function FlowchartKitsSvg() {
  return jsxs('svg', {
    className: 'cz-flowchart-svg',
    viewBox: '0 0 680 668',
    role: 'img',
    'aria-labelledby': 'cz-flow-kits-title cz-flow-kits-desc',
    children: [
      jsx('title', { id: 'cz-flow-kits-title', children: 'Starting a project from a kit' }),
      jsx('desc', {
        id: 'cz-flow-kits-desc',
        children:
          'Six-step flowchart from choosing a project folder through kit list, kit show, folder safety check, kit apply, and kit check. Steps 1, 2, 3, 4, and 6 only read data; only step 5 writes, and only inside the folder you name.'
      }),
      jsxs('defs', {
        children: [
          jsx('marker', {
            id: 'marker-kit-arrow',
            viewBox: '0 0 10 10',
            refX: '8.5',
            refY: '5',
            markerWidth: '7',
            markerHeight: '7',
            orient: 'auto-start-reverse',
            children: jsx('path', { d: 'M 1 1.5 L 8.5 5 L 1 8.5 z', fill: 'var(--cz-stroke-strong)' })
          }),
          jsx('marker', {
            id: 'marker-kit-stop',
            viewBox: '0 0 10 10',
            refX: '8.5',
            refY: '5',
            markerWidth: '7',
            markerHeight: '7',
            orient: 'auto-start-reverse',
            children: jsx('path', { d: 'M 1 1.5 L 8.5 5 L 1 8.5 z', fill: 'var(--cz-danger-border)' })
          })
        ]
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '12',
            width: '640',
            height: '44',
            rx: '6',
            fill: 'var(--ui-bg-tertiary)',
            stroke: 'var(--ui-stroke-tertiary)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '32', y: '38', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: 'LEGEND:' }),
          jsx('rect', {
            x: '94',
            y: '22',
            width: '20',
            height: '22',
            rx: '5',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('text', { x: '122', y: '37', className: 'cz-fc-sub', children: 'Rounded solid = Reads only' }),
          jsx('polygon', {
            points: '304,22 318,22 324,28 324,44 304,44',
            fill: 'var(--cz-write-bg)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '2',
            strokeDasharray: '5 2.5'
          }),
          jsx('text', { x: '332', y: '37', className: 'cz-fc-sub', children: 'Cut-corner dashed = Writes in your folder' }),
          jsx('polygon', {
            points: '598,22 609,33 598,44 587,33',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('text', { x: '615', y: '37', className: 'cz-fc-sub', children: 'Check' })
        ]
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '72',
            width: '640',
            height: '68',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '81',
            width: '148',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '93.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '1 · READ ONLY · IDEA' }),
          jsx('text', { x: '194', y: '94.5', className: 'cz-fc-title', children: 'Pick what you are building and a folder path' }),
          jsx('text', { x: '34', y: '116', className: 'cz-fc-sub', children: 'Example target: /home/user/projects/short-film (new or existing folder).' }),
          jsx('text', { x: '34', y: '131', className: 'cz-fc-note', children: 'No command has run yet; nothing on disk is changed.' })
        ]
      }),
      jsx('line', {
        x1: '244',
        y1: '140',
        x2: '244',
        y2: '160',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '162',
            width: '640',
            height: '68',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '171',
            width: '112',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '183.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '2 · READ ONLY' }),
          jsx('text', { x: '158', y: '184.5', className: 'cz-fc-cmd', children: 'hermes componentize kit list' }),
          jsx('text', { x: '34', y: '206', className: 'cz-fc-sub', children: 'Reads the 14 kit JSON files shipped inside the plugin folder.' }),
          jsx('text', { x: '34', y: '221', className: 'cz-fc-note', children: 'Writes nothing. Does not need a component library to be set up.' })
        ]
      }),
      jsx('line', {
        x1: '244',
        y1: '230',
        x2: '244',
        y2: '250',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '252',
            width: '640',
            height: '68',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '261',
            width: '112',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '273.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '3 · READ ONLY' }),
          jsx('text', { x: '158', y: '274.5', className: 'cz-fc-cmd', children: 'hermes componentize kit show <id>' }),
          jsx('text', { x: '34', y: '296', className: 'cz-fc-sub', children: 'Reads vendor/kits/<id>/kit.json and prints its folder tree and rules.' }),
          jsx('text', { x: '34', y: '311', className: 'cz-fc-note', children: 'Preview the exact folders and AGENTS.md block before anything is created.' })
        ]
      }),
      jsx('line', {
        x1: '244',
        y1: '320',
        x2: '244',
        y2: '340',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '36,342 454,342 470,380 454,418 36,418 20,380',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.75'
          }),
          jsx('rect', {
            x: '44',
            y: '351',
            width: '144',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '52', y: '363.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '4 · CHECK · READ ONLY' }),
          jsx('text', { x: '198', y: '364.5', className: 'cz-fc-title', children: 'Is <folder> a safe directory?' }),
          jsx('text', { x: '44', y: '386', className: 'cz-fc-sub', children: 'Refuses unsafe folders (/, ~, parents of ~), symlinks, or plain files.' }),
          jsx('text', { x: '44', y: '403', className: 'cz-fc-note', children: 'Also refuses broken <!-- kit:<id> --> markers in AGENTS.md.' })
        ]
      }),
      jsx('line', {
        x1: '470',
        y1: '380',
        x2: '494',
        y2: '380',
        stroke: 'var(--cz-danger-border)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-stop)'
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '496',
            y: '348',
            width: '164',
            height: '64',
            rx: '6',
            fill: 'var(--cz-danger-bg)',
            stroke: 'var(--cz-danger-border)',
            strokeWidth: '1.5',
            strokeDasharray: '3 2'
          }),
          jsx('text', { x: '506', y: '367', className: 'cz-fc-tag', fill: 'var(--cz-danger-ink)', children: 'IF UNSAFE (EXIT 1 OR 2)' }),
          jsx('text', { x: '506', y: '384', className: 'cz-fc-sub', children: 'Stops with fix hint.' }),
          jsx('text', { x: '506', y: '400', className: 'cz-fc-sub', children: 'Nothing overwritten.' })
        ]
      }),
      jsx('line', {
        x1: '244',
        y1: '418',
        x2: '244',
        y2: '442',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-arrow)'
      }),
      jsx('text', { x: '254', y: '434', className: 'cz-fc-edge', children: 'Safe path' }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '20,444 642,444 660,462 660,548 20,548',
            fill: 'var(--cz-write-bg)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '2',
            strokeDasharray: '6 3'
          }),
          jsx('polyline', {
            points: '642,444 642,462 660,462',
            fill: 'none',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '454',
            width: '192',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '466.5', className: 'cz-fc-tag', fill: 'var(--cz-write-ink)', children: '5 · WRITES ONLY IN <FOLDER>' }),
          jsx('text', { x: '236', y: '467.5', className: 'cz-fc-cmd', children: 'hermes componentize kit apply <id> <folder>' }),
          jsx('text', { x: '34', y: '490', className: 'cz-fc-sub', children: 'Creates missing folders and starter files only; existing starter files are kept.' }),
          jsx('text', { x: '34', y: '508', className: 'cz-fc-sub', children: 'Edits only <!-- kit:<id> ... --> in AGENTS.md; appends .gitignore / .gitattributes lines.' }),
          jsx('text', { x: '34', y: '528', className: 'cz-fc-note', children: 'Never touched: other existing files, text outside kit markers, other folders, or config.yaml.' })
        ]
      }),
      jsx('line', {
        x1: '244',
        y1: '548',
        x2: '244',
        y2: '570',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-kit-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '572',
            width: '640',
            height: '76',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '582',
            width: '112',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '594.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '6 · READ ONLY' }),
          jsx('text', { x: '158', y: '595.5', className: 'cz-fc-cmd', children: 'hermes componentize kit check <id> <folder>' }),
          jsx('text', { x: '34', y: '617', className: 'cz-fc-sub', children: 'Checks that every expected folder, starter file, AGENTS.md block, and ignore rule exists.' }),
          jsx('text', { x: '34', y: '635', className: 'cz-fc-note', children: 'Exit 0 = PASS (all pieces present) · Exit 1 = FAIL (lists missing items; re-run apply).' })
        ]
      })
    ]
  })
}

function FlowchartComponentizeSvg() {
  return jsxs('svg', {
    className: 'cz-flowchart-svg',
    viewBox: '0 0 720 896',
    role: 'img',
    'aria-labelledby': 'cz-flow-comp-title cz-flow-comp-desc',
    children: [
      jsx('title', { id: 'cz-flow-comp-title', children: 'Turning code into a component' }),
      jsx('desc', {
        id: 'cz-flow-comp-desc',
        children:
          'Nine-node receipt-gated flowchart: 1 survey (read-only), 2 begin (runs golden twice and writes baseline), 3 attest (writes notes), 4 check (runs your golden and test commands and writes check state), 5 pass or fail check decision, 6 rebaseline when output changed on purpose, 7 finish (writes receipt in .componentize), 8 status (read-only verification), and 9 OK or stale receipt check.'
      }),
      jsxs('defs', {
        children: [
          jsx('marker', {
            id: 'marker-comp-arrow',
            viewBox: '0 0 10 10',
            refX: '8.5',
            refY: '5',
            markerWidth: '7',
            markerHeight: '7',
            orient: 'auto-start-reverse',
            children: jsx('path', { d: 'M 1 1.5 L 8.5 5 L 1 8.5 z', fill: 'var(--cz-stroke-strong)' })
          }),
          jsx('marker', {
            id: 'marker-comp-write',
            viewBox: '0 0 10 10',
            refX: '8.5',
            refY: '5',
            markerWidth: '7',
            markerHeight: '7',
            orient: 'auto-start-reverse',
            children: jsx('path', { d: 'M 1 1.5 L 8.5 5 L 1 8.5 z', fill: 'var(--cz-write-border)' })
          }),
          jsx('marker', {
            id: 'marker-comp-exec',
            viewBox: '0 0 10 10',
            refX: '8.5',
            refY: '5',
            markerWidth: '7',
            markerHeight: '7',
            orient: 'auto-start-reverse',
            children: jsx('path', { d: 'M 1 1.5 L 8.5 5 L 1 8.5 z', fill: 'var(--cz-warn-border)' })
          })
        ]
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '12',
            width: '680',
            height: '48',
            rx: '6',
            fill: 'var(--ui-bg-tertiary)',
            stroke: 'var(--ui-stroke-tertiary)',
            strokeWidth: '1'
          }),
          jsx('rect', {
            x: '32',
            y: '24',
            width: '20',
            height: '22',
            rx: '5',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('text', { x: '60', y: '39', className: 'cz-fc-sub', children: 'Reads only' }),
          jsx('polygon', {
            points: '154,24 168,24 174,30 174,46 154,46',
            fill: 'var(--cz-write-bg)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '2',
            strokeDasharray: '5 2.5'
          }),
          jsx('text', { x: '182', y: '39', className: 'cz-fc-sub', children: 'Writes in .componentize/' }),
          jsx('polygon', {
            points: '366,24 384,24 390,35 384,46 366,46 360,35',
            fill: 'var(--cz-warn-bg)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '2'
          }),
          jsx('text', { x: '398', y: '39', className: 'cz-fc-sub', children: 'Runs your commands + writes' }),
          jsx('polygon', {
            points: '612,24 623,35 612,46 601,35',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('text', { x: '630', y: '39', className: 'cz-fc-sub', children: 'Decision' })
        ]
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '76',
            width: '480',
            height: '68',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '85',
            width: '108',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '97.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '1 · READ ONLY' }),
          jsx('text', { x: '154', y: '98.5', className: 'cz-fc-cmd', children: 'survey <path>' }),
          jsx('text', { x: '34', y: '119', className: 'cz-fc-sub', children: 'Parses .py files as text without importing or running them.' }),
          jsx('text', { x: '34', y: '134', className: 'cz-fc-note', children: 'Lists candidate splits for review. Writes nothing to .componentize/.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '144',
        x2: '260',
        y2: '164',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '34,166 486,166 500,205 486,244 34,244 20,205',
            fill: 'var(--cz-warn-bg)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '2'
          }),
          jsx('rect', {
            x: '42',
            y: '175',
            width: '186',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '50', y: '187.5', className: 'cz-fc-tag', fill: 'var(--cz-warn-ink)', children: '2 · RUNS COMMANDS + WRITES' }),
          jsx('text', { x: '238', y: '188.5', className: 'cz-fc-cmd', children: 'begin <name> --manifest' }),
          jsx('text', { x: '42', y: '210', className: 'cz-fc-sub', children: 'Runs your golden command twice (unless waived) to check repeatability.' }),
          jsx('text', { x: '42', y: '228', className: 'cz-fc-note', children: 'Saves manifest gen-1, baseline gen-1, and attest.json in .componentize/<name>/.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '244',
        x2: '260',
        y2: '264',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '20,266 484,266 500,282 500,338 20,338',
            fill: 'var(--cz-write-bg)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '2',
            strokeDasharray: '6 3'
          }),
          jsx('polyline', {
            points: '484,266 484,282 500,282',
            fill: 'none',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '275',
            width: '186',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-write-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '287.5', className: 'cz-fc-tag', fill: 'var(--cz-write-ink)', children: '3 · WRITES IN .COMPONENTIZE' }),
          jsx('text', { x: '230', y: '288.5', className: 'cz-fc-cmd', children: 'attest <name> plan | review' }),
          jsx('text', { x: '34', y: '310', className: 'cz-fc-sub', children: 'Saves plan, review, and optional impl notes (>= 20 chars) in attest.json.' }),
          jsx('text', { x: '34', y: '326', className: 'cz-fc-note', children: 'Deletes any older receipt.json so a changed note requires a fresh finish.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '338',
        x2: '260',
        y2: '360',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '34,362 486,362 500,410 486,458 34,458 20,410',
            fill: 'var(--cz-warn-bg)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '2'
          }),
          jsx('rect', {
            x: '42',
            y: '371',
            width: '186',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '50', y: '383.5', className: 'cz-fc-tag', fill: 'var(--cz-warn-ink)', children: '4 · RUNS COMMANDS + WRITES' }),
          jsx('text', { x: '238', y: '384.5', className: 'cz-fc-cmd', children: 'check <name>' }),
          jsx('text', { x: '42', y: '406', className: 'cz-fc-sub', children: 'Checks file rules; runs golden and tests from spec.json (plus hermes_plugins).' }),
          jsx('text', { x: '42', y: '423', className: 'cz-fc-sub', children: 'Plain authority note: your commands run with your user permissions (no sandbox).' }),
          jsx('text', { x: '42', y: '441', className: 'cz-fc-note', children: 'Deletes old receipt; writes last_check.json in <target>/.componentize/<name>/.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '458',
        x2: '260',
        y2: '480',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '36,482 484,482 500,520 484,558 36,558 20,520',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.75'
          }),
          jsx('rect', {
            x: '44',
            y: '491',
            width: '104',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '52', y: '503.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '5 · DECISION' }),
          jsx('text', { x: '160', y: '504.5', className: 'cz-fc-title', children: 'Did check pass (exit 0)?' }),
          jsx('text', { x: '44', y: '526', className: 'cz-fc-sub', children: 'Exit 0: go to finish · Exit 1 (bug): fix files and run check again.' }),
          jsx('text', { x: '44', y: '543', className: 'cz-fc-note', children: 'Only if output changed on purpose: run rebaseline with a written reason.' })
        ]
      }),
      jsx('path', {
        d: 'M 500 508 L 522 508 L 522 426 L 500 426',
        fill: 'none',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        strokeDasharray: '4 3',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsx('text', { x: '528', y: '472', className: 'cz-fc-edge', children: 'Exit 1: fix files,' }),
      jsx('text', { x: '528', y: '486', className: 'cz-fc-edge', children: 'run check again' }),
      jsx('path', {
        d: 'M 500 536 L 614 536 L 614 406',
        fill: 'none',
        stroke: 'var(--cz-warn-border)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-exec)'
      }),
      jsx('text', { x: '524', y: '552', className: 'cz-fc-edge', children: 'Intentional output change' }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '536,288 694,288 704,346 694,404 536,404 526,346',
            fill: 'var(--cz-warn-bg)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '2'
          }),
          jsx('rect', {
            x: '540',
            y: '298',
            width: '148',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '546', y: '310.5', className: 'cz-fc-tag', fill: 'var(--cz-warn-ink)', children: '6 · RUNS + WRITES' }),
          jsx('text', { x: '540', y: '331', className: 'cz-fc-cmd', children: 'rebaseline <name>' }),
          jsx('text', { x: '540', y: '349', className: 'cz-fc-sub', children: '--reason "<why>"' }),
          jsx('text', { x: '540', y: '366', className: 'cz-fc-note', children: 'Runs golden 2x; saves' }),
          jsx('text', { x: '540', y: '381', className: 'cz-fc-note', children: 'new baseline gen in' }),
          jsx('text', { x: '540', y: '395', className: 'cz-fc-note', children: '.componentize/.' })
        ]
      }),
      jsx('path', {
        d: 'M 526 388 L 500 388',
        fill: 'none',
        stroke: 'var(--cz-warn-border)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-exec)'
      }),
      jsx('line', {
        x1: '260',
        y1: '558',
        x2: '260',
        y2: '584',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsx('text', { x: '270', y: '575', className: 'cz-fc-edge', children: 'Exit 0 (pass)' }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '34,586 486,586 500,626 486,666 34,666 20,626',
            fill: 'var(--cz-warn-bg)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '2'
          }),
          jsx('rect', {
            x: '42',
            y: '595',
            width: '196',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-warn-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '50', y: '607.5', className: 'cz-fc-tag', fill: 'var(--cz-warn-ink)', children: '7 · RUNS + WRITES RECEIPT' }),
          jsx('text', { x: '248', y: '608.5', className: 'cz-fc-cmd', children: 'finish <name>' }),
          jsx('text', { x: '42', y: '630', className: 'cz-fc-sub', children: 'Re-runs check under lock and verifies required plan and review notes.' }),
          jsx('text', { x: '42', y: '648', className: 'cz-fc-note', children: 'Writes bound receipt to <target>/.componentize/<name>/receipt.json.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '666',
        x2: '260',
        y2: '690',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('rect', {
            x: '20',
            y: '692',
            width: '480',
            height: '72',
            rx: '8',
            fill: 'var(--ui-bg-secondary)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1.5'
          }),
          jsx('rect', {
            x: '34',
            y: '701',
            width: '186',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-stroke-strong)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '42', y: '713.5', className: 'cz-fc-tag', fill: 'var(--ui-text-secondary)', children: '8 · READ ONLY · NO EXEC' }),
          jsx('text', { x: '230', y: '714.5', className: 'cz-fc-cmd', children: 'status <name>' }),
          jsx('text', { x: '34', y: '736', className: 'cz-fc-sub', children: 'Re-hashes declared files, inputs, notes, runtime, and checker vs receipt.' }),
          jsx('text', { x: '34', y: '752', className: 'cz-fc-note', children: 'Never runs golden or tests. Acquires only .componentize/<name>/lock.' })
        ]
      }),
      jsx('line', {
        x1: '260',
        y1: '764',
        x2: '260',
        y2: '788',
        stroke: 'var(--cz-stroke-strong)',
        strokeWidth: '1.75',
        markerEnd: 'url(#marker-comp-arrow)'
      }),
      jsxs('g', {
        children: [
          jsx('polygon', {
            points: '36,790 484,790 500,830 484,870 36,870 20,830',
            fill: 'var(--cz-ok-bg)',
            stroke: 'var(--cz-ok-border)',
            strokeWidth: '1.75'
          }),
          jsx('rect', {
            x: '44',
            y: '799',
            width: '130',
            height: '18',
            rx: '4',
            fill: 'var(--ui-bg-editor)',
            stroke: 'var(--cz-ok-border)',
            strokeWidth: '1'
          }),
          jsx('text', { x: '52', y: '811.5', className: 'cz-fc-tag', fill: 'var(--cz-ok-ink)', children: '9 · RECEIPT CHECK' }),
          jsx('text', { x: '186', y: '812.5', className: 'cz-fc-title', children: 'Exit 0 (OK) or Exit 1 (STALE)?' }),
          jsx('text', { x: '44', y: '834', className: 'cz-fc-sub', children: 'Exit 0: OK — every declared byte still matches receipt.json (done).' }),
          jsx('text', { x: '44', y: '852', className: 'cz-fc-note', children: 'Exit 1: STALE — a file, note, or plugin version changed; run finish again.' })
        ]
      }),
      jsx('path', {
        d: 'M 500 830 L 596 830 L 596 626 L 500 626',
        fill: 'none',
        stroke: 'var(--cz-write-border)',
        strokeWidth: '1.75',
        strokeDasharray: '5 3',
        markerEnd: 'url(#marker-comp-write)'
      }),
      jsx('text', { x: '516', y: '724', className: 'cz-fc-edge', children: 'Exit 1: receipt stale' }),
      jsx('text', { x: '516', y: '739', className: 'cz-fc-edge', children: '(run finish again)' })
    ]
  })
}

function renderStatusCells(state) {
  const savedRoot = state.savedSettings.library_root
  const lib = state.library

  let libBadgeClass = 'cz-badge cz-badge-warn'
  let libBadgeText = 'NOT SET YET'
  let libPathText = ''
  let libMeta = 'Run hermes componentize init <folder> when ready.'

  let countText = 'None (no library set)'
  let countMeta = 'Project kits below still work without a library.'

  if (state.phase === 'timeout') {
    libBadgeClass = 'cz-badge cz-badge-err'
    libBadgeText = 'PROBE TIMED OUT'
    libPathText = savedRoot
    libMeta = 'Status check exceeded deadline; saved setting is unchanged.'
    countText = 'Unknown (probe timed out)'
    countMeta = 'No files were modified.'
  } else if (state.backendInactive) {
    libBadgeClass = 'cz-badge cz-badge-warn'
    libBadgeText = savedRoot ? 'SAVED (UNPROBED)' : 'NOT SET YET'
    libPathText = savedRoot
    libMeta = 'Enable the Python plugin half to verify COMPONENTS.md on disk.'
    countText = savedRoot ? 'Unknown (status route inactive)' : 'None (no library set)'
    countMeta = 'Settings below still save to profile config.'
  } else if (!savedRoot || lib.state === 'not_set') {
    libBadgeClass = 'cz-badge cz-badge-warn'
    libBadgeText = 'NOT SET YET'
    libPathText = ''
    libMeta = 'Run hermes componentize init <folder> when ready.'
    countText = 'None (no library set)'
    countMeta = 'Project kits below still work without a library.'
  } else if (lib.ok && lib.state === 'ready') {
    libBadgeClass = 'cz-badge cz-badge-ok'
    libBadgeText = 'READY'
    libPathText = savedRoot
    const idxName = lib.index_rel || 'components/COMPONENTS.md'
    libMeta = `Index verified (${idxName} present).`
    const n = Number(lib.count ?? 0)
    countText = n === 1 ? '1 component' : `${n} components`
    countMeta = `Counted from entries in ${idxName}.`
  } else if (lib.state === 'missing' || lib.state === 'missing_index') {
    libBadgeClass = 'cz-badge cz-badge-err'
    libBadgeText = 'NOT FOUND'
    libPathText = savedRoot
    libMeta = 'Finding kit-missing: folder or COMPONENTS.md not found.'
    countText = 'Unknown (folder missing)'
    countMeta = 'Run init on the folder or update library_root below.'
  } else {
    libBadgeClass = 'cz-badge cz-badge-err'
    libBadgeText = 'CHECK NEEDED'
    libPathText = savedRoot
    libMeta = lib.message || 'Configured folder could not be inspected.'
    countText = 'Unknown'
    countMeta = 'Select a valid dedicated library folder below.'
  }

  let defuzzBadgeClass = 'cz-badge cz-badge-read'
  let defuzzBadgeText = 'NOT INSTALLED'
  let defuzzMeta = 'Optional; all 14 kits work from CLI and /componentize.'
  if (state.defuzzStatus === 'enabled') {
    defuzzBadgeClass = 'cz-badge cz-badge-ok'
    defuzzBadgeText = 'INSTALLED & ENABLED'
    defuzzMeta = 'Defuzz can scaffold new workspaces with these 14 kits.'
  } else if (state.defuzzStatus === 'disabled') {
    defuzzBadgeClass = 'cz-badge cz-badge-warn'
    defuzzBadgeText = 'INSTALLED (DISABLED)'
    defuzzMeta = 'Enable defuzz in Capabilities -> Plugins to scaffold from UI.'
  }

  return jsxs('div', {
    className: 'cz-status-grid',
    'aria-label': 'Plugin status summary',
    children: [
      jsxs('div', {
        className: 'cz-status-cell',
        children: [
          jsx('div', { className: 'cz-status-label', children: 'Library folder' }),
          jsxs('div', {
            className: 'cz-status-value',
            id: 'cz-st-library-val',
            children: [
              jsx('span', { className: libBadgeClass, children: libBadgeText }),
              libPathText ? jsx('span', { className: 'cz-mono', children: libPathText }) : null
            ]
          }),
          jsx('div', { className: 'cz-status-meta', id: 'cz-st-library-meta', children: libMeta })
        ]
      }),
      jsxs('div', {
        className: 'cz-status-cell',
        children: [
          jsx('div', { className: 'cz-status-label', children: 'Components indexed' }),
          jsx('div', { className: 'cz-status-value', id: 'cz-st-count-val', children: countText }),
          jsx('div', { className: 'cz-status-meta', id: 'cz-st-count-meta', children: countMeta })
        ]
      }),
      jsxs('div', {
        className: 'cz-status-cell',
        children: [
          jsx('div', { className: 'cz-status-label', children: 'Kits available' }),
          jsx('div', {
            className: 'cz-status-value',
            children: jsx('span', { className: 'cz-badge cz-badge-ok', children: `${state.kitsCount} BUNDLED` })
          }),
          jsxs('div', {
            className: 'cz-status-meta',
            children: ['Schema ', jsx('code', { children: 'componentize.kit/1' }), ' inside ', jsx('code', { children: 'vendor/kits/' }), '.']
          })
        ]
      }),
      jsxs('div', {
        className: 'cz-status-cell',
        children: [
          jsx('div', { className: 'cz-status-label', children: 'Plugin version' }),
          jsx('div', {
            className: 'cz-status-value',
            children: jsx('span', { className: 'cz-mono', children: state.pluginVersion })
          }),
          jsxs('div', {
            className: 'cz-status-meta',
            children: [
              'Bundled checker CLI ',
              jsx('code', { children: state.checkerVersion }),
              ' · Hermes ',
              jsx('code', { children: '>=0.23.0' }),
              '.'
            ]
          })
        ]
      }),
      jsxs('div', {
        className: 'cz-status-cell',
        children: [
          jsx('div', { className: 'cz-status-label', children: 'Defuzz plugin' }),
          jsx('div', {
            className: 'cz-status-value',
            id: 'cz-st-defuzz-val',
            children: jsx('span', { className: defuzzBadgeClass, children: defuzzBadgeText })
          }),
          jsx('div', { className: 'cz-status-meta', id: 'cz-st-defuzz-meta', children: defuzzMeta })
        ]
      })
    ]
  })
}

export function ComponentizeSettingsPage({ ctx, model: externalModel, initialState }) {
  const [state, setState] = useState(() =>
    initialState ? { ...createInitialState(), ...initialState } : externalModel ? externalModel.get() : createInitialState()
  )
  const modelRef = useRef(null)
  if (!modelRef.current) {
    modelRef.current = externalModel ?? createSettingsPageModel(ctx, next => setState(next))
  } else if (externalModel && modelRef.current !== externalModel) {
    modelRef.current = externalModel
  }

  useEffect(() => {
    const owned = !externalModel
    const activeModel = modelRef.current
    if (!initialState && owned) {
      void activeModel.load()
    }
    return () => {
      if (owned) {
        activeModel.dispose()
      }
    }
  }, [externalModel, initialState])

  const current = externalModel ? state : state
  const model = modelRef.current

  const isLoading = current.phase === 'loading'
  const clientCheck = validateSettingsDraft(current.draft.library_root, current.draft.timeout)
  const serverErrors = current.serverErrors || {}
  const draftCheck = {
    ...clientCheck,
    libErr: clientCheck.libErr || String(serverErrors.library_root || ''),
    timeErr: clientCheck.timeErr || String(serverErrors.timeout || '')
  }
  const isDirty =
    current.draft.library_root.trim() !== current.savedSettings.library_root ||
    current.draft.timeout.trim() !== current.savedSettings.timeout
  const canDiscard = !isLoading && !current.saving && isDirty
  const canSave = !isLoading && !current.saving && isDirty && draftCheck.valid

  const filteredKits = current.kits.filter(kit => kitMatchesFilter(kit, current.activeKitGroup, current.kitSearch))
  const initExampleCmd = 'hermes componentize init /home/user/components'

  return jsxs('div', {
    className: 'cz-page',
    children: [
      jsx('style', { children: PAGE_CSS }),
      jsx('a', {
        className: 'cz-skip-link',
        href: '#cz-main-settings',
        children: 'Skip to plugin settings'
      }),
      jsxs('div', {
        id: 'cz-main-settings',
        children: [
          jsxs('div', {
            className: 'cz-topbar',
            children: [
              jsxs('nav', {
                className: 'cz-breadcrumb',
                'aria-label': 'Breadcrumb',
                children: [
                  jsx('span', { children: 'Settings' }),
                  jsx('span', { 'aria-hidden': 'true', children: '/' }),
                  jsx('span', { children: 'Plugins' }),
                  jsx('span', { 'aria-hidden': 'true', children: '/' }),
                  jsx('strong', { children: 'defuzz-componentize' })
                ]
              }),
              jsxs('span', {
                className: 'cz-scope-pill',
                children: ['Profile scope: ', jsx('strong', { className: 'cz-mono', children: current.profile })]
              })
            ]
          }),
          jsxs('div', {
            className: 'cz-heading-row',
            children: [
              jsxs('div', {
                className: 'cz-title-block',
                children: [
                  jsxs('h1', {
                    children: ['Componentize (', jsx('span', { className: 'cz-mono', children: 'defuzz-componentize' }), ')']
                  }),
                  jsx('p', {
                    className: 'cz-subtitle',
                    children: `Version ${current.pluginVersion} · MIT License · Commands and read-only guide skill · Requires Hermes >= 0.23.0`
                  })
                ]
              }),
              jsxs('div', {
                className: 'cz-heading-actions',
                children: [
                  jsx('button', {
                    type: 'button',
                    id: 'cz-discard-btn',
                    className: 'cz-btn',
                    disabled: !canDiscard,
                    onClick: () => model.discardDraft(),
                    children: 'Discard draft'
                  }),
                  jsx('button', {
                    type: 'submit',
                    form: 'cz-settings-form',
                    id: 'cz-save-btn',
                    className: 'cz-btn cz-btn-primary',
                    disabled: !canSave,
                    children: current.saving ? 'Saving...' : 'Save settings'
                  })
                ]
              })
            ]
          }),
          jsxs('nav', {
            className: 'cz-section-nav',
            'aria-label': 'Page sections',
            children: [
              jsx('a', { href: '#sec-what', children: '1. What it does' }),
              jsx('a', { href: '#sec-flow-kits', children: '2. Kit flowchart' }),
              jsx('a', { href: '#sec-flow-comp', children: '3. Component flowchart' }),
              jsx('a', { href: '#sec-settings', children: '4. Settings & status' }),
              jsx('a', { href: '#sec-machine', children: '5. Machine access' }),
              jsx('a', { href: '#sec-kits', children: '6. Kits (14)' }),
              jsx('a', { href: '#sec-help', children: '7. Help' })
            ]
          }),
          jsx('div', {
            id: 'cz-live-announcer',
            className: 'cz-sr-only',
            'aria-live': 'polite',
            children: current.announcement
          }),

          // Section 1: What this plugin does
          jsxs('section', {
            id: 'sec-what',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-what',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-what', children: 'What this plugin does' }),
                  jsx('span', { className: 'cz-kicker', children: 'Overview' })
                ]
              }),
              jsxs('p', {
                className: 'cz-prose',
                children: [
                  jsx('code', { children: 'defuzz-componentize' }),
                  ' adds the ',
                  jsx('code', { children: 'hermes componentize' }),
                  ' terminal command, the ',
                  jsx('code', { children: '/componentize' }),
                  ' chat command, and a read-only guide skill to Hermes. It helps you set up clear project folders and turn working Python scripts into reusable, tested modules with a saved check record. It runs commands only when you invoke them, makes no network calls of its own, and adds no automatic agent tools or background tasks.'
                ]
              }),
              jsxs('div', {
                className: 'cz-two-col',
                children: [
                  jsxs('div', {
                    className: 'cz-cap-card',
                    children: [
                      jsx('h3', { children: '(a) Project kits (14 bundled layouts)' }),
                      jsxs('p', {
                        children: [
                          'Start a new or existing folder with a known directory layout, starter files, ignore rules, and per-folder placement rules inside ',
                          jsx('code', { children: 'AGENTS.md' }),
                          '.'
                        ]
                      }),
                      jsxs('p', {
                        children: [
                          jsx('strong', { children: 'When you can use it:' }),
                          ' Right away on any dedicated project folder. Project kits do not need a component library to be set up first.'
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-cap-card',
                    children: [
                      jsx('h3', { children: '(b) Componentizing (checked Python modules)' }),
                      jsxs('p', {
                        children: [
                          'Split reusable functions out of an existing Python script into an importable module (a ',
                          jsx('strong', { children: 'component' }),
                          '), check that its output and unit tests pass, and write a hash-checked ',
                          jsx('strong', { children: 'receipt' }),
                          '.'
                        ]
                      }),
                      jsxs('p', {
                        children: [
                          jsx('strong', { children: 'When it runs:' }),
                          ' Only when you run the CLI commands or explicitly ask the agent to ',
                          jsx('code', { children: 'componentize <name>' }),
                          '.'
                        ]
                      })
                    ]
                  })
                ]
              })
            ]
          }),

          // Section 2: Flowchart 1 - Starting a project from a kit
          jsxs('section', {
            id: 'sec-flow-kits',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-flow-kits',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-flow-kits', children: 'Starting a project from a kit' }),
                  jsx('span', { className: 'cz-kicker', children: 'Flowchart 1 of 2 · 6 steps' })
                ]
              }),
              jsxs('p', {
                className: 'cz-prose',
                children: [
                  'A ',
                  jsx('strong', { children: 'kit' }),
                  ' is a static JSON file shipped inside this plugin (no code inside a kit is executed). Five of the six steps only read data; only ',
                  jsx('code', { children: 'kit apply' }),
                  ' writes, and only inside the folder you name.'
                ]
              }),
              jsx('div', {
                className: 'cz-flowchart-wrap',
                tabIndex: 0,
                role: 'region',
                'aria-label': 'Diagram: Starting a project from a kit',
                children: FlowchartKitsSvg()
              }),
              jsx('p', {
                className: 'cz-narrow-hint',
                children: 'Narrow panel: scroll the diagram sideways to zoom details, or read the ordered steps below.'
              }),
              jsxs('div', {
                className: 'cz-text-equiv',
                children: [
                  jsx('h3', {
                    className: 'cz-text-equiv-title',
                    children: 'Text equivalent: Starting a project from a kit (ordered steps)'
                  }),
                  jsxs('ol', {
                    id: 'cz-kits-step-list',
                    className: 'cz-step-list',
                    children: [
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsx('strong', { children: 'Pick your project idea and folder path' }),
                          ' (for example ',
                          jsx('code', { children: '/home/user/projects/short-film' }),
                          '). Nothing on disk is read or written yet.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsx('strong', { children: 'List kits:' }),
                          ' ',
                          jsx('code', { children: 'hermes componentize kit list' }),
                          ' reads the 14 bundled ',
                          jsx('code', { children: 'vendor/kits/*/kit.json' }),
                          ' files inside this plugin. Writes nothing.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsx('strong', { children: 'Preview one kit:' }),
                          ' ',
                          jsx('code', { children: 'hermes componentize kit show <id>' }),
                          " reads that kit's JSON file and prints the folders, starter files, file-routing rules, and ",
                          jsx('code', { children: 'AGENTS.md' }),
                          ' block it would add. Writes nothing.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsx('strong', { children: 'Target folder safety check:' }),
                          ' Before writing, the plugin requires a safe dedicated directory path and refuses unsafe folders (including ',
                          jsx('code', { children: '/' }),
                          ', your home folder ',
                          jsx('code', { children: '~' }),
                          ' and its parent folders), symlink or regular-file targets, and any existing ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ' whose ',
                          jsx('code', { children: '<!-- kit:<id> ... -->' }),
                          ' marker lines are broken. If refused, it stops with exit code ',
                          jsx('code', { children: '2' }),
                          ' or ',
                          jsx('code', { children: '1' }),
                          ' and overwrites nothing.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-write', children: 'WRITES IN <FOLDER>' }),
                          jsx('strong', { children: 'Apply the kit:' }),
                          ' ',
                          jsx('code', { children: 'hermes componentize kit apply <id> <folder>' }),
                          ' creates missing folders and starter files inside ',
                          jsx('code', { children: '<folder>' }),
                          ' only, updates only the ',
                          jsx('code', { children: '<!-- kit:<id> ... -->' }),
                          ' block in ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ' inside that folder, and appends missing lines to ',
                          jsx('code', { children: '.gitignore' }),
                          ' and ',
                          jsx('code', { children: '.gitattributes' }),
                          '. ',
                          jsx('strong', { children: 'Never touched:' }),
                          ' existing starter files, text outside that kit\'s markers in ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ', any folder outside ',
                          jsx('code', { children: '<folder>' }),
                          ', or Hermes ',
                          jsx('code', { children: 'config.yaml' }),
                          '.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsx('strong', { children: 'Check the layout:' }),
                          ' ',
                          jsx('code', { children: 'hermes componentize kit check <id> <folder>' }),
                          ' verifies that every expected folder, starter file, ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ' block, and ignore line is present. Exit ',
                          jsx('code', { children: '0' }),
                          ' means all items are present; exit ',
                          jsx('code', { children: '1' }),
                          ' lists any missing items.'
                        ]
                      })
                    ]
                  })
                ]
              })
            ]
          }),

          // Section 3: Flowchart 2 - Turning code into a component
          jsxs('section', {
            id: 'sec-flow-comp',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-flow-comp',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-flow-comp', children: 'Turning code into a component' }),
                  jsx('span', { className: 'cz-kicker', children: 'Flowchart 2 of 2 · 9 nodes' })
                ]
              }),
              jsx('p', {
                className: 'cz-prose',
                children:
                  'Componentizing splits reusable Python logic from its command-line wrapper and proves the split did not break behavior. Four plain definitions explain the words used in this flow:'
              }),
              jsxs('div', {
                className: 'cz-term-grid',
                'aria-label': 'Plain-language definitions',
                children: [
                  jsxs('div', {
                    className: 'cz-term-item',
                    children: [
                      jsx('strong', { children: 'Component:' }),
                      ' A standalone Python module (',
                      jsx('code', { children: 'components/<name>.py' }),
                      ') that takes plain inputs and returns JSON-ready data, listed in ',
                      jsx('code', { children: 'COMPONENTS.md' }),
                      '.'
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-term-item',
                    children: [
                      jsx('strong', { children: ['Manifest (', jsx('code', { children: 'spec.json' }), '):'] }),
                      ' A JSON file you write naming the component and runner files, test inputs, your golden command, and your unit test command.'
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-term-item',
                    children: [
                      jsx('strong', { children: 'Golden command:' }),
                      ' A command in ',
                      jsx('code', { children: 'spec.json' }),
                      ' that runs your script on fixed test data so the checker can compare output before and after refactoring.'
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-term-item',
                    children: [
                      jsx('strong', { children: ['Receipt (', jsx('code', { children: 'receipt.json' }), '):'] }),
                      ' A file saved in ',
                      jsx('code', { children: '<target>/.componentize/<name>/' }),
                      ' recording SHA-256 hashes of every checked file, note, and test pass.'
                    ]
                  })
                ]
              }),
              jsx('div', {
                className: 'cz-flowchart-wrap',
                tabIndex: 0,
                role: 'region',
                'aria-label': 'Diagram: Turning code into a component',
                children: FlowchartComponentizeSvg()
              }),
              jsx('p', {
                className: 'cz-narrow-hint',
                children: 'Narrow panel: scroll the diagram sideways to zoom details, or read the ordered steps below.'
              }),
              jsxs('div', {
                className: 'cz-text-equiv',
                children: [
                  jsx('h3', {
                    className: 'cz-text-equiv-title',
                    children: 'Text equivalent: Turning code into a component (ordered steps and loops)'
                  }),
                  jsxs('ol', {
                    id: 'cz-comp-step-list',
                    className: 'cz-step-list',
                    children: [
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsxs('strong', { children: ['1. ', jsx('code', { children: 'survey <path>' }), ':'] }),
                          ' Reads Python files as plain text using ',
                          jsx('code', { children: 'ast.parse' }),
                          ' (never imports or executes them) and prints candidate function splits for human review. Writes nothing to disk or ',
                          jsx('code', { children: '.componentize/' }),
                          '.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-exec', children: 'RUNS YOUR COMMANDS + WRITES' }),
                          jsxs('strong', { children: ['2. ', jsx('code', { children: 'begin <name> --manifest spec.json' }), ':'] }),
                          ' Validates ',
                          jsx('code', { children: 'spec.json' }),
                          ', runs your ',
                          jsx('code', { children: 'golden' }),
                          ' command twice (unless waived) to confirm its output is repeatable, and writes generation 1 baseline, manifest, and empty attestation state inside ',
                          jsx('code', { children: '<target>/.componentize/<name>/' }),
                          '.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-write', children: 'WRITES IN .COMPONENTIZE' }),
                          jsxs('strong', { children: ['3. ', jsx('code', { children: 'attest <name> plan | review | impl "<note>"' }), ':'] }),
                          ' Appends a note (at least 20 characters) to ',
                          jsx('code', { children: '<target>/.componentize/<name>/attest.json' }),
                          ' and deletes any existing ',
                          jsx('code', { children: 'receipt.json' }),
                          '. Components require both ',
                          jsx('code', { children: 'plan' }),
                          ' and ',
                          jsx('code', { children: 'review' }),
                          ' notes before ',
                          jsx('code', { children: 'finish' }),
                          '.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-exec', children: 'RUNS YOUR COMMANDS + WRITES' }),
                          jsxs('strong', { children: ['4. ', jsx('code', { children: 'check <name>' }), ':'] }),
                          ' Deletes any old ',
                          jsx('code', { children: 'receipt.json' }),
                          ', checks static architecture rules on declared files, runs your ',
                          jsx('code', { children: 'golden' }),
                          ' and ',
                          jsx('code', { children: 'tests' }),
                          ' commands from ',
                          jsx('code', { children: 'spec.json' }),
                          ' (plus child-process plugin schema capture if ',
                          jsx('code', { children: 'hermes_plugins' }),
                          ' is listed), verifies no declared file changed mid-run, and writes ',
                          jsx('code', { children: 'last_check.json' }),
                          ' under ',
                          jsx('code', { children: '<target>/.componentize/<name>/' }),
                          '. ',
                          jsx('strong', { children: 'Plain authority note:' }),
                          ' ',
                          jsx('code', { children: 'golden' }),
                          ' and ',
                          jsx('code', { children: 'tests' }),
                          " are your own commands and run with your user account's full permissions (not inside a sandbox)."
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'DECISION' }),
                          jsxs('strong', { children: ['5. Did ', jsx('code', { children: 'check' }), ' pass (exit 0)?'] }),
                          ' If exit ',
                          jsx('code', { children: '0' }),
                          ', continue to ',
                          jsx('code', { children: 'finish' }),
                          ' (step 7). If exit ',
                          jsx('code', { children: '1' }),
                          ' because code or tests failed, fix your files and run ',
                          jsx('code', { children: 'check' }),
                          ' (step 4) again. If output changed on purpose, go to ',
                          jsx('code', { children: 'rebaseline' }),
                          ' (step 6).'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-exec', children: 'RUNS YOUR COMMANDS + WRITES' }),
                          jsxs('strong', {
                            children: ['6. ', jsx('code', { children: 'rebaseline <name> --reason "<why>"' }), ' (only when output changed on purpose):']
                          }),
                          ' Deletes any old receipt, runs your ',
                          jsx('code', { children: 'golden' }),
                          ' command twice, and saves a new baseline generation with your reason in ',
                          jsx('code', { children: '<target>/.componentize/<name>/baseline/' }),
                          ' (shown permanently in ',
                          jsx('code', { children: 'status' }),
                          '). Never use ',
                          jsx('code', { children: 'rebaseline' }),
                          ' to hide a failing check. Afterwards, run ',
                          jsx('code', { children: 'check' }),
                          ' (step 4).'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-exec', children: 'RUNS YOUR COMMANDS + WRITES' }),
                          jsxs('strong', { children: ['7. ', jsx('code', { children: 'finish <name>' }), ':'] }),
                          ' Re-runs ',
                          jsx('code', { children: 'check' }),
                          ' under the job lock, confirms required ',
                          jsx('code', { children: 'plan' }),
                          ' and ',
                          jsx('code', { children: 'review' }),
                          ' notes exist and all declared file hashes match, and writes ',
                          jsx('code', { children: '<target>/.componentize/<name>/receipt.json' }),
                          '.'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-read', children: 'READ ONLY' }),
                          jsxs('strong', { children: ['8. ', jsx('code', { children: 'status <name>' }), ':'] }),
                          " Re-computes SHA-256 hashes of declared files, test inputs, notes, Python runtime, and the checker's own code against ",
                          jsx('code', { children: 'receipt.json' }),
                          '. Never runs ',
                          jsx('code', { children: 'golden' }),
                          ' or ',
                          jsx('code', { children: 'tests' }),
                          ', and never modifies ',
                          jsx('code', { children: 'receipt.json' }),
                          ' (only acquires the advisory lock file ',
                          jsx('code', { children: '.componentize/<name>/lock' }),
                          ').'
                        ]
                      }),
                      jsxs('li', {
                        children: [
                          jsx('span', { className: 'cz-badge cz-badge-ok', children: 'RESULT' }),
                          jsx('strong', { children: '9. Receipt OK or STALE?' }),
                          ' Exit ',
                          jsx('code', { children: '0' }),
                          ' prints ',
                          jsx('code', { children: 'OK' }),
                          ' (every declared file still matches the receipt; work is complete). Exit ',
                          jsx('code', { children: '1' }),
                          ' prints ',
                          jsx('code', { children: 'STALE' }),
                          ' (a file, note, or plugin version changed); fix unintended edits or run ',
                          jsx('code', { children: 'finish' }),
                          ' (step 7) again to write a fresh receipt.'
                        ]
                      })
                    ]
                  })
                ]
              })
            ]
          }),

          // Section 4: Settings & Read-only Status
          jsxs('section', {
            id: 'sec-settings',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-settings',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-settings', children: 'Settings' }),
                  jsx('span', { className: 'cz-kicker', children: 'Profile config & live status' })
                ]
              }),
              isLoading
                ? jsxs('div', {
                    id: 'cz-state-loading',
                    'aria-busy': 'true',
                    children: [
                      jsx('p', {
                        className: 'cz-prose',
                        children: 'Reading plugin settings and checking component library status...'
                      }),
                      jsx('div', { className: 'cz-skeleton-line', style: { width: '72%' } }),
                      jsx('div', { className: 'cz-skeleton-line', style: { width: '54%' } }),
                      jsx('div', { className: 'cz-skeleton-line', style: { width: '86%' } })
                    ]
                  })
                : jsxs('div', {
                    id: 'cz-settings-loaded',
                    children: [
                      current.errorBanner
                        ? jsxs('div', {
                            id: 'cz-banner-error',
                            className: 'cz-callout cz-callout-error',
                            role: 'alert',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-err', children: 'ERROR' }),
                                  jsx('span', { children: current.errorBanner })
                                ]
                              })
                            ]
                          })
                        : null,
                      current.backendInactive
                        ? jsxs('div', {
                            id: 'cz-banner-inactive',
                            className: 'cz-callout cz-callout-warn',
                            role: 'status',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-warn', children: 'STATUS ROUTE INACTIVE' }),
                                  jsx('span', { children: 'Backend status route is inactive (plugins.enabled)' })
                                ]
                              }),
                              jsxs('p', {
                                children: [
                                  'The Desktop view is enabled while the Python plugin half is currently disabled for this profile. Settings below still save via Hermes config; run ',
                                  jsx('code', { children: 'hermes plugins enable defuzz-componentize' }),
                                  ' (or flip the Agent switch in Capabilities -> Plugins) to inspect your library folder and live kit files.'
                                ]
                              })
                            ]
                          })
                        : null,
                      current.phase === 'empty'
                        ? jsxs('div', {
                            id: 'cz-banner-empty',
                            className: 'cz-callout cz-callout-empty',
                            role: 'status',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-write', children: 'FIRST RUN' }),
                                  jsx('span', { children: 'No component library folder is set yet' })
                                ]
                              }),
                              jsxs('p', {
                                children: [
                                  'Project kits (',
                                  jsx('code', { children: 'hermes componentize kit ...' }),
                                  ') work right now without this setting. To use receipt commands (',
                                  jsx('code', { children: 'begin' }),
                                  ', ',
                                  jsx('code', { children: 'attest' }),
                                  ', ',
                                  jsx('code', { children: 'check' }),
                                  ', ',
                                  jsx('code', { children: 'finish' }),
                                  ', ',
                                  jsx('code', { children: 'status' }),
                                  ', ',
                                  jsx('code', { children: 'rebaseline' }),
                                  '), pick a dedicated folder and run this command once in your terminal:'
                                ]
                              }),
                              jsxs('div', {
                                className: 'cz-cmd-bar',
                                children: [
                                  jsx('code', { id: 'cz-init-cmd-text', children: initExampleCmd }),
                                  jsx('button', {
                                    type: 'button',
                                    id: 'cz-copy-init-btn',
                                    className: 'cz-btn',
                                    onClick: () => void model.copyCommand('init', initExampleCmd),
                                    children: current.copiedKey === 'init' ? 'Copied' : 'Copy command'
                                  })
                                ]
                              })
                            ]
                          })
                        : null,
                      current.phase === 'missing-folder'
                        ? jsxs('div', {
                            id: 'cz-banner-missing-folder',
                            className: 'cz-callout cz-callout-error',
                            role: 'alert',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-err', children: 'ERROR · LIBRARY MISSING' }),
                                  jsx('span', { children: 'Saved component library folder was not found on disk' })
                                ]
                              }),
                              jsxs('p', {
                                children: [
                                  jsx('code', { children: 'library_root' }),
                                  ' is set to ',
                                  jsx('code', { children: current.savedSettings.library_root }),
                                  ', which does not exist or is missing ',
                                  jsx('code', { children: 'components/COMPONENTS.md' }),
                                  ' (',
                                  jsx('code', { children: 'kit-missing' }),
                                  '). Create or restore the folder with ',
                                  jsx('code', { children: `hermes componentize init ${current.savedSettings.library_root}` }),
                                  ', or enter a different folder path below and save.'
                                ]
                              })
                            ]
                          })
                        : null,
                      current.phase === 'library-error'
                        ? jsxs('div', {
                            id: 'cz-banner-library-error',
                            className: 'cz-callout cz-callout-error',
                            role: 'alert',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-err', children: 'ERROR · LIBRARY CHECK' }),
                                  jsx('span', { children: 'Saved component library folder could not be verified' })
                                ]
                              }),
                              jsxs('p', {
                                children: [
                                  current.library.message || 'Configured library folder failed validation.',
                                  ' Enter a dedicated absolute directory path below and save.'
                                ]
                              })
                            ]
                          })
                        : null,
                      current.phase === 'timeout'
                        ? jsxs('div', {
                            id: 'cz-banner-timeout',
                            className: 'cz-callout cz-callout-error',
                            role: 'alert',
                            children: [
                              jsxs('div', {
                                className: 'cz-callout-title',
                                children: [
                                  jsx('span', { className: 'cz-badge cz-badge-err', children: 'ERROR · EXIT 2' }),
                                  jsx('span', { children: 'Status probe timed out before finishing' })
                                ]
                              }),
                              jsxs('p', {
                                children: [
                                  'Reading library status did not finish within the probe deadline. Your files and saved settings were not changed. Check whether the folder is on a stalled network mount, or raise ',
                                  jsx('strong', { children: 'Outer command timeout' }),
                                  ' below if long test suites hit the 900-second limit during ',
                                  jsx('code', { children: 'check' }),
                                  ' or ',
                                  jsx('code', { children: 'finish' }),
                                  '.'
                                ]
                              })
                            ]
                          })
                        : null,

                      renderStatusCells(current),

                      jsxs('form', {
                        id: 'cz-settings-form',
                        noValidate: true,
                        onSubmit: event => {
                          event?.preventDefault?.()
                          void model.save()
                        },
                        children: [
                          jsxs('div', {
                            className: 'cz-setting-row',
                            children: [
                              jsxs('div', {
                                children: [
                                  jsxs('label', {
                                    className: 'cz-setting-label',
                                    htmlFor: 'cz-input-library-root',
                                    children: ['Component library folder ', jsx('code', { children: 'library_root' })]
                                  }),
                                  jsxs('p', {
                                    className: 'cz-setting-help',
                                    children: [
                                      'Folder holding your reusable Python components (',
                                      jsx('code', { children: 'components/' }),
                                      ' or existing ',
                                      jsx('code', { children: 'hermes_audit/' }),
                                      '), their index (',
                                      jsx('code', { children: 'COMPONENTS.md' }),
                                      '), thin runners (',
                                      jsx('code', { children: 'scripts/' }),
                                      '), specs (',
                                      jsx('code', { children: 'specs/' }),
                                      '), and ',
                                      jsx('code', { children: 'AGENTS.md' }),
                                      '. Running ',
                                      jsx('code', { children: 'hermes componentize init <folder>' }),
                                      ' sets up missing files and saves this path automatically. Leave blank if you only use project kits.'
                                    ]
                                  }),
                                  jsxs('ul', {
                                    className: 'cz-rule-list',
                                    children: [
                                      jsxs('li', {
                                        children: [
                                          jsx('strong', { children: 'Path requirement:' }),
                                          ' Must be an absolute normalized directory path starting with ',
                                          jsx('code', { children: '/' }),
                                          ' (or left empty).'
                                        ]
                                      }),
                                      jsxs('li', {
                                        children: [
                                          jsx('strong', { children: 'Safety checks:' }),
                                          ' Refuses ',
                                          jsx('code', { children: '/' }),
                                          ', your home folder ',
                                          jsx('code', { children: '~' }),
                                          ' and its parent folders, symlink or regular-file targets, and Hermes configuration directories.'
                                        ]
                                      })
                                    ]
                                  })
                                ]
                              }),
                              jsxs('div', {
                                children: [
                                  jsx('input', {
                                    id: 'cz-input-library-root',
                                    name: 'library_root',
                                    type: 'text',
                                    className: 'cz-input cz-mono',
                                    value: current.draft.library_root,
                                    placeholder: '/home/user/components (or leave empty)',
                                    spellCheck: false,
                                    autoComplete: 'off',
                                    'aria-invalid': draftCheck.libErr ? 'true' : 'false',
                                    'aria-describedby': 'cz-msg-library-root',
                                    onChange: event => model.editDraft({ library_root: event?.target?.value ?? '' })
                                  }),
                                  jsx('div', {
                                    id: 'cz-msg-library-root',
                                    className: `cz-field-msg ${draftCheck.libErr ? 'cz-field-msg-err' : isDirty && draftCheck.libWarn ? 'cz-field-msg-warn' : ''}`.trim(),
                                    'aria-live': 'polite',
                                    children: draftCheck.libErr || (isDirty ? draftCheck.libWarn : '')
                                  })
                                ]
                              })
                            ]
                          }),
                          jsxs('div', {
                            className: 'cz-setting-row',
                            children: [
                              jsxs('div', {
                                children: [
                                  jsxs('label', {
                                    className: 'cz-setting-label',
                                    htmlFor: 'cz-input-timeout',
                                    children: ['Outer command timeout ', jsx('code', { children: 'timeout' })]
                                  }),
                                  jsxs('p', {
                                    className: 'cz-setting-help',
                                    children: [
                                      'Maximum wall-clock time for one subprocess command (',
                                      jsx('code', { children: 'survey' }),
                                      ', ',
                                      jsx('code', { children: 'begin' }),
                                      ', ',
                                      jsx('code', { children: 'attest' }),
                                      ', ',
                                      jsx('code', { children: 'check' }),
                                      ', ',
                                      jsx('code', { children: 'finish' }),
                                      ', ',
                                      jsx('code', { children: 'status' }),
                                      ', or ',
                                      jsx('code', { children: 'rebaseline' }),
                                      '). Default is ',
                                      jsx('code', { children: '900' }),
                                      ' seconds (15 minutes). Inside a run, each ',
                                      jsx('code', { children: 'golden' }),
                                      ' or ',
                                      jsx('code', { children: 'tests' }),
                                      ' command has its own 600-second CLI limit by default, so keep this outer limit at least as long as all checks combined.'
                                    ]
                                  }),
                                  jsxs('ul', {
                                    className: 'cz-rule-list',
                                    children: [
                                      jsxs('li', {
                                        children: [
                                          jsx('strong', { children: 'Checked at command start:' }),
                                          ' Must be a finite number greater than ',
                                          jsx('code', { children: '0' }),
                                          '.'
                                        ]
                                      }),
                                      jsxs('li', {
                                        children: [
                                          jsx('strong', { children: 'Recommended range:' }),
                                          ' ',
                                          jsx('code', { children: '60' }),
                                          ' to ',
                                          jsx('code', { children: '3600' }),
                                          ' seconds (1 minute to 1 hour). ',
                                          jsx('code', { children: 'init' }),
                                          ', ',
                                          jsx('code', { children: 'library' }),
                                          ', and ',
                                          jsx('code', { children: 'kit' }),
                                          ' run in-process and do not use this timeout.'
                                        ]
                                      })
                                    ]
                                  })
                                ]
                              }),
                              jsxs('div', {
                                children: [
                                  jsxs('div', {
                                    className: 'cz-input-suffix-wrap',
                                    children: [
                                      jsx('input', {
                                        id: 'cz-input-timeout',
                                        name: 'timeout',
                                        type: 'number',
                                        min: '1',
                                        max: '86400',
                                        step: '1',
                                        className: 'cz-input cz-mono',
                                        value: current.draft.timeout,
                                        'aria-invalid': draftCheck.timeErr ? 'true' : 'false',
                                        'aria-describedby': 'cz-msg-timeout',
                                        onChange: event => model.editDraft({ timeout: event?.target?.value ?? '' })
                                      }),
                                      jsx('span', { className: 'cz-input-suffix', children: 'seconds (default 900)' })
                                    ]
                                  }),
                                  jsx('div', {
                                    id: 'cz-msg-timeout',
                                    className: `cz-field-msg ${draftCheck.timeErr ? 'cz-field-msg-err' : draftCheck.timeWarn ? 'cz-field-msg-warn' : ''}`.trim(),
                                    'aria-live': 'polite',
                                    children: draftCheck.timeErr || draftCheck.timeWarn
                                  })
                                ]
                              })
                            ]
                          }),
                          jsxs('div', {
                            className: 'cz-storage-footnote',
                            children: [
                              jsxs('span', {
                                children: [
                                  'Saved in Hermes profile configuration under ',
                                  jsx('code', { children: 'plugins.entries.defuzz-componentize.settings.<key>' }),
                                  '. No secrets or API keys are used.'
                                ]
                              }),
                              jsx('span', {
                                id: 'cz-save-feedback',
                                role: 'status',
                                'aria-live': 'polite',
                                children: current.saveFeedback
                              })
                            ]
                          })
                        ]
                      })
                    ]
                  })
            ]
          }),

          // Section 5: What it does on this machine
          jsxs('section', {
            id: 'sec-machine',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-machine',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-machine', children: 'What it does on this machine' }),
                  jsx('span', { className: 'cz-kicker', children: 'Permissions & boundaries' })
                ]
              }),
              jsxs('p', {
                className: 'cz-prose',
                children: [
                  'Plain summary of what ',
                  jsx('code', { children: 'defuzz-componentize' }),
                  ' reads, writes, and executes on this machine, and what it never touches:'
                ]
              }),
              jsxs('div', {
                className: 'cz-disclosure-table',
                role: 'region',
                'aria-label': 'Machine access disclosure',
                children: [
                  jsxs('div', {
                    className: 'cz-disclosure-row',
                    children: [
                      jsx('div', { className: 'cz-disclosure-key', children: 'Reads' }),
                      jsxs('div', {
                        className: 'cz-disclosure-val',
                        children: [
                          'This settings page and its read-only API routes read only the bundled ',
                          jsx('code', { children: 'vendor/kits/*/kit.json' }),
                          ' files, your profile\'s ',
                          jsx('code', { children: 'library_root' }),
                          ' and ',
                          jsx('code', { children: 'timeout' }),
                          ' settings, and ',
                          jsx('code', { children: 'COMPONENTS.md' }),
                          ' inside your configured library folder (bounded, without following symlinks). When you invoke CLI or slash commands, the plugin also reads the target folder you name, your ',
                          jsx('code', { children: 'spec.json' }),
                          ' manifest, declared ',
                          jsx('code', { children: 'files' }),
                          ' and ',
                          jsx('code', { children: 'inputs' }),
                          ', and job state inside ',
                          jsx('code', { children: '<target>/.componentize/' }),
                          '.'
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-disclosure-row',
                    children: [
                      jsx('div', { className: 'cz-disclosure-key', children: 'Writes' }),
                      jsxs('div', {
                        className: 'cz-disclosure-val',
                        children: [
                          jsx('strong', { children: 'Settings page:' }),
                          ' Writes only ',
                          jsx('code', { children: 'library_root' }),
                          ' and ',
                          jsx('code', { children: 'timeout' }),
                          ' via Hermes ',
                          jsx('code', { children: 'plugins.manage' }),
                          ' when you select Save settings; backend routes never write.',
                          jsx('br', {}),
                          jsxs('strong', { children: [jsx('code', { children: 'init' }), ' and ', jsx('code', { children: 'kit apply' }), ':'] }),
                          ' Create missing folders and starter files inside the folder you name, update only that kit\'s ',
                          jsx('code', { children: '<!-- kit:<id> ... -->' }),
                          ' block in ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ', and append missing lines to ',
                          jsx('code', { children: '.gitignore' }),
                          ' and ',
                          jsx('code', { children: '.gitattributes' }),
                          '. ',
                          jsx('code', { children: 'init' }),
                          ' also saves ',
                          jsx('code', { children: 'library_root' }),
                          ' in Hermes ',
                          jsx('code', { children: 'config.yaml' }),
                          '.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Receipt commands:' }),
                          ' Write job state and ',
                          jsx('code', { children: 'receipt.json' }),
                          ' inside ',
                          jsx('code', { children: '<target>/.componentize/<name>/' }),
                          ' (or ',
                          jsx('code', { children: '--state-root' }),
                          ' if specified); ',
                          jsx('code', { children: 'status' }),
                          ' acquires only ',
                          jsx('code', { children: '<target>/.componentize/<name>/lock' }),
                          '.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Temporary folders:' }),
                          ' While running ',
                          jsx('code', { children: 'tests' }),
                          ' or ',
                          jsx('code', { children: 'hermes_plugins' }),
                          ' schema checks, creates temporary folders named ',
                          jsx('code', { children: 'componentize-home-*' }),
                          ' and ',
                          jsx('code', { children: 'tool-schema-home-*' }),
                          ' in your system temp directory and removes them when the step ends.'
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-disclosure-row',
                    children: [
                      jsx('div', { className: 'cz-disclosure-key', children: 'Runs' }),
                      jsxs('div', {
                        className: 'cz-disclosure-val',
                        children: [
                          jsx('strong', { children: 'In-process:' }),
                          ' ',
                          jsx('code', { children: 'init' }),
                          ', ',
                          jsx('code', { children: 'library' }),
                          ', ',
                          jsx('code', { children: 'kit' }),
                          ', and the settings page routes run directly in Python without starting child processes.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Subprocess checker:' }),
                          ' Receipt commands and ',
                          jsx('code', { children: 'survey' }),
                          ' run the bundled checker in isolated Python (',
                          jsx('code', { children: 'python -I -B' }),
                          ') with closed ',
                          jsx('code', { children: 'stdin' }),
                          ' and no shell.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Your manifest commands (not a sandbox):' }),
                          ' ',
                          jsx('code', { children: 'begin' }),
                          ', ',
                          jsx('code', { children: 'check' }),
                          ', ',
                          jsx('code', { children: 'finish' }),
                          ', and ',
                          jsx('code', { children: 'rebaseline' }),
                          ' execute the ',
                          jsx('code', { children: 'golden' }),
                          ' and ',
                          jsx('code', { children: 'tests' }),
                          ' commands listed in your ',
                          jsx('code', { children: 'spec.json' }),
                          " with your user account's full authority.",
                          jsx('br', {}),
                          jsxs('strong', { children: [jsx('code', { children: 'hermes_plugins' }), ' measurement:'] }),
                          ' If your ',
                          jsx('code', { children: 'spec.json' }),
                          ' lists ',
                          jsx('code', { children: 'hermes_plugins' }),
                          ', ',
                          jsx('code', { children: 'check' }),
                          ' and ',
                          jsx('code', { children: 'finish' }),
                          ' start a child Python process that imports each listed plugin and calls its ',
                          jsx('code', { children: 'register()' }),
                          ' function with your user authority to measure tool-schema token size.'
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-disclosure-row',
                    children: [
                      jsx('div', { className: 'cz-disclosure-key', children: 'Network' }),
                      jsxs('div', {
                        className: 'cz-disclosure-val',
                        children: [
                          jsx('strong', { children: 'None from this plugin.' }),
                          ' No telemetry, no update checks, and no remote assets. Commands you put in ',
                          jsx('code', { children: 'spec.json' }),
                          ' (',
                          jsx('code', { children: 'golden' }),
                          ', ',
                          jsx('code', { children: 'tests' }),
                          ', or ',
                          jsx('code', { children: 'hermes_plugins' }),
                          ') are not blocked from using the network.'
                        ]
                      })
                    ]
                  }),
                  jsxs('div', {
                    className: 'cz-disclosure-row',
                    children: [
                      jsx('div', { className: 'cz-disclosure-key', children: 'Never does' }),
                      jsxs('div', {
                        className: 'cz-disclosure-val',
                        children: [
                          'Never overwrites an existing starter file; never edits text outside its ',
                          jsx('code', { children: '<!-- kit:<id> ... -->' }),
                          ' markers in ',
                          jsx('code', { children: 'AGENTS.md' }),
                          '; never reads, asks for, or stores API keys, tokens, or passwords; never registers LLM tools or automatic hooks; never bypasses Hermes command approvals; and never installs packages or background services (note: if a command in your ',
                          jsx('code', { children: 'spec.json' }),
                          ' detaches its own background process, outer timeout kills the main process group but cannot guarantee immediate termination of detached grandchildren).'
                        ]
                      })
                    ]
                  })
                ]
              })
            ]
          }),

          // Section 6: Kits Browser (14 bundled kits)
          jsxs('section', {
            id: 'sec-kits',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-kits',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-kits', children: 'Kits' }),
                  jsx('span', { className: 'cz-kicker', children: '14 bundled layouts · Read-only preview' })
                ]
              }),
              jsxs('p', {
                className: 'cz-prose',
                children: [
                  'Browse all 14 bundled kits below (data from ',
                  jsx('code', { children: 'hermes componentize kit list --json' }),
                  ' and ',
                  jsx('code', { children: 'kit show <id> --json' }),
                  '). Select ',
                  jsx('strong', { children: 'Preview folder tree' }),
                  ' on any kit to inspect every folder and file it creates before applying it to a directory.'
                ]
              }),
              jsxs('div', {
                className: 'cz-kits-controls',
                children: [
                  jsxs('div', {
                    className: 'cz-kits-search-row',
                    children: [
                      jsx('label', {
                        className: 'cz-sr-only',
                        htmlFor: 'cz-kit-search',
                        children: 'Search kits by ID, name, summary, or tag'
                      }),
                      jsx('input', {
                        id: 'cz-kit-search',
                        type: 'search',
                        className: 'cz-input',
                        value: current.kitSearch,
                        placeholder: 'Filter kits by ID, purpose, or tag (for example: godot, blender, python, video)...',
                        autoComplete: 'off',
                        onChange: event => model.setKitSearch(event?.target?.value ?? '')
                      }),
                      jsx('span', {
                        id: 'cz-kits-count-label',
                        className: 'cz-kits-count',
                        'aria-live': 'polite',
                        children: `Showing ${filteredKits.length} of ${current.kits.length} kits`
                      })
                    ]
                  }),
                  jsx('div', {
                    className: 'cz-chip-bar',
                    role: 'group',
                    'aria-label': 'Filter kits by category tag',
                    children: KIT_FILTER_GROUPS.map(group =>
                      jsx(
                        'button',
                        {
                          type: 'button',
                          className: 'cz-chip',
                          'data-kit-group': group.id,
                          'aria-pressed': current.activeKitGroup === group.id ? 'true' : 'false',
                          onClick: () => model.setKitGroup(group.id),
                          children: group.label
                        },
                        group.id
                      )
                    )
                  })
                ]
              }),
              jsx('div', {
                id: 'cz-kit-list',
                className: 'cz-kit-list',
                role: 'list',
                children:
                  filteredKits.length === 0
                    ? jsx('div', {
                        className: 'cz-callout cz-callout-empty',
                        style: { margin: 0 },
                        children: jsx('p', {
                          style: { margin: 0 },
                          children: 'No kits match that filter. Clear the search field or choose "All (14)".'
                        })
                      })
                    : filteredKits.map(kit => {
                        const unavailable = kit.availability === 'coming_soon'
                        const isOpen = !unavailable && current.openKitId === kit.id
                        const panelId = `cz-kit-preview-${kit.id}`
                        const applyCmd = `hermes componentize kit apply ${kit.id} /home/user/projects/${kit.id}`
                        const copyKey = `kit:${kit.id}`
                        const preview = current.kitPreviews[kit.id]
                        const previewLoading = current.kitPreviewLoading === kit.id

                        let previewNode = null
                        if (isOpen) {
                          if (preview && Array.isArray(preview.declared_dirs) && Array.isArray(preview.tree)) {
                            const hasLfs = preview.tree.includes('.gitattributes')
                            const rulesCount = Array.isArray(preview.placement_rules) ? preview.placement_rules.length : 0
                            previewNode = jsxs('div', {
                              id: panelId,
                              className: 'cz-kit-preview-panel',
                              children: [
                                jsxs('div', {
                                  className: 'cz-kit-preview-top',
                                  children: [
                                    jsx('span', {
                                      className: 'cz-kits-count',
                                      children: `${preview.declared_dirs.length} declared folders · ${preview.tree.length} total paths · ${rulesCount} extension routing ${rulesCount === 1 ? 'rule' : 'rules'} · ${hasLfs ? 'Git LFS rules included' : 'No Git LFS rules'}`
                                    }),
                                    jsx('span', {
                                      className: 'cz-badge cz-badge-read',
                                      children: `READ-ONLY PREVIEW (kit show ${kit.id})`
                                    })
                                  ]
                                }),
                                jsxs('div', {
                                  className: 'cz-kit-preview-grid',
                                  children: [
                                    jsxs('div', {
                                      children: [
                                        jsx('p', {
                                          className: 'cz-preview-col-label',
                                          children: `Declared folders and purpose (${preview.declared_dirs.length})`
                                        }),
                                        jsx('ul', {
                                          className: 'cz-dir-list',
                                          tabIndex: 0,
                                          'aria-label': `Declared folders for ${kit.id}`,
                                          children: preview.declared_dirs.map(entry =>
                                            jsxs(
                                              'li',
                                              {
                                                children: [
                                                  jsx('code', { children: `${entry.dir}/` }),
                                                  ` — ${entry.summary}`
                                                ]
                                              },
                                              entry.dir
                                            )
                                          )
                                        })
                                      ]
                                    }),
                                    jsxs('div', {
                                      children: [
                                        jsx('p', {
                                          className: 'cz-preview-col-label',
                                          children: `Paths created when missing (${preview.tree.length})`
                                        }),
                                        jsx('pre', {
                                          className: 'cz-tree-box cz-mono',
                                          tabIndex: 0,
                                          'aria-label': `Folder tree for ${kit.id}`,
                                          children: preview.tree.join('\n')
                                        })
                                      ]
                                    })
                                  ]
                                }),
                                jsxs('div', {
                                  className: 'cz-cmd-bar',
                                  children: [
                                    jsx('code', { children: applyCmd }),
                                    jsx('button', {
                                      type: 'button',
                                      className: 'cz-btn',
                                      'data-copy-kit': kit.id,
                                      onClick: () => void model.copyCommand(copyKey, applyCmd),
                                      children: current.copiedKey === copyKey ? 'Copied' : 'Copy apply command'
                                    })
                                  ]
                                })
                              ]
                            })
                          } else {
                            previewNode = jsxs('div', {
                              id: panelId,
                              className: 'cz-kit-preview-panel',
                              children: [
                                jsx('p', {
                                  className: 'cz-prose',
                                  children: previewLoading
                                    ? `Loading folder tree preview for ${kit.id}...`
                                    : current.kitPreviewError ||
                                      (current.backendInactive
                                        ? 'Enable the Python plugin half (hermes plugins enable defuzz-componentize) to load full folder trees.'
                                        : 'Select Preview folder tree to load paths.')
                                }),
                                jsxs('div', {
                                  className: 'cz-cmd-bar',
                                  children: [
                                    jsx('code', { children: applyCmd }),
                                    jsx('button', {
                                      type: 'button',
                                      className: 'cz-btn',
                                      'data-copy-kit': kit.id,
                                      onClick: () => void model.copyCommand(copyKey, applyCmd),
                                      children: current.copiedKey === copyKey ? 'Copied' : 'Copy apply command'
                                    })
                                  ]
                                })
                              ]
                            })
                          }
                        }

                        return jsxs(
                          'div',
                          {
                            className: unavailable ? 'cz-kit-item cz-kit-unavailable' : 'cz-kit-item',
                            'aria-disabled': unavailable ? 'true' : undefined,
                            role: 'listitem',
                            'data-kit-id': kit.id,
                            'data-open': isOpen ? 'true' : 'false',
                            children: [
                              jsxs('div', {
                                className: 'cz-kit-row-header',
                                children: [
                                  jsxs('div', {
                                    className: 'cz-kit-row-main',
                                    children: [
                                      jsxs('div', {
                                        className: 'cz-kit-id-line',
                                        children: [
                                          jsx('span', { className: 'cz-kit-id', children: kit.id }),
                                          jsx('span', { className: 'cz-badge cz-badge-read', children: `v${kit.version}` }),
                                          jsx('span', { className: 'cz-kit-name', children: `· ${kit.name}` })
                                        ]
                                      }),
                                      jsx('p', { className: 'cz-kit-summary', children: kit.summary }),
                                      jsx('div', {
                                        className: 'cz-kit-tags',
                                        'aria-label': 'Tags',
                                        children: (kit.tags || []).map(tag =>
                                          jsx('span', { className: 'cz-kit-tag', children: tag }, tag)
                                        )
                                      })
                                    ]
                                  }),
                                  jsx('button', {
                                    type: 'button',
                                    className: 'cz-btn',
                                    'data-toggle-kit': kit.id,
                                    disabled: unavailable,
                                    'aria-disabled': unavailable ? 'true' : undefined,
                                    'aria-expanded': isOpen ? 'true' : 'false',
                                    'aria-controls': panelId,
                                    onClick: () => void model.toggleKit(kit.id),
                                    children: unavailable ? 'Coming soon' : isOpen ? 'Hide folder tree' : 'Preview folder tree'
                                  })
                                ]
                              }),
                              previewNode
                            ]
                          },
                          kit.id
                        )
                      })
              })
            ]
          }),

          // Section 7: Help
          jsxs('section', {
            id: 'sec-help',
            className: 'cz-section',
            'aria-labelledby': 'cz-h-help',
            children: [
              jsxs('div', {
                className: 'cz-section-header',
                children: [
                  jsx('h2', { id: 'cz-h-help', children: 'Help' }),
                  jsx('span', { className: 'cz-kicker', children: 'First questions & troubleshooting' })
                ]
              }),
              jsxs('div', {
                className: 'cz-faq-list',
                children: [
                  jsxs('details', {
                    className: 'cz-faq-item',
                    open: true,
                    children: [
                      jsx('summary', { children: '1. Do I need a component library folder just to use project kits?' }),
                      jsxs('div', {
                        className: 'cz-faq-body',
                        children: [
                          'No. Project kits (',
                          jsx('code', { children: 'hermes componentize kit list' }),
                          ', ',
                          jsx('code', { children: 'show' }),
                          ', ',
                          jsx('code', { children: 'apply' }),
                          ', and ',
                          jsx('code', { children: 'check' }),
                          ') work immediately on any safe folder path without setting ',
                          jsx('code', { children: 'library_root' }),
                          '. You only need a component library folder when running receipt commands (',
                          jsx('code', { children: 'begin' }),
                          ', ',
                          jsx('code', { children: 'attest' }),
                          ', ',
                          jsx('code', { children: 'check' }),
                          ', ',
                          jsx('code', { children: 'finish' }),
                          ', ',
                          jsx('code', { children: 'status' }),
                          ', and ',
                          jsx('code', { children: 'rebaseline' }),
                          ').'
                        ]
                      })
                    ]
                  }),
                  jsxs('details', {
                    className: 'cz-faq-item',
                    open: true,
                    children: [
                      jsx('summary', { children: '2. Will init or kit apply overwrite files I already wrote?' }),
                      jsxs('div', {
                        className: 'cz-faq-body',
                        children: [
                          'No. Starter files and folders are created only when missing; existing files are kept untouched. In an existing ',
                          jsx('code', { children: 'AGENTS.md' }),
                          ', the plugin edits only inside that kit\'s ',
                          jsx('code', { children: '<!-- kit:<id> ... -->' }),
                          ' marker comments, and in ',
                          jsx('code', { children: '.gitignore' }),
                          ' or ',
                          jsx('code', { children: '.gitattributes' }),
                          ' it only appends lines that are not already present.'
                        ]
                      })
                    ]
                  }),
                  jsxs('details', {
                    className: 'cz-faq-item',
                    children: [
                      jsx('summary', { children: '3. Does Hermes run componentize on my code automatically in the background?' }),
                      jsxs('div', {
                        className: 'cz-faq-body',
                        children: [
                          'No. This plugin registers no LLM tools and no automatic lifecycle hooks. In chat, the bundled skill tells the agent to start componentizing only when you explicitly say ',
                          jsx('code', { children: 'componentize <name>' }),
                          ', and to ask you which folder to use before running ',
                          jsx('code', { children: 'init' }),
                          ' or ',
                          jsx('code', { children: 'kit apply' }),
                          '.'
                        ]
                      })
                    ]
                  }),
                  jsxs('details', {
                    className: 'cz-faq-item',
                    children: [
                      jsx('summary', { children: '4. Why does status say my receipt is STALE after editing a file or updating the plugin?' }),
                      jsxs('div', {
                        className: 'cz-faq-body',
                        children: [
                          jsx('code', { children: 'receipt.json' }),
                          " binds SHA-256 hashes of every declared file, test fixture, attestation note, the Python runtime string, and the checker's own code. If any of those change — including after upgrading ",
                          jsx('code', { children: 'defuzz-componentize' }),
                          ' (which reports ',
                          jsx('code', { children: 'receipt-stale: checker_digest changed' }),
                          ') — ',
                          jsx('code', { children: 'status' }),
                          ' exits ',
                          jsx('code', { children: '1' }),
                          ' without altering the receipt file. Run ',
                          jsx('code', { children: 'hermes componentize finish <name>' }),
                          ' to re-run checks and write a fresh receipt.'
                        ]
                      })
                    ]
                  }),
                  jsxs('details', {
                    className: 'cz-faq-item',
                    children: [
                      jsx('summary', { children: '5. What do exit codes 0, 1, and 2 mean?' }),
                      jsxs('div', {
                        className: 'cz-faq-body',
                        children: [
                          jsx('strong', { children: 'Exit 0:' }),
                          ' Check passed, kit layout is complete, or receipt matches disk.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Exit 1:' }),
                          ' A check found a problem in declared files/tests, a kit item is missing, or a receipt is stale. Each finding prints ',
                          jsx('code', { children: 'id detail — fix: ...' }),
                          '.',
                          jsx('br', {}),
                          jsx('strong', { children: 'Exit 2:' }),
                          ' Usage error, missing library setup, unsafe path, another process holds ',
                          jsx('code', { children: '.componentize/<name>/lock' }),
                          ', or a command exceeded the timeout.'
                        ]
                      })
                    ]
                  })
                ]
              }),
              jsxs('div', {
                className: 'cz-report-box',
                children: [
                  jsx('strong', { children: 'Where to report a problem:' }),
                  ' Open an issue at ',
                  jsx('a', {
                    href: ISSUE_TRACKER_URL,
                    rel: 'noopener noreferrer',
                    children: ISSUE_TRACKER_URL
                  }),
                  '. Include the output of ',
                  jsx('code', { children: 'hermes componentize --version' }),
                  ', the exact command you ran, its exit code (',
                  jsx('code', { children: '0' }),
                  ', ',
                  jsx('code', { children: '1' }),
                  ', or ',
                  jsx('code', { children: '2' }),
                  '), and the finding ID printed on screen. Never attach ',
                  jsx('code', { children: '.env' }),
                  ' files, API keys, or private repository secrets.'
                ]
              })
            ]
          })
        ]
      })
    ]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Componentize',
  description: 'Settings, flowcharts, machine-access disclosure, and 14-kit preview for defuzz-componentize.',
  register(ctx) {
    ctx.registerSettingsPage?.({
      id: 'settings',
      title: 'Componentize',
      icon: 'package',
      render: () => jsx(ComponentizeSettingsPage, { ctx })
    })
  }
}
