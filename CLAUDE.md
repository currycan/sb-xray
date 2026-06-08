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

## 5. State Facts, Not Guesses

**Every claim about system state must trace to a command you actually ran — never to inference.**

A fact is something you observed in real output. An inference is something you reasoned your way to. Never present an inference as a fact.

This applies hardest to external/system state: architecture, versions, image digests, what's deployed, host identities, counts, file contents, who-runs-what.

- When evidence is ambiguous (two different image IDs, an unexpected count, a surprising name), **verify the cause before explaining it.** Do not invent a tidy story that fits the pattern — e.g. seeing 5 hosts with a different short image ID and declaring "those 5 are arm64" without checking. Run `uname -m`, read the RepoDigest, inspect the binary. Then state what you found.
- Prefer authoritative identifiers over circumstantial ones: RepoDigest over short image ID, `docker inspect` over a guess, the actual file over memory.
- If you must hypothesize before you can verify, **label it** ("guess, unverified") and verify before anyone relies on it.
- If a later observation contradicts an earlier claim, **stop and retract it explicitly in the same turn.** A wrong claim left standing is worse than no claim.
- Numbers, names, and lists are facts, not flavor. Don't fabricate node names, counts, or mappings to make a summary look complete.

The test: for every factual sentence, could you point to the exact command output that backs it? If not, run the command or mark the claim unverified.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation rather than after mistakes, and every factual claim traces to real command output rather than a plausible guess.
