# AGENTS.md

Lightweight operating guide for coding agents in this repository.

## Scope

- Project: `mini-swe-agent`
- Language: Python (`>=3.10`)
- Core packages under `src/debugmaster/`
- Keep solutions minimal and readable.

## Repository Map

- `src/debugmaster/agents` - agent control flow
- `src/debugmaster/environments` - action execution backends
- `src/debugmaster/models` - model interfaces/adapters
- `src/debugmaster/run` - CLI and run scripts
- `src/debugmaster/config` - YAML configs
- `tests` - pytest suite

## Quick Setup

- Editable install:
  - `pip install -e .`
- Dev install:
  - `pip install -e '.[dev]'`
- Full install (extras):
  - `pip install -e '.[full]'`
- Pre-commit:
  - `pip install pre-commit && pre-commit install`

## Build / Lint / Test Commands

## Tests

- Run all tests (fast path):
  - `pytest -n auto`
- CI-like test run with coverage:
  - `pytest -v --cov --cov-branch --cov-report=xml -n auto`
- Run non-slow tests:
  - `pytest -k "not slow"`

## Single-Test Patterns (important)

- One file:
  - `pytest tests/agents/test_default.py`
- One test function:
  - `pytest tests/agents/test_default.py::test_parse_action`
- One class test method:
  - `pytest tests/agents/test_code_context_manager.py::TestCodeContextManager::test_render_file_context`
- Name filter:
  - `pytest -k "code_context and not slow"`

## Lint / Format

- Full local checks:
  - `pre-commit run --all-files`
- Ruff lint:
  - `ruff check .`
- Ruff autofix:
  - `ruff check . --fix`
- Ruff format:
  - `ruff format .`
- Pylint (error-only, CI-aligned):
  - `pylint debugmaster/ --errors-only`

## Docs

- Strict docs build:
  - `mkdocs build --strict`

## Useful CLI Commands

- `mini`
- `mini -v`
- `mini-extra`
- `mini-extra swebench --help`
- `mini-extra swebench-single --help`

# Style guide

1. Target python 3.10 or higher
2. Use python with type annotations. Use `list` instead of `List`.
3. Use `pathlib` instead of `os.path`. Use `Path.read_text()` over `with ...open()` constructs.
4. Use `typer` to add interfaces
5. Keep code comments to a minimum and only highlight particularly logically challenging things
6. Do not append to the README unless specifically requested
7. Use `jinja` for formatting templates
8. Use `dataclass` for keeping track config
9. Do not catch exceptions unless explicitly told to.
10. Write concise, short, minimal code.
11. In most cases, avoid initializing variables just to pass them to a function. Instead just pass the expression to the function directly.
12. Not every exception has to be caught. Exceptions are a good way to show problems to a user.
13. This repository rewards minimal code. Try to be as concise as possible.

Here's an example for rule 11:

```python
# bad
a = func()
Class(a)

# good
Class(func())
```

## Test style

1. Use `pytest`, not `unittest`.
2. <IMPORTANT>Do not mock/patch anything that you're not explicitly asked to do</IMPORTANT>
3. Avoid writing trivial tests. Every test should test for at least one, preferably multiple points of failure
4. Avoid splitting up code in multiple lines like this: `a=func()\n assert a=b`. Instead, just do `assert func() == b`
5. The first argument to `pytest.mark.parametrize` should be a tuple (not a string! not a list!), the second argument must be a list (not a tuple!).

Here's an example for rule 4:

```python
# bad
result = func()
assert result == b

# good
assert func() == b
```


## Formatting / Imports

- Follow Ruff config in `pyproject.toml`.
- Max line length: `120`.
- Use double quotes.
- Keep imports sorted and clean (`I001` enforced).
- Prefer modern syntax compatible with `py310`.

## Naming

- Functions/variables/modules: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Match existing naming in touched files over personal preference.

## Test Style Rules

- Use `pytest`, not `unittest`.
- Do not mock/patch unless explicitly requested.
- Avoid trivial tests; cover meaningful behavior/failure points.
- Prefer direct assertions:
  - `assert func() == expected`
- For `pytest.mark.parametrize`:
  - first arg: tuple
  - second arg: list
- Print statements in tests are allowed.

## Architecture Preferences

