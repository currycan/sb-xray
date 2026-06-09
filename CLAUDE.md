# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Verified, Not Imagined

**Everything you state must be grounded in evidence you actually checked — never in assumption.**

- Separate what you observed from what you inferred. Assert only what you verified.
- Before stating something as true, confirm it: run the command, read the file, check the source.
- If you can't verify it yet, say so — mark it unverified rather than asserting it.
- When new evidence contradicts an earlier claim, retract it explicitly in the same turn.

The test: for every claim, could you point to what you checked to back it? If not, verify it or don't assert it.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation rather than after mistakes, and every claim is verified rather than assumed.

---

## 6. Watchtower 自动更新发布纪律（漂移缓解契约）

16 台生产节点经 watchtower 自动跟进 `currycan/sb-xray:latest`。watchtower **不读 docker-compose.yml**——它从现有容器 inspect 出已实例化的 env 重建新镜像。所以运维 `git pull` 同步 compose 之前，新发布引入的 compose env 拿不到。两条硬约束（设计：`docs/superpowers/specs/2026-06-09-watchtower-auto-update-design.md` §4.6）：

- **(a) 新增 env 必须镜像内默认兜底。** 凡新增 `docker-compose.yml` 的 env，必须在 `scripts/entrypoint.py` / `scripts/sb_xray` 内有对应 `os.environ.get(key, 合理默认)`，且默认值向后兼容。保证 watchtower 用旧 env 集重建新镜像时不崩，新功能暂用镜像内默认值直至运维 `git pull` 同步。
- **(b) 修复必须镜像内默认生效。** 任何修复/安全类变更必须落在镜像内默认行为里，不得以「运维设新 compose env」为前提。若某发布确实必须靠新 env 才能正确运行，在发布说明标记 `requires-compose-sync`——**该发布不走 watchtower 自动分发**，强制全量 `git pull && docker compose up -d`。否则修复镜像被自动拉下却因缺 env 不生效，造成虚假安全感。
