Based on the current framework, rewrite the existing agent with another one.

Under `src/debugmaster/agents`, create `code_context_manager.py`, `action_manager.py`, and `llm_ide_agent.py`.

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


# Reflection & Action

Refactor the reflection and action of the agent in the following manner:

For each iteration, the agent responses in reflection-action manner.

## Reflection

If there are previous actions in operation history, first prompt the agent to reflet about the past observation

- it is *admissible* if the action executed correctly and produced a reliable result that can be incorporated into ongoing reasoning
- it is *non-productive* if it cannot be used to extend the current reasoning chain, either because the action did not execute as intended (e.g., command errors, timeouts, or tool malfunctions), or because it executed correctly but yielded no informative evidence (e.g., redundant, irrelevant, or vacuous results) that would constrain future decisions or eliminate alternatives.
- it is *dead-end* if it reliably indicates that the agent has reached a dead-end state under the current commitments.

Then prompts the agent to conclude the *lessons learned* from the observations, including why the action command malfunctioned and how to avoid it in future actions; why the observation yielded no informative evidence and what should be avoided in future actions. 

Response format:

<outcome>admissible, non-productive, or dead-end (define enums)</outcome>

<lessons>Write one concise sentence for each lesson, one lesson in a separate line<lessons>


## Action

The agent should point out whether an action is deterministic or not. A *deterministic* action means that, under the current commitments, it is the only admissible continuation and introduces no alternative branches. A *non-deterministic* action means that multiple admissible alternatives exist, and selecting the action commits the agent to one branch among several possible continuations.

Response format

<property>deterministic or non-deterministic (define enums)</property>
<thoughts>the reasoning of why to take this action</thoughts>
<action>the action command</action>

Note that we no longer need action_type and action_params.

## Flow

If the outcome of an observation is *admissible*, natrually ask it to propose a new action, and make the new action the child of the previous one.

If the outcome is *non-productive*, put this action into the `non-productive` lists of the previous one. Ask the agent to propose a new action to replace it, following the previous one.
If there are continuously M non-productive actions proposed based on one action, force to make the previous action a *dead-end*.

If the outcome is *dead-end* or forced to become dead-end, backtrack to the cloest action which *property* is *non-deterministic*.
First prompt the llm to summarize the dead-path, from the non-deterministic action to the dead-end, append the summary to the 'summary' attribute of the non-deterministic action that leading the dead-path.

Then ask the agent to propose a new alternative of this non-deterministic action (based on the entire path to the non-deterministic action and the summary of the dead-paths (there may be multiple dead-paths over time)), and put the new action as the *sibling* of the non-deterministic one. It this new action resulted in a *dead-end*, back-track again, until getting the root action.


# Redesign

Curreently, the agent prompts the LLM twice per interaction. It is a waste of token. Redesign the agent so that it prompts the LLM once per interaction. For now, the agent won't classify an incoming operation as "dead-end". Use the logic that M consecutive rejected operations yield a dead end. Leave the logic of processing a dead end a placeholder or NotImplemented.

First, modify the `general input` template:
(Move task_description_template and tool_usage_template into system message)

```
    You are an interactive debugging assistant that iteratively interacts with a computer shell to solve programming tasks. Your objective is to identify the root cause of a software issue and resolve it efficiently and systematically.

    # Debugging Task

    {{task_description_template}}

    # Available Tools

    {{tool_usage_template}}
    
    You are provided with the following information:
    
    - *Code Context*. This section contains all code snippets accessed during previous debugging operations. These snippets are up-to-date and reflect the modifications made during previous debugging operations. **Note**: Do not retrieve code snippets that are already present in the Code Context.

    - *Rejected Operations and Lessons Learned*. This section tracks unsuccessful debugging operations. It lists proposed operations that failed to yield useful observations due to incorrect tool usage or retrieval errors. Each entry includes lessons to help you avoid repeating these mistakes.

    - *Code Changes. This section shows a cumulative diff comparing the current state of the codebase to the original version. This provides a clear view of the modifications made so far.

    - *Operation History*. This section lists the sequence of **successful** operations since the debugging task began. Each operation retrieves useful information that contributes to the reasoning chain leading to resolving the issue.
```

reflection_instructions (current `reflection_system_template`) should be modified as: 

```
    - *Incoming Operation*. This describes the actions and observations of an incoming new action.

    # Rethink the incoming operation

    Your are expected to determine whether the observations from the incoming operation help reveal the root cause of the software issue, based on the reasoning chain constructed from the successful operations in the Operation History section.

      - If the new operation successfully retrieves the expected information and contributes to identifying the root cause of the software issue, **keep** the incoming operation in the reasoning chain.
    
      - If the operation crashes, malfunctions, or fails to execute properly, or the observations do not contain the expected information and do not contribute to identifying the root cause, **drop** the operation.
    
    Enclose your decision within a pair of `<decision></decision>` tags.

    Write a concise summary of the incoming operation. Include what information you intended to retrieve, which tool you used, what you observed from the output, and why or why not the observation successfully retrieved the expected information and contributed to identifying the root cause of the software issue. The summary must accurately reflect both the actions taken and their resulting observations. Enclose the summary within a pair of `<summary></summary>` tags.

    Finally, extract the lessons learned from the incoming operation if you decide to **drop** it. For example, explain why the command malfunctioned and how to avoid similar issues, or why the observation did not provide useful evidence and what should be avoided in future debugging operations. Place the lessons within a single pair of `<lessons></lessons>` tags.
```

