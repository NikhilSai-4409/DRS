# Cricket DRS Review Workstation — Roadmap & Backlog

**Product scope:** a third-umpire **review workstation** — receive an appeal, analyze
evidence, produce a decision, store it, export it. It is **not** a scoreboard,
broadcast-graphics, or match-management system. The workflow is the spec:

```
Launch → Pre-Match Checklist → Start Review Session → Dashboard
       → Request Review → Choose Type → Analysis → Replay → Decision → Export
       → repeat → End Review Session
```

---

## Pass 1 — FROZEN

Navigation and page responsibilities are finalized and verified in the running app.

- Review Workstation navigation; sidebar finalized; one responsibility per page.
- Pages: Dashboard · Reviews · Replay · Cameras · Camera Health · Calibration ·
  System · Pre-Match Checklist · Testing · Validation · Vision Studio · Model Manager.
- Settings moved to the top bar (gear).
- Checklist is **read-only** and **deep-links** each non-pass item to its owning page.
- Camera selection lives **only** on the Cameras page ("Cameras in use"); zero duplicated controls.
- Existing review workflow verified (Request Review → analysis; engineer panels render).

### 🔒 UI Freeze rule
> **No navigation changes, page moves, or page additions unless fixing a bug or
> supporting a completely new review type.** From now on, don't ask "what page
> should this go on?" — ask "**which existing page owns this?**" Only if the answer
> is "none" consider a new page.

---

## Backlog

### Camera Discovery & Selection
Broadened from the "hardcoded 6-camera launcher" limitation (`main.js` spawns the
backend with a fixed `--cameras 0,1,2,3,4,5`). This is a subsystem, not a one-liner.

- [ ] Auto-detect connected cameras at startup (`drs_app.py --list-cameras` already scans; the launcher must use it)
- [ ] Launch backend with only the detected cameras
- [ ] Refresh the camera list without restarting
- [ ] Persist selected cameras between sessions
- [ ] Identify cameras by serial number (later, for industrial SDKs)

**Progression**
- **Now (USB/webcams):** detect available cameras → 3 connected launches `--cameras 0,1,2`;
  2 connected launches `--cameras 0,1`. **No offline placeholders.**
- **Later (industrial):** stop labeling as Camera 0/1/2; identify by serial and role, so
  camera order never changes even if USB order does:
  ```
  Basler-40129382 → Pitch Camera
  Basler-40129390 → Bowler Camera
  Basler-40129404 → Batter Camera
  ```

### Configuration Profiles
Return to the same ground and load everything instead of re-configuring each time.

- [ ] Save venue profile
- [ ] Save calibration profile
- [ ] Save camera layout
- [ ] Save replay settings
- [ ] Save production model

Selecting e.g. **"PES Ground A"** loads calibration + camera names + production model +
replay settings — saving several minutes before every session. (More useful once the
Session model exists, hence its position after Session in the roadmap.)

### Smaller items (not regressions — deferred features)
- [ ] Reviews page **export** (per-review export control)
- [ ] Store **review type** on the review record (currently `—` in the Reviews table)
- [ ] Industrial camera metrics on Camera Health (temperature / exposure / gain — needs SDK)

---

## Data model (design — write before building the Session)

The Review Session is responsible for **everything needed to reproduce a review
months later**, not just operator/team info.

```
Review Session
──────────────────
Session ID
Operator
Tournament
Venue
Ground
Date
Camera Configuration
    Pitch Camera
    Bowler Camera
    Batter Camera
Active Production Model
Calibration Profile
Started
Ended
Reviews[]
```

```
Review
──────────────
ID
Type
Timestamp
Decision            (final)
Original Decision   (on-field, if applicable)
Confidence
Replay
Export Files
Model Version
Calibration Version
Camera Set
```

Goal: six months later, "Which model produced Review #42?" has an exact answer.
Every review type (LBW, Edge, Run Out, …) then stores data in the same format —
saving major rework as types are added.

---

## Roadmap (ordered — dependencies first)

```
✓ Freeze Pass 1
↓ Review Session
↓ Configuration Profiles
↓ Real LBW validation
↓ Trajectory tuning
↓ Calibration accuracy
↓ Model validation
↓ Run Out
↓ Stumping
↓ Wide
↓ Edge
↓ Above-waist (No Ball)
↓ Industrial Camera Discovery
↓ Industrial Camera SDK
↓ Hardware Synchronization
↓ Performance Optimization
```

Rationale: no UI tasks remain — everything now improves the **review engine** and
**decision quality**. Ordering keeps dependencies sane (e.g. hardware sync only after
the algorithms are validated; Configuration Profiles only after the Session model exists).
