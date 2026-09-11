# Agent notes

**Before and after any change: `python tools/verify_all.py` (`--full` before a reconvert or commit).** It runs the converter contract tests, emit-shape checks, assertion coverage, the phase vocabulary self-test, a scan of the generated tree, and the Java guard suite. Each check exists because something broke once.

ReadyAPI XML is converted by `tools/ra_converter/`. Do not hand-edit imported tests. Replicate ReadyAPI (last `setPropertyValue` wins); do not invent a different extract order.

- Rules: `.cursor/rules/`
- Rescue skill: `.cursor/skills/readyapi-restassured-migration/SKILL.md`
- Plan template (Cursor Plan mode): `.cursor/plans/readyapi-restassured-rescue.md`
- Converter Cursor agent: `tools/ra_converter/cursor_agent.json.example` (copy to `cursor_agent.json`, set `enabled` + `apiKey`). Reports: `_audit/cursor_assist_report.md` then `_audit/cursor_assist_after.md`. Memory: `tools/ra_converter/cursor_assist_memory.md`.
- GitLab CI: `.gitlab-ci.yml` (JUnit MR reports; optional `GITLAB_ENABLED` + `GITLAB_TOKEN`)
- Hand-written tests: `CreateTestCase.md` (`tests/manual/`, never `tests/imported/`)