- Keep components simple and composable.
- Prefer adding a new variant/component over bloating an existing one.
- Put highly specific functionality in `extra/` modules.
- Put shared helpers in `utils/` only when reuse is real.

## Agent Workflow (minimal)

1. Read relevant code and nearby tests.
2. Make the smallest correct change.
3. Run the most targeted test(s) first.
4. Run lint/format checks.
5. Run broader tests only as needed by risk.

## CI Reality (what to match locally)

- Pytest workflow runs on Python 3.11 with coverage and xdist.
- Lint workflow runs pylint with `--errors-only`.
- Pre-commit uses Ruff, typos, basic hygiene hooks, and prettier for JS/CSS.

## Rule Sources Included

- `.cursor/rules/project.mdc`
- `.cursor/rules/style.mdc`
- `.github/copilot-instructions.md`
- `pyproject.toml`
- `.pre-commit-config.yaml`
- `.github/workflows/pytest.yaml`
- `.github/workflows/pylint.yaml`

Based on the current framework, rewrite the existing agent with another one.


# Project info

llm_ide_agent.py should implement a LLMIDEAgent class that inherits default agent. It specifically has a mamoey structure, as elaborated below:

# Memory

It has a memory, including the following:

- Code Context
- Operation History

Global variables:

- n_operations: int # number of completed operations

## Code Context

The code context memory is maintained by labeling chunks of files of source code. Each chunk is stored in the following data format:

{
    file_path: <relative_file_path>
    class: <name_of_class>
    function: <name_of_function>
    whole_function: bool, 
    lines: List[int] # a list of line numbers
    activity: {
        accessed: List[int] # a list of int, grows as the n_operations increments. It stores 0 if this is not accessed (opened) by the new op, and 1 otherwise.
        referred: List[int] # a list of int, grows as the n_operations increments. It stores n if this chunk is referred to in the reasoning step for the new op (will be specified later).
        score: a referral score computed based on `accessed` and `referred`. It is updated when rendering code context for a new round of query.
    }
}

Define proper dataclasses to wrap up this data structure.


## Operation History

It should be a list of operation nodes. Each operation history unit should have the following:


### Should be filled by the agent in the current response
- thought: str, the text of thought of why to execute this. It should include the reflection of the previous action (if there is one), and the reasoning of why to take the next action. This should be grounded with references to code lines, in the format of `<text>[<ref_index>](<file_path>:<line_number>)`.
- action-type: the executed action type (will specify later)
- action-params: the params of the action (will specify later)
### Should be filled by the execution of the action
- observation: the output of the action
### Should be filled by the agent in its next response to 'reflect' on the previous onr (if there is any) based on the observation
- bad_action: bool, True if the observation indicates the action is invoked incorrectly and needs to be fixed, or the hypothesis cannot be clearly supported or refuted and needs to be replaced by another one. Otherwise False, indicating the reasoning logic can be continued.
- sumary: str, a few sentences to summarize what this action did and what information it retrieved through the observation.
- lessons: str, lessons learned from the previous action, including the mistake that should avoid to make when calling a tool or making a hypothesis. Seperate each piece of lession by new lines.

When executing each action, compute the `accessed` and `referred` value for each source code chunk in code context memory, and update the `activity` entity for each.


The nodes should be maintained as a tree structure.

If `bad_action` is False, the new node should be the child of the previous one.
If True, the new node should be the sibling of the previous one.

Create a config file `src/debugmaster/config/llm-ide/swebench.yaml`, which takes over the one in `src/debugmaster/config/extra/swebench.yaml`.

It should introduce an entry "tools" under "environment". Each 'tool' entry includes:

- name: the name of this tool
- source: the local path of the package that needs to be copied. Empty if no files needs to be copied
- target: the target location in the container that the package needs to be copied. Empty if no files needs to be copied
- installation_script: a script in string to install the tool
- command: the command (without arguments) that can run the tool
- usage: a block of text that is displayed to the agent to show how to use the tool

`src/debugmaster/environments/docker.py` should process the installation of tools based on the latest format above. Keep `setup_reproduction_script` and `reproduction_script`.


# Prompting

This mode does not append all conversation history. Instead, each iteration should use the following prompt format:

