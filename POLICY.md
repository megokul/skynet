# SKYNET — Mandatory Update Policy

**Status**: ENFORCED
**Applies To**: All agents (Claude Code, Cursor, Copilot, human developers)
**Last Updated**: 2026-02-15

---

## 🚨 MANDATORY RULE: Always Update These 5 Files

After **EVERY** significant change (building a component, adding a feature, fixing a bug, making decisions), you **MUST** update these files:

### **The 5 Mandatory Files**

| File | When to Update | What to Update |
|------|----------------|----------------|
| **1. [CLAUDE.md](CLAUDE.md)** | After every component/feature | ✅ Status<br>✅ Implementation table<br>✅ Change log<br>✅ Last updated date |
| **2. [TODO.md](TODO.md)** | After completing tasks | ✅ Mark tasks [x]<br>✅ Add new tasks discovered<br>✅ Update progress bar<br>✅ Current sprint |
| **3. [SESSION_NOTES.md](SESSION_NOTES.md)** | End of every session | ✅ Add session entry<br>✅ Document decisions<br>✅ Record blockers<br>✅ List artifacts created |
| **4. [AGENT_GUIDE.md](AGENT_GUIDE.md)** | When workflow changes | ✅ Update "What's Working"<br>✅ Add troubleshooting if new issues<br>✅ Update quick links |
| **5. [DEVELOPMENT.md](DEVELOPMENT.md)** | When patterns change | ✅ Add new code patterns<br>✅ Document new conventions<br>✅ Update examples |

---

## ⚠️ Consequences of Not Updating

### **If You Don't Update These Files:**

❌ **Context Loss** - Next agent won't know what was done
❌ **Duplicate Work** - Rebuilding what already exists
❌ **Broken Continuity** - Can't pick up where you left off
❌ **Confusion** - Unclear what's working vs broken
❌ **Time Wasted** - Hours spent catching up

### **If You DO Update These Files:**

✅ **Zero Ramp-Up** - Next agent productive immediately
✅ **Clear Progress** - Always know what's done
✅ **Smooth Handoff** - Perfect session continuity
✅ **Quality Maintained** - Patterns followed consistently
✅ **Fast Velocity** - No time wasted on context

---

## 📋 Update Checklist (Use This Every Time)

### **Before Starting Work**
```
[ ] Read CLAUDE.md (current state)
[ ] Check TODO.md (what to build next)
[ ] Review last session in SESSION_NOTES.md
[ ] Verify tests pass
```

### **After Completing Work**
```
[ ] Update CLAUDE.md:
    [ ] Mark component status (✅ or 🚧)
    [ ] Update "Last Updated" date
    [ ] Add entry to "Change Log"
    [ ] Update "Current Implementation Status"

[ ] Update TODO.md:
    [ ] Mark completed tasks [x]
    [ ] Add new tasks discovered
    [ ] Update progress percentage
    [ ] Update "Current Sprint"

[ ] Update SESSION_NOTES.md:
    [ ] Add new session entry
    [ ] Document what was built
    [ ] Record decisions made
    [ ] Note blockers encountered
    [ ] List next session goals

[ ] Update AGENT_GUIDE.md (if needed):
    [ ] Add to "What's Working"
    [ ] Update troubleshooting
    [ ] Add new common tasks

[ ] Update DEVELOPMENT.md (if needed):
    [ ] Add new code patterns
    [ ] Document new conventions
    [ ] Update examples

[ ] Run tests to verify nothing broke
[ ] Commit changes (if using git)
```

---

## 🎯 Detailed Update Requirements

### **1. CLAUDE.md Updates**

#### **When Component Built:**
```markdown
### ✅ **Completed (Phase X.X)**

#### **X. Component Name — Description**
- **Location**: `path/to/file.py`
- **Purpose**: What it does
- **Status**: ✅ Implemented and tested
- **Model**: gemini-2.5-flash (if AI-powered)
```

#### **Change Log:**
```markdown
### YYYY-MM-DD (Session XXX)
- ✅ Built Component X
- ✅ Created test_component.py
- ✅ Updated documentation
```

#### **Last Updated:**
```markdown
**Last Updated**: 2026-02-15  ← Change this!
```

---

### **2. TODO.md Updates**

#### **Mark Tasks Complete:**
```markdown
- [x] Create component.py  ← Change [ ] to [x]
- [x] Implement main method
- [x] Create test file
```

#### **Update Progress:**
```markdown
Phase 1: SKYNET Core
├─ 1.1 Planner       [████████████] 100% ✅
├─ 1.2 Dispatcher    [████████████] 100% ✅  ← Update this!
```

