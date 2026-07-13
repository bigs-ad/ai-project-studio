# AI Project Studio

AI Project Studio is a producer-led Codex plugin for advancing game and web application projects without relying on chat history as project memory.

It keeps project truth in the repository, limits implementation to approved work, declares deliverable fidelity, and restores context across new Codex tasks and temporary subagents.

## What it provides

- Discovery, validation, build, release, and operate phases
- Game and web application profiles
- Project-local goals, decisions, roadmap, backlog, and playtest or product files
- Bounded work items with approval-bound specifications
- Explicit deliverable levels from placeholder through final in-context output
- Compact recovery briefs and pause checkpoints
- User-owned approval and final acceptance records
- Deterministic validation and lifecycle commands

The workflow intentionally avoids permanent fictional teams, exhaustive checklists, and automatic process expansion.

## Requirements

- Codex with plugin support
- Python 3.10 or newer

## Install for local use

Clone the repository into your personal plugin source directory:

```bash
git clone https://github.com/bigs-ad/ai-project-studio.git ~/plugins/ai-project-studio
```

Then ask Codex to add the local `ai-project-studio` directory to your personal marketplace and install it. The installed plugin should expose the `$manage-project-studio` skill.

## Start a project

Open Codex in a game or web application repository and use a short project brief:

```text
Use $manage-project-studio to start this game project.

Project name: [name]
Project owner: [owner label]
Initial idea: [rough idea]
```

The first response stays in Discovery: no implementation or delegation, a separation of facts from assumptions and unknowns, one largest current risk, one recommended next step, and one important question.

## Recover context

At the start of a later task, ask:

```text
Use $manage-project-studio to recover this project and recommend the single next step.
```

The skill validates project state, generates a compact brief, and reads only the files needed for the current decision.

## CLI

The deterministic state manager lives at:

```text
skills/manage-project-studio/scripts/studio.py
```

Examples:

```bash
python3 skills/manage-project-studio/scripts/studio.py profiles
python3 skills/manage-project-studio/scripts/studio.py validate /path/to/project
python3 skills/manage-project-studio/scripts/studio.py brief /path/to/project
```

Run `--help` for lifecycle, gate, work-item, repair, owner, and checkpoint commands.

## Development

Run the regression suite:

```bash
python3 -m unittest discover \
  -s skills/manage-project-studio/scripts/tests \
  -v
```

## Scope

The current profiles support games and web applications. Other project types are intentionally not treated as equivalent without a dedicated profile.

## License

[MIT](LICENSE)