## Task Description
task description, the same as default swebench setting.
## Code Context
First, compute the referral score using a decay function similar to:
$$S_{f} = \sum_{i=1}^{M} (\alpha \cdot A_i + \beta \cdot R_i) \cdot \gamma^{(M-i)}$$
$A_i$: Binary indicator if function was Accessed in step $i$.
$R_i$: Frequency of References in reasoning in step $i$.
$\gamma$: Decay factor (e.g., $0.9$) to ensure older operations carry less weight than recent ones.

Then, arrange the code context in the following format:

### File: `<file_path>`
Under a file path, if a *referral score of a chunk is greater than a threshold*, display the line numbers and code of the lines specified in the code chunk.
If `whole_function` is true, just display the whole function definition with line numbers, no need to extract based on line numbers in `line`.
Use '...' to fill gaps between unconnected lines.

Important: 
Always display the signature lines of class and functions specified in the chunks. Use tree-sitter to analyze the corresponding line numbers.
If lines figured out in `lines` lies in a loop/if block, but the block declaration lines (e.g. `while ...: / if ... / elif ... / for ...:, etc`), include the declaration lines as well.
These signature lines and loop/if block declaration lines does not need to be stored in `lines` of the chunk. Should be a temp value used to render code context.
If the signature lines and loop/if block declaration lines are not connected with the lines figured out in `lines`, use '...' to fill gaps.

Move on to the next file after completing all chunks in one file. No need to use indicator to seperate chunks. 


## Operation History
Extract the reasoning chain from the tree constructed in operation history memory. Starting from the root node, the chain should involve *every* node that has a child and then move on to its child. Otherwise, move on to its sibling. Always involve the *last* node in the tree.

Put the content of each node here, in the order of the reasoning chain.

## Tool Usage

Put the tool usage specified in the config files here.

## Lessons Learned

Put lessons learned by each nodes in the decision tree here (do not consider they have child or not).

## Response Format

For each operation (excluding the first one that has no previous operations), the agent should prompt twice:

1. reflection on the previous action's observatoin

<bad_action>...</bad_action>
<lessions>...</lessons>
<summary>...</summary>

2. Prompt the second time asking for the following:
# if bad_action is True, it is for the alternative action;
# if bad_action is False, it is for the next-step action;
# Process two different prompt templates and choose which one to use based on `bad_action` value.
<thought>...</thought>
<action_type>...<action-type>
<action-params>...<action-params>

`src/debugmaster/config/extra/swebench.yaml` should be updated with prompt templates used in this agent design.
Never hard-code prompt templates in source code.

Leave the tool handling part not implemented, this is the next step.

Make sure `llm_ide_agent` is the default agent when running:

mini-extra swebench \
  --subset verified \
  --split test \
  --model tensorblock/gpt-5-mini \
  ...

and

mini-extra swebench-single \
  --subset verified \
  --split test \
  --model tensorblock/gpt-5-mini \
  ...



# Tools

`congif/tools/<tool_name.yaml>` should define how a tool can be installed, it includes the following:

- tool_name: tool name.
- version: version number. Only useful if not installing from source.
- source: the source folder on host machine that needs to be copied into the container, or None.
- py_standalone: a python version, or None.
- installation: only support 'pip' at this time.
- setup_script: a script to set up the tool after installation success. It can be a template.
- executable: a string, indicating how the tool should be called within the container.
- usage: a block of string, indicating the usage.

If `source` is set, copy the local folder to container machine: /tools/<tool_name>
if `py_standalone` is set, create a new conda env using `conda create -n {tool_name} python=<version> -y`, verify creation success
If installation is `pip`,
 - if `source` is set, `cd` to `/tools/<tool_name>` first, <tool> should be '-e .'. Otherwise, do not cd, and <tool> should be <tool_name>==<version>.
 - if `py_standalone` is set, run `conda install -n <env_name> <tool> -y`. Otherwise, run `conda install <tool> -y`.
executable: to run the tool, 
 - if `py_standalone` is set, use `conda run -n <env_name> <executable> <arguments>`. 
 - Otherwise, use `<executable> <arguments>`.
 For example, for debugger, the setup script refers to `init_debugger_task` in `environments/utils/debugger.py`; the executable can be `python -m rdb.client` (the same as `debug`, let's use the very initial command here).
usage: only used to append to prompts.

Make debugger as one tool installed in this way, and refactor the code for debugger support to general tool support.



