# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **License changed from MIT to PolyForm Noncommercial 1.0.0.** Noncommercial use (personal, research, hobby, charitable/educational/government organizations) remains free; any commercial use now requires a separate written license from the copyright holder (aryanduntley@gmail.com). Versions up to and including 1.66.1 remain under MIT.

## [0.1.0] - 2026-02-10

### Added
- 220 MCP tools for FP enforcement and project management
- 4 SQLite databases: aifp_core (global, read-only), project (per-project), user_preferences (per-project), user_directives (optional, per-project)
- Custom JSON-RPC 2.0 stdio server (pure Python stdlib, no SDK dependency)
- Directive-driven workflow system with 129 directive definitions
- Convention-based tool annotations on all 220 tools (readOnlyHint, destructiveHint, idempotentHint, openWorldHint, title)
- Two use cases: regular software development (Case 1) and custom directive automation (Case 2)
- Reserve-finalize lifecycle for files, functions, and types
- Hierarchical project tracking: completion paths, milestones, tasks, subtasks, sidequests
- Themes and flows for project organization
- User preference learning with per-directive key-value overrides
- FP-powered Git integration (branch management, conflict resolution)
- Automated backup system (opt-in)
- System prompt shipped with package (`python3 -m aifp --system-prompt`)
- MCPB bundle for Claude Desktop installation
- 180 automated tests (102 server + 78 submission)
- Interactive test screenplay for end-to-end lifecycle verification