action_instructions (current `action_system_template`) should be modified as:

```
    # Propose a new action

    Propose the next operation based on the existing ones in the Operation History section. To accomplish this task, you should:

    1. Review the sequence of successful operations in the Operation History section. Try to infer the root cause of the software issue based on their observations, and identify what additional information is still needed to confirm it. This may include *static* information (e.g., the implementation of a function) and *dynamic* information (e.g., the runtime value of a variable or expression). While your next operation does not need to gather all missing information, it should retrieve something that extends the reasoning chain in a concrete, evidence-driven way.

    2. Refer to the Code Context section to read relevant code snippets. Do not repeatedly access snippets that are already included there just to check for updates. The Code Context already reflects the latest modifications from previous operations. Use the Current Code Change section to see how the source code has been modified.

    3. Refer to the rejected operations and the lessons learned from them. Make sure your proposed new operation avoids repeating the same mistakes.

    4. Carefully read the usage instructions, examples, and “pro tips” for the available tools, and choose the most appropriate one to accomplish your objective. Specifically, you must use the `get_code_context` tool to access code snippets. If you use common bash commands such as `cat` or `nl`, the retrieved code will not be added to the Code Context section.

    You may choose to *submit* if you have successfully edited the source code so that the script reproducing the reported issue now passes, and there are no test failures **related to your fix**. If there are pre-existing test failures that are clearly unrelated to the code you modified (for example, due to test setup, environment, or compatibility issues), you do not need to rerun the test suite repeatedly or try to fix those unrelated failures.

    Execute one action in each operation. Put the command of the action command inside a pair of `<action></action>` tags.

    Consider how your proposed operation contributes to the reasoning chain built from the existing successful operations. Classify the operation as either `exploitative` or `exploratory`:
    
      - An operation is **exploitative** if it attempts to validate the current hypothesis by gathering additional evidence grounded in prior observations. For example, if you suspected an issue with a function's behavior in a previous operation, and you now propose to collect more evidence to confirm or refute that suspicion, the operation is exploitative.
    
      - An operation is **exploratory** if it selects one direction among multiple plausible alternatives in order to reduce uncertainty. For example, if you identify several functions as potentially suspicious and decide to begin investigating one of them, that operation is exploratory.

    Enclose the classification of your proposed operation between a pair of `<property></property>` tags.

    Write a few sentences explaining why you chose to take this action. Enclose your reasoning within a pair of `<thoughts></thoughts>` tags.
```


If there is an incoming action, use the following system message:

```
    {{general_input}}

    {{reflection_instructions}}

    {{action_instructions}}

    {{response_format}}
```

If there is no incoming operation (first operation), use the following:

```
    {{general_input}}

    {{action_instructions}}

    {{response_format}}
```

response_format should be:

```
    # Response Format

    Strictly follow the response format listed below:

    ```
    {% if incoming_action -%}
    # Rethink the incoming action

    <decision>keep or drop</decision>

    <summary>summary of the incoming operation</summary>

    <lessons>lessions learned if decided to drop</lessons>

    {% endif %}
    # Propose a new action

    <property>exploitative or exploratory</property>

    <thoughts>your thoughts</thoughts>
    
    <action>action command</action>
    ```
```

Change `non-productive`/`admissible` terms (Enum) to a boolean attribute `valid`. Use this to decide whether to put it into the reasoning chain, or the `invalid_ops` of the parent node.

Make the source code under `src/debugmaster/agents/llm_ide` clean. Make sure one function does its own job, and create helper functions if there is logic focusing on a sub-process within a long function. You can create a few utility modules under `src/debugmaster/agents/llm_ide` to make sure each module (python file) include the classes and functions related to its funcionality.


# Diagnosis

You are a professional prompt engineer. You run debugmaster with llm-ide agent on two SWE-bench cases:

```
python -m debugmaster.run.mini_extra swebench \
  --subset verified \
  --split test \
  --model openai/gpt-5-mini \
  --output /home/ruixinw/debugmaster_artifact/aclarr_experiments/experiments_output/llm-ide-debug \
  --environment-class docker \
  --workers 4 \
  --config llm-ide/swebench_fl_debug \
  --filter "sympy__sympy-16597|astropy__astropy-12907"
```

Each time you run a new experiment, remove old experiment output folder `/home/ruixinw/debugmaster_artifact/aclarr_experiments/experiments_output/llm-ide-debug`.

The readable prompts and responses of each iteration is stored under `/home/ruixinw/debugmaster_artifact/aclarr_experiments/experiments_output/traj_readable/llm_ide`.

The problem is, with `gpt-5-mini`, the model fails to follow the system instruction to do systematic debugging. Instead, it keeps uses code search.

Read the current implementation of agent and prompt templates, read the prompts and responses of the experiments with gpt-5-mini, and diagnose why the model fails to follow the instructions. Try your best to improve the prompt template (src/debugmaster/config/llm-ide/swebench_fl_debug.yaml). Do not modify `src/debugmaster/config/llm-ide/swebench_fl_debug.yaml.bak` since it is a backup file.

Iterate examine -> improve -> run experiment loop for up to 10 iterations, or stop when you resolved this issue.
