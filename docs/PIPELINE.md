# Build an Automated Teacher-Ranking Literature Mining Pipeline

Build a reproducible pipeline that automatically searches the literature for **published agentic SFT experiments that provide teacher-ranking ground truth without requiring us to rerun SFT**.

The pipeline itself should perform literature screening and extraction using LLM calls. Your role is to **implement, test, and validate the pipeline**, not to manually curate the final papers or decide scientific inclusion yourself.

I have API access to **OpenAI and DeepSeek**. Make the LLM backend configurable.

---

## 1. Goal

We want to discover experiments of the following form:

```text
same student
+ same or closely controlled tasks/data
+ trajectories generated separately by multiple teachers
+ student SFT'd separately on each teacher's trajectories
+ downstream score reported for each resulting student
=
published teacher ranking for that student
```

These published SFT outcomes will later serve as ground truth for evaluating teacher-selection proxies.

For example, we ultimately want records such as:

```text
student: Qwen3-8B

teacher ranking:
1. Teacher A
2. Teacher B
3. Teacher C
4. Teacher D

task dataset: ...
teacher trajectory dataset: ...
evaluation benchmark: ...
published downstream scores: ...
source: paper/table/section
```

The pipeline should search broadly enough to find relevant experiments in papers, project pages, technical reports, and associated public artifacts.

---

## 2. Inclusion criteria

For every candidate experiment, independently evaluate:

```text
1. agentic / interactive trajectories are used
2. tasks or environments are public
3. >= 2 teacher models generate trajectories
4. preferably the same tasks are used across teachers
5. teacher identity is known for each trajectory
6. teacher trajectories are publicly available
7. the same pre-SFT student is trained separately on each teacher dataset
8. SFT recipe and data amount are controlled across teachers
9. downstream evaluation scores are reported separately for every teacher
```

Do not silently convert uncertainty into `yes`.

Each criterion must be classified as:

```text
yes
no
unclear
```

with supporting evidence.

---

## 3. Experiment classification

Classify each discovered experiment as:

```text
gold
    satisfies all important criteria and public tasks + teacher trajectories
    are directly usable

silver
    valid published teacher-ranking experiment but has a limitation,
    e.g. only 2 teachers, artifact reconstruction required, or one criterion
    is imperfect

results_only
    controlled per-teacher SFT results exist, but the trajectories/tasks
    required for our proxy evaluation are not publicly available

reject
    does not provide a valid controlled same-student teacher comparison
```

Classification must be derived from the structured criterion results, not from an unconstrained LLM opinion.

---

## 4. Automated discovery pipeline

Implement approximately:

```text
candidate discovery
      ↓
paper / webpage retrieval
      ↓
LLM relevance screening
      ↓
LLM experiment extraction
      ↓
artifact discovery and verification
      ↓
LLM evidence-based classification
      ↓
validation
      ↓
CSV + JSON export
```

### 4.1 Candidate discovery

Search broadly across sources such as:

```text
arXiv
Semantic Scholar / OpenAlex if useful
citation/reference graphs
project pages
technical blog posts
GitHub
Hugging Face
```

Use combinations of search concepts such as:

```text
teacher trajectories
multiple teachers
teacher model ablation
teacher selection
teacher-generated data
knowledge distillation
SFT
supervised fine-tuning
trajectory distillation
agent
agentic
terminal
SWE
software engineering agent
tool use
interactive environment
same tasks
trajectory dataset
```

Do not depend on one exact keyword query.

Support citation/reference expansion from high-value seed papers.

Seed papers can include known examples such as:

```text
Terminal-Lego
arXiv:2606.03461
```

but the discovery logic must not be hard-coded specifically to them.

---

## 5. LLM-based screening

The **runtime LLM**, not the coding agent, should decide whether a candidate paper appears relevant.

Supply the relevant paper text to the LLM and request strict structured output.

Example:

```json
{
  "agentic": {
    "decision": "yes",
    "evidence": "The agents interact with executable terminal environments...",
    "source_location": "Section 3.2",
    "confidence": 0.97
  },
  "multiple_teachers": {
    "decision": "yes",
    "evidence": "...",
    "source_location": "Table 4",
    "confidence": 0.99
  }
}
```

The LLM must be instructed to:

```text
use only supplied source material
never fill missing fields from memory
return unclear when evidence is insufficient
distinguish explicit evidence from inference
give a source location for every important claim
```

---

## 6. Experiment extraction

A single paper may contain **multiple qualifying experiments or multiple students**.

Extract each experiment separately.

For every student × experiment, extract:

```text
student model
student Hugging Face ID, if verified
base/instruct/post-trained status

teacher models
number of teachers

task/environment dataset
task dataset Hugging Face ID
environment repository

whether tasks are matched across teachers

teacher trajectory dataset
trajectory Hugging Face ID
whether teacher identity is recoverable per trajectory

number of tasks
number of trajectories per teacher
training-data budget

SFT recipe
whether the recipe is controlled across teachers

evaluation benchmarks

downstream score for every teacher-trained student

teacher ranking implied by those downstream scores
```

Preserve the **actual numerical downstream scores** whenever available.

Do not store only the derived ranking.

---

## 7. Artifact verification

Do not trust statements such as:

```text
"we release the data"
"code is publicly available"
```

without checking the linked artifacts.

Automatically inspect associated:

```text
GitHub repositories
Hugging Face datasets
Hugging Face models
project pages
```

Verify where possible:

```text
artifact exists
dataset is accessible
expected files/configurations exist
teacher identity is represented
multiple teacher subsets can actually be separated
task IDs can be recovered
student checkpoint exists
```

