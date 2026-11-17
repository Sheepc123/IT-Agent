---
name: project-code-workflow
description: 本仓库新增、修改或重构业务代码时使用；约束模块组织、英文代码注释与按改动风险选择验证范围。纯讨论、只读检查和纯文档修改不使用。
---

# Project code workflow

Apply this skill when changing executable code in this repository.

- Put a cohesive feature in a named package or directory when it has multiple responsibilities. Keep route handlers, business logic, identity or permission checks, and development seed helpers in appropriately named modules. Update imports and documented entry points when moving code; avoid leaving misleading duplicate implementations at the package root.
- Write code comments and docstrings in English. Document public functions, parameters or return values when the contract is not obvious, and explain non-obvious security, transaction, or business invariants. Keep comments accurate and concise; do not narrate each line or add filler comments.
- Match verification to the change. For a small local feature or refactor, run focused tests and the affected build or static check only when needed. Do not run the entire suite by default. Broaden testing only when shared behavior changed, focused checks fail, or the user requests it. Report precisely what ran and what did not.
- Follow the repository change-log requirement in `scripts/CHANGELOG.md` and the file-by-file delivery rule in `.agents/skills/project-file-delivery/SKILL.md`.
