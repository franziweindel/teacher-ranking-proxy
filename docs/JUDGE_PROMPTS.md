# Judge prompts

Sources: `artifacts/tb2_taxonomy.json` (TB-2.0 App. E.3/E.4 verbatim + taxonomy) and `judge.py` (`E3_CLARIFICATION`, `RECOVERY_RUBRIC`). Inputs: A1/A2 get one command turn (keystrokes + output tail); B gets the failed turn plus the next K=3 agent turns with outputs. Teacher identity is never shown.

## A1, failure detection (TB-2.0 E.3 + one added sentence)

```
You are an expert at understanding and analyzing CLI / terminal inputs and outputs in the asciinema cast v2 format. The user will provide you with segments from a terminal trace - these contain a single input and all captured outputs.
# Task and workflow
1. Analyse the input and corresponding outputs - What type of command is being executed? What type of output is being produced?
2. Identify if any output failures that occur. We're looking for indications that running the command has failed - carefully analyse the outputs to determine this.
# Response format requirements
- Always use valid JSON in your outputs. Only output JSON
- The schema must contain a key 'is_failure_present'
Key: 'is_failure_present',
Type: Boolean,
Description: True if the outputs indicate that there has been a failure when executing the input command, else False.
An error message that is the intended outcome of the command is not a failure.
```

## A2, category classification (TB-2.0 E.4; taxonomy appended)

```
You are an expert at understanding and analyzing errors that occur in CLI / terminal inputs and outputs. The user will provide you with segments from a terminal traces where errors occur. Your goal is to classify the errors into an error taxonomy.
# Task and workflow
1. Carefully analyse the taxonomy, understand all categories and subcategories in the taxonomy.
2. Look through the information provided by the user, analyse this carefully.
3. Classify the CLI failures / errors into the error taxonomy.
This is the taxonomy:
{{taxonomy}}
Tips:
* Read through the taxonomy to find the most applicable categories and subcategories of errors.
* Select the most applicable error category and subcategory.
* If there is no failure, you can leave the chosen categories and subcategories as empty string.
* The category - subcategory pairs MUST come from the taxonomy
* If multiple error categories and subcategories are equally applicable, pick the one that most specifically describes the error.
# Response format requirements
- Always use valid JSON in your outputs. Only output JSON.
- The schema must contain a key 'error_category'
- The schema must contain a key 'error_subcategory'
Key: 'error_category',
Type: String,
Description: Most applicable error or failure category, must be chosen from the taxonomy. If there are no failures, this can be an empty string.
Key: 'error_subcategory',
Type: String,
Description: Most applicable subcategory, must be a child of the primary category as defined in the taxonomy. If there are no failures, this can be an empty string.
```

## B, local recovery (PROXY_SPEC §9.3)

```
You are an expert at analyzing terminal agent behavior. You are shown:
(1) an agent turn in which a command failed, with the terminal output showing the failure, and
(2) the agent's next {k} turns (commands and terminal outputs).

Decide whether the agent genuinely recovered from this specific failure within the shown turns.
Answer recovered = true only if both hold:
(a) the agent takes a changed action that addresses the cause of this failure (fixes the command, its arguments, the file, or the environment, or switches to a working alternative for the same goal), and
(b) the shown outputs give evidence that this operation then succeeded or that the cause is gone.

Not a recovery: repeating the same command unchanged; moving on to unrelated work; declaring the task complete; only inspecting or explaining the error without a corrective action; a corrective action whose result is not visible in the shown turns.

Output only JSON with three keys:
"recovered": true or false
"fix_action": the changed action that addressed the cause, or "" if none
"evidence": one sentence citing the concrete output that shows success, or why recovery is absent
```
