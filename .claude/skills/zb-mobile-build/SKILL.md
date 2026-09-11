---
name: zb-mobile-build
description: >-
  Build the bound Angular/Ionic project and run it on a device, emulator, or simulator via
  Capacitor — web build, cap sync, native build, with the real logs surfaced on failure.
  Use when the operator says "build the app", "run on device", "run on emulator",
  "ลองรันบนมือถือ", "build android", "cap sync", "เปิดใน Android Studio", or asks to see the
  app running outside the browser. NOT for scaffolding screens (use zb-mobile-scaffold) and
  NOT for store submission — publishing is operator-only.
argument-hint: "<android|ios|web> [--device <id>] [--release] [--open]"
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash(npx:*)
  - Bash(npm:*)
  - Bash(curl:*)
metadata:
  version: 1.0.0
  category: stack
  tags: [mobile, capacitor, ionic, build, mutate]
---

# /zb-mobile-build — build and run the bound mobile project on a real target

Chains the four steps that are easy to get out of order (`build` → `cap sync` → native build →
run) and surfaces the raw failure output instead of a summary.

> 🖥️ **iOS cannot be built on a Windows host.** Capacitor iOS builds require macOS + Xcode —
> there is no Windows path, no emulator workaround, and no CLI flag that changes this. On a
> Windows host, `ios` MUST halt at step 1 with that message, not fail confusingly three steps
> later.

## Step 1 — resolve target and refuse the impossible

Resolve the bound project (`bin/lead-project-id.ps1`), read `paths_web`.

| Target | Host requirement | If unmet |
|---|---|---|
| `web` | none | — |
| `android` | Android Studio + SDK + a device/AVD | STOP, report what is missing |
| `ios` | **macOS + Xcode** | **STOP on Windows** — state it plainly, offer `android` or `web` |

Check the toolchain before building, not after: `npx cap doctor`.

## Step 2 — web build

```
npm --prefix <paths_web> run build
```

A failure here is an Angular problem, not a Capacitor one — stop and report the compiler
output verbatim. Do not proceed to sync with a broken build.

## Step 3 — sync native

```
npx cap sync <platform>
```

This copies the web build into the native project AND updates native dependencies. Running it
before step 2 finishes silently ships the PREVIOUS build to the device — the single most
common cause of "my change didn't show up."

## Step 4 — run

```
npx cap run <platform> --target <device-id>
```

`--open` instead opens Android Studio / Xcode and stops there — use it when the operator wants
to drive the native IDE themselves.

## Step 5 — verify it is actually the new build

A running app is not proof the change shipped. Confirm with something only the new build has —
a changed string, a new route, a version stamp:

```
npx cap ls
grep -rn "<a string only the new build contains>" <paths_web>/www/
```

Report which check you used.

## Gate — never cross these

- **No store submission.** Uploading to App Store Connect / Google Play Console is operator-only.
- **No signing-key handling.** Never read, write, generate, or move a keystore / provisioning
  profile / `.p12`. If a build needs signing, stop and hand it to the operator.
- **No `--release` without an explicit operator go-signal** in the current turn.

---

## Why this exists

The four commands are individually simple and constantly run in the wrong order. Step 3 before
step 2 is the classic one: everything "succeeds" and the device shows stale code, which then
gets debugged as an application bug. Step 5 exists so that never survives to the report.

## Usage

```
/zb-mobile-build android
/zb-mobile-build android --device Pixel_7_API_34
/zb-mobile-build android --open
/zb-mobile-build web
```

## Related skills

- **zb-mobile-scaffold** — create the screen before building it.
- **zb-report** — post a checkpoint when a build gates or fails.