Store the exact artifact identifiers/URLs discovered.

A paper cannot be `gold` solely because it claims artifacts are public.

---

## 8. Multi-model verification

Support:

```text
LLM_BACKEND=openai
LLM_BACKEND=deepseek
LLM_BACKEND=both
```

For important scientific extraction steps, support running OpenAI and DeepSeek independently.

When both are enabled:

```text
agreement      → accept structured result
disagreement   → preserve both outputs and trigger adjudication
```

Do not silently pick one answer.

Adjudication may use an additional LLM call supplied with:

```text
source evidence
OpenAI extraction
DeepSeek extraction
```

and must itself return evidence-grounded structured output.

Make models configurable rather than hard-coding a specific model version.

API keys must be read from environment variables and must never be committed or written to logs.

---

## 9. Evidence provenance

Every important extracted value must retain provenance.

At minimum, store:

```text
source URL / arXiv ID
paper section/table/figure when available
supporting text snippet
artifact URL where relevant
LLM model used for extraction
pipeline version
timestamp
```

In particular, the following may never appear in the final dataset without supporting evidence:

```text
student model
teacher identities
teacher-specific downstream scores
SFT data amount
matched-task claim
public trajectory claim
teacher ranking
Hugging Face dataset/model ID
```

---

## 10. Output schema

Produce:

```text
external_teacher_rankings.csv
```

with one row per **student × experiment**.

Suggested columns:

```text
paper_title
arxiv_id
paper_url
year

experiment_id
status
agent_domain

student_model
student_hf_id
student_model_type

num_teachers
teacher_rank_1
teacher_rank_2
teacher_rank_3
teacher_rank_4

teacher_models_json
teacher_scores_json

task_dataset
task_dataset_hf_id
environment_repo
same_tasks_across_teachers

trajectory_dataset
trajectory_dataset_hf_id
trajectories_public
teacher_identity_per_trajectory

num_tasks
trajectories_per_teacher
sft_data_budget
sft_recipe_controlled

evaluation_benchmarks_json
downstream_scores_json

criterion_results_json

paper_evidence
table_or_section
artifact_evidence
github_repo

notes
```

The JSON score fields are the canonical representation.

Example:

```json
{
  "DeepSeek-V3.2": 31.8,
  "GLM-5": 29.4,
  "Qwen3.5-Plus": 29.1,
  "Claude-Opus-4.6": 24.7
}
```

Derive rank columns automatically from the published numerical scores while preserving ties.

Also save the complete unflattened records to:

```text
external_teacher_rankings.jsonl
```

including evidence and intermediate LLM judgments.

---

## 11. Caching and reproducibility

The pipeline may become API-intensive, so make every stage:

```text
cached
resumable
deterministic where possible
independently rerunnable
```

Cache at least:

```text
search results
downloaded paper text
LLM screening results
LLM extraction results
artifact verification results
adjudications
```

Changing the export format must not rerun LLM calls.

Changing one prompt should rerun only the affected stage.

Record:

```text
LLM provider
model
temperature
prompt version
source document hash
pipeline git commit
```

---

## 12. Pipeline validation

Your responsibility is to validate that **each stage of the automated pipeline works correctly**, not to manually perform the final literature review.

Test:

```text
candidate discovery retrieves relevant papers
PDF/HTML retrieval works
paper text is passed correctly to the LLM
structured outputs validate against schemas
unclear evidence remains unclear
multiple experiments within one paper can be extracted
artifact verification works
citations/evidence survive through export
rankings are deterministically derived from scores
ties are preserved
API failures retry safely
cache/resume works
CSV and JSONL are reproducible
```

Use known papers such as Terminal-Lego as **integration-test fixtures**.

The pipeline should independently recover the expected structure from the source paper.

Do not hard-code known Terminal-Lego values into the extraction or tests merely to make them pass.

The test should instead verify that the automated pipeline can recover required fields from the source.

---

## 13. Manual review queue

Do not force uncertain records into gold/silver/reject.

Create:

```text
review_queue.csv
```

for cases such as:

```text
OpenAI / DeepSeek disagreement
missing teacher-specific scores
ambiguous student checkpoint
uncertain task matching
paper claims data release but artifact cannot be found
teacher identity cannot be recovered from released dataset
multiple plausible interpretations of an experiment
```

Include enough evidence that a human can resolve each case quickly.

---

## 14. Summary output

Also generate:

```text
external_teacher_rankings_summary.md
```

containing:

```text
papers discovered
papers screened
experiments extracted

gold count
silver count
results_only count
reject count

number of distinct students
number of teacher-ranking ground truths

number with >=2 teachers
number with >=3 teachers
number with >=4 teachers

number with matched teacher tasks
number with fully public trajectories

list of datasets immediately usable for proxy evaluation
```

---

## 15. Important scientific constraints

Do not treat the following as equivalent to the target experiment:

```text
different students trained with different teachers
teachers generated different uncontrolled task sets
all teacher trajectories mixed into one SFT dataset
teacher used only as an LLM judge
teacher used only during RL
teacher routing without separate teacher-specific SFT outcomes
different training budgets per teacher without a controlled comparison
teacher quality inferred without downstream SFT results
```

The core ground-truth requirement is:

```text
same pre-SFT student
→ separate SFT run for each teacher
→ controlled training setup
→ separate downstream score for each teacher-trained student
```

Prefer matched tasks and equal trajectory budgets wherever possible.

The ultimate objective is a reusable collection of **published teacher-ranking ground truths for agentic SFT**, together with the public tasks and teacher trajectories necessary to evaluate teacher-ranking proxies without rerunning SFT.
