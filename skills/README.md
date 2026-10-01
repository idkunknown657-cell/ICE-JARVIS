# `skills/` — installed skill packages

A folder in here is a **skill package**: a capability JARVIS can call by voice,
kept as a manifest, its code and its tests in one place. Drop a folder in, restart
(or just ask JARVIS to look again), and the tool appears — no code change, no
registration step.

This is the richer of the two ways to add a capability. A single-file tool belongs
in [`plugins/`](../plugins) instead; a package is what a skill needs once it
outgrows one file — settings of its own, a test case per behaviour, a version.

## Layout

```
skills/
└── internet_speed_test/
    ├── manifest.json     what it is called, when to call it, what it takes
    ├── skill.py          the code: one execute() (or run()) function
    └── test_cases.json   arguments to try it with, one object per case
```

### `manifest.json`

```json
{
  "name": "internet_speed_test",
  "description": "Measures download and upload speed. Trigger: 'test my internet speed'.",
  "parameters": {
    "type": "OBJECT",
    "properties": {
      "include_upload": { "type": "BOOLEAN", "description": "Also measure upload." }
    },
    "required": []
  },
  "triggers": ["test my internet speed", "how fast is my internet"],
  "aliases": ["speed_test"],
  "version": "1.0.0"
}
```

* **`name`** must match the folder name and be a valid identifier.
* **`description`** is read by the model to decide when to call the skill, so
  write it for a reader trying to choose between this and another tool. Say what
  it does, and name the phrases that should trigger it.
* **`parameters`** is a Gemini function-declaration schema. `{"type": "OBJECT", "properties": {}}` means "takes no arguments".
* **`triggers`** and **`aliases`** are optional. They let a spoken phrase reach
  this skill *without* a model call, and they are the only place a phrase like
  "how fast is my internet" needs to be written down.
* **`active`** (optional, default `true`) — `false` ships the package switched
  off. JARVIS can also switch it off from the registry without touching this
  file, which is why the flag is optional.

### `skill.py`

Two shapes are accepted, so a skill written either way works:

```python
def execute(**kwargs):          # or: def run(parameters, player=None, session_memory=None)
    include_upload = kwargs.get("include_upload", True)
    ...
    return "Your download speed is 94 megabits per second."
```

* Return a **short natural-language sentence** — it is spoken. Returning a dict
  with a `summary`, `output`, `text`, `result` or `message` key also works, and
  that value is spoken instead.
* **Never raise.** Catch your own errors and return a sentence explaining what
  could not be done. An exception here is caught and reported, but the sentence
  you write will be a better one.
* Survive being called with **no arguments**: JARVIS tries a skill that way first.

### `test_cases.json`

```json
[{ "include_upload": false }]
```

Argument sets to try the skill with. Used by the forge's sandbox when a skill is
generated, and by anyone testing a package by hand.

## What JARVIS will refuse to run

Anything in a package is code that runs on the user's machine, so packages pass
the same gate a self-written skill does (`core/skill_crucible.py`): it must parse,
declare a name and a description, contain no destructive or credential-reading
behaviour, import only packages that are already installed, and survive being
called once in an isolated interpreter. A package that fails is skipped with its
reason shown in the skill list rather than silently ignored.

Packages that a user installs are **not** deleted by anything JARVIS does on its
own: `forget` only removes files it wrote itself (`"source": "forged"`).

## Writing one without writing code

Ask for it: *"learn how to convert a Vedic square"*, or *"make yourself able to
check a domain's expiry date"*. JARVIS writes the package into a staging folder,
verifies it, and — once it passes — moves it into `plugins/` and starts using it,
answering the original request in the same breath. What it wrote is listed by
**"what have you taught yourself?"**, and can be switched off or deleted by name.
