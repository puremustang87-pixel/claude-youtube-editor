<!-- Every PR answers these three. No answers = not mergeable. Keep it plain-language. -->

## 1. What does this do? (plain language, no jargon)
_One paragraph a non-coder understands. What can the tool do after this that it couldn't before?_

## 2. How does the owner verify it? (one command or click)
_The exact command to run, or what to look at, to see it works with their own eyes._

```bash
# e.g. python -m unittest tools.editor.test_server -v
```

## 3. What could break? What did you NOT do?
_Honest risks, edge cases, and anything deferred. "Nothing" is rarely true._

---

### Checklist
- [ ] Tests added/updated for every new behavior
- [ ] `python -m unittest tools.editor.test_server -v` passes locally
- [ ] Existing Remotion + `timeline.json` + `bake.py` contracts unchanged (or migration + justification included)
- [ ] No secrets, tokens, or footage committed
- [ ] Windows + POSIX paths both handled
- [ ] STATUS.md updated if this changes what's merged/in-review

### For the reviewer (Fable)
- [ ] Cloned and **executed** — not just read
- [ ] Findings reference `file:line`
- [ ] Verdict stated: merge / merge-after-fixes / redesign
