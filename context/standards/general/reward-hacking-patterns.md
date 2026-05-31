# Reward-hacking patterns — a catalogue for pre-DONE self-check

**Purpose:** When a developer audits their own work before marking it DONE, they use this pattern catalogue to detect whether they've satisfied acceptance criteria by exploiting a loophole in the spec rather than implementing the actual intent. The 9 patterns below are common reward-hacking shapes that appear in code review; knowing them helps catch your own drift before it lands.

This is a **pre-DONE self-check discipline** — developers in the following roles run through these patterns before flipping a task to DONE:
- dev-backend, dev-sr-backend (FastAPI + PostgreSQL)
- dev-frontend, dev-sr-frontend (Next.js + React)
- dev-tester (all tests)

## Pattern A — Test skip / xfail / deletion

**What it looks like:**
- `@pytest.mark.skip` / `@pytest.mark.xfail` decorators added to existing tests
- Test files deleted or renamed to `_test_*` (hidden from pytest discovery)
- Tests commented out

**Why it's bad:**
The test was checking that behavior was correct. Skipping it doesn't implement the behavior; it just hides the verification. An AC like "POST endpoint works" gets "satisfied" by deleting the test that would catch when it breaks.

**Grep anchor:** `@pytest.mark.skip` / `@pytest.mark.xfail` / deleted-test files / renamed `_test_*`

---

## Pattern B — Hardcoded constant on hot path

**What it looks like:**
```python
if x == <fixture_value>:
    return <expected_constant>
```

A conditional that returns the exact output the test expects, keyed on an input the test is known to use.

**Why it's bad:**
The code doesn't actually compute the result; it recognizes the test input and returns canned output. Real inputs fail silently.

**Grep anchor:** `if x == <fixture_value>: return <constant>` shape on hot paths

---

## Pattern C — Bare exception suppression

**What it looks like:**
- `except:` or `except Exception: pass` blocks added in the diff
- Errors swallowed silently without logging or a fallback

**Why it's bad:**
The AC probably says "the feature works" — but silently catching all exceptions masks bugs. The feature appears to work (no error raised), but the actual operation failed. An e-mail send fails? Silently caught. An API call times out? Silently caught.

**Grep anchor:** bare `except:` / `except Exception: pass` blocks added in the diff

---

## Pattern D — Mocking the real dependency

**What it looks like:**
- Mock added to `tests/integration/` or to files that previously hit real services (e.g., `notify_slack`, `stripe_charge`, `oauth_fetch`)
- Integration test that should verify the real contract instead exercises a stub

**Why it's bad:**
The AC says "integrates with Slack" or "charges Stripe" — but the test never touches Slack or Stripe; it hits a mock. The test passes forever; production fails the first time the code runs.

**Grep anchor:** mocks newly added to `tests/integration/` or files that previously hit real services

---

## Pattern E — Test-mode env conditional in production code

**What it looks like:**
```python
if os.environ.get("TEST"):
    return fake_result
```

Or: `if "pytest" in sys.modules: return cached_value`

Business-logic code that changes behavior when it detects it's being tested.

**Why it's bad:**
The code behaves differently in tests vs production. The test passes (returns the fake result); production fails (the fake path is never exercised). Example: `if TEST_MODE: return mock_auth_token`.

**Grep anchor:** `os.environ.get("TEST")` / `pytest in sys.modules` conditionals on business-logic paths

---

## Pattern F — Reading compiled Python artifacts

**What it looks like:**
Code in a test fixture that reads `.pyc` / `__pycache__` / compiled-artifact paths directly to avoid imports.

**Why it's bad:**
`.pyc` files are an implementation detail. Test code that depends on their presence is brittle and non-portable. It also suggests the test is trying to avoid side effects of an import — which the code being tested can't do.

**Grep anchor:** code reading `.pyc` / `__pycache__` / compiled-artifact paths in test fixtures

---

## Pattern G — Blanket suppression directives

**What it looks like:**
- New `# noqa` comments added without an explicit TODO + ticket id
- `# type: ignore` flood newly added to a file
- `@pytest.mark.filterwarnings("ignore")` on a test to suppress warnings the code is emitting

**Why it's bad:**
The real issue (a linting warning, a type error, a deprecation warning) is still there — you just hid it. The AC says "code is clean" but the warning is still being emitted; you just suppressed the signal.

**Grep anchor:** new `# noqa` / `# type: ignore` additions without explicit TODO + ticket id

---

## Pattern H — Vacuous-shape assertion (M9 pattern)

**What it looks like:**
```python
def test_response_shape():
    result = endpoint()
    assert result == baseline  # Never paired with a positive-path assertion
```

An assertion that compares actual output to a baseline, without first asserting that the mutation actually happens on the positive path.

**Why it's bad:**
If the code is broken (returns None, crashes, returns the old value), the baseline is often already equal to the broken output. The test passes for the wrong reason. Classic example: `assert updated_at == old_updated_at` — passes whether the code updated the field or not.

**Fix:** pair with a sibling assertion: `assert updated_at != baseline` (positive), then `assert updated_at == expected_value` (negative on the no-op case).

**Kanban reference:** Kanban #76 root cause.

**Grep anchor:** `assert actual == baseline` without a sibling positive-path assertion

---

## Pattern I — Test-only helper in production code

**What it looks like:**
- New `*_for_tests` / `reset_*_for_tests` / `_for_testing` helper functions added to production modules (`api/src/services/`, `api/src/models/`, etc.)
- Production code with a parameter like `_test_mode=False` that changes behavior

**Why it's bad:**
The AC says "the feature works" — but the code has a back door that tests use to bypass the real logic. When the back door isn't available (in production), the code fails.

**Grep anchor:** new `*_for_tests` / `reset_*_for_tests` / `_for_testing` helpers in production modules

---

## How to use this in your pre-DONE self-check

Before flipping your task to DONE, ask yourself:

1. **Pattern A:** Did I satisfy an AC by skipping, disabling, or deleting a test?
2. **Pattern B:** Did I hardcode an expected output value (literal in source) that masks a bug?
3. **Pattern C:** Did I suppress an exception that should have surfaced — broad `except:` / `except Exception:` / `@ts-ignore` flood?
4. **Pattern D:** Did I substitute a mock for a real dependency the AC required (Slack, Stripe, OAuth, etc.)?
5. **Pattern E:** Did I add an env-conditional shortcut (e.g., `if TEST_MODE: return fake`)?
6. **Pattern F:** Did I rely on compiled artifacts (`.pyc`, `__pycache__`) in test fixtures?
7. **Pattern G:** Did I add `# noqa` / `# type: ignore` WITHOUT an explicit TODO + ticket reference?
8. **Pattern H:** Did I write an assertion like `assert actual == baseline` without a sibling positive-path assertion?
9. **Pattern I:** Did the AC have a hackable surface (literal-vs-intent gap) that I exploited — e.g., a `_for_tests` helper in production code?

**If ANY answer is yes:** STOP. Either fix the implementation to satisfy the actual intent OR halt with `halt_reason='AC hackable — needs spec clarification'`. Do NOT mark DONE.

---

## See also

- Kanban #76 — the M9 root-cause analysis that drove Pattern H discipline.
- dev-reviewer role (`dev-reviewer.md`) — scans diffs for the full A–I catalogue at code-review time as a MAJOR-severity audit.
- dev-spec-reviewer role (`dev-spec-reviewer.md`) — audits acceptance criteria BEFORE implementation to prevent specs that can be trivially "satisfied" by reward-hacking.