#### **Add New Tasks:**
```markdown
### Discovered During Development
- [ ] Add retry logic for API calls
- [ ] Improve error messages
```

---

### **3. SESSION_NOTES.md Updates**

#### **Add Session Entry:**
```markdown
## Session XXX — YYYY-MM-DD — Component Name

**Agent**: Claude Code (Sonnet 4.5)
**Duration**: ~2 hours
**Phase**: 1.2 (Dispatcher)

### 🎯 Goals
- Build Dispatcher component
- Test PlanSpec → ExecutionSpec conversion

### ✅ What Was Built
- skynet/core/dispatcher.py
- test_dispatcher.py

### 🔧 Technical Decisions
- Decision: Use pattern matching for step mapping
- Reasoning: More flexible than hardcoded rules

### 🧪 Testing Results
- Test 1: PASSED - Simple plan converted correctly

### 🚧 Blockers Encountered
- None

### 🎯 Next Session Goals
- Build Orchestrator
```

---

### **4. AGENT_GUIDE.md Updates**

#### **Update "What's Working":**
```markdown
| Component | Status | Location | Test File |
|-----------|--------|----------|-----------|
| Planner | ✅ Complete | `skynet/core/planner.py` | `test_planner_simple.py` |
| Dispatcher | ✅ Complete | `skynet/core/dispatcher.py` | `test_dispatcher.py` |  ← Add this!
```

#### **Add Troubleshooting (if new issue):**
```markdown
### **Error: "New error message"**
```bash
# Solution
...
```
```

---

### **5. DEVELOPMENT.md Updates**

#### **Add New Patterns (if established):**
```markdown
### **Pattern Name**

```python
✅ Good:
# Example

❌ Bad:
# Counter-example
```
```

---

## 🔒 Enforcement Mechanism

### **Pre-Commit Checklist**

Before considering work "done":

```
1. Component works? [ ]
2. Tests pass? [ ]
3. CLAUDE.md updated? [ ]
4. TODO.md updated? [ ]
5. SESSION_NOTES.md updated? [ ]
6. AGENT_GUIDE.md updated (if needed)? [ ]
7. DEVELOPMENT.md updated (if needed)? [ ]
```

**If ANY checkbox is unchecked → NOT DONE!**

---

## 📊 Quality Metrics

### **Good Session** ✅
- All 5 files updated
- Tests passing
- Clear next steps documented
- Decisions recorded

### **Bad Session** ❌
- Files not updated
- No session notes
- Unclear what was done
- Next agent confused

---

## 🎓 Why This Matters

### **Real Scenario:**

**Without Policy:**
```
Session 1: Build component, don't update docs
Session 2: Can't remember what was built, waste 30 min figuring out
Session 3: Rebuild something that exists, waste 2 hours
Result: 2.5 hours wasted
```

**With Policy:**
```
Session 1: Build component, update all 5 files
Session 2: Read files, know exactly what's done, continue building
Session 3: Productive immediately
Result: 0 hours wasted, smooth progress
```

**Time Saved**: 2.5 hours per 3 sessions = **50% velocity increase!**

---

## 🚀 Quick Reference

### **File Update Priority**

**Always (100% of sessions):**
1. CLAUDE.md
2. TODO.md
3. SESSION_NOTES.md

**When Needed (50% of sessions):**
4. AGENT_GUIDE.md
5. DEVELOPMENT.md

---

## 📝 Session Template

Copy this at end of every session:

```markdown
## Session Completion

✅ Checklist:
- [ ] CLAUDE.md - Updated status, change log, date
- [ ] TODO.md - Marked tasks [x], updated progress
- [ ] SESSION_NOTES.md - Added session entry
- [ ] AGENT_GUIDE.md - Updated if needed
- [ ] DEVELOPMENT.md - Updated if needed
- [ ] Tests passing
- [ ] Ready for next session

Next Agent: You're good to go! ✅
```

---

## 🎯 Bottom Line

**MANDATORY RULE:**

> After every significant change, update:
> 1. CLAUDE.md
> 2. TODO.md
> 3. SESSION_NOTES.md
> 4. AGENT_GUIDE.md (if workflow changed)
> 5. DEVELOPMENT.md (if patterns changed)

**NO EXCEPTIONS.**

This is not optional. This is how we maintain velocity and quality.

---

**Policy Established**: 2026-02-15
**Enforced By**: All agents working on SKYNET
**Non-Compliance**: Results in context loss and wasted time
