# TechPilot — Job Intelligence Prototype Closeout

> Status date: 2026-08-24  
> Decision: **CLOSED AS A BUSINESS PROTOTYPE — NOT A PRODUCT GATE PASS**  
> Next direction: **AI Coding**, but product differentiation must be frozen before implementation.

---

## 1. Executive decision

The Job Intelligence work is closed at the prototype/business-validation stage.

This is not a claim that the product is complete. The final state is:

```text
Flow A  intent -> real jobs -> JD analysis/recommendation
        validated on two Chinese public recruitment sources

Flow B  resume -> real job recommendation
        contract/focused-tested, real E2E not closed

Flow C  resume + JD -> fit/gap analysis
        contract/focused-tested, real E2E not closed

BOSS    important source, but stable unattended discovery/full-JD acquisition
        was not solved without turning the project into recruitment-site crawling work
```

The project therefore stops before spending more engineering time on anti-bot/page-structure adaptation.

The reason is product-level, not merely technical:

> If a job recommendation product cannot independently discover enough relevant jobs, source coverage becomes the dominant bottleneck. Continuing to optimize extraction/matching while the source layer is structurally weak produces misleading progress.

---

## 2. Frozen product boundary

P5 Job Intelligence and P3 Code RAG are permanently separate capabilities.

There is **no** pipeline of:

```text
JD requirement
-> search my repository
-> prove I have the capability from code
```

and this must not be described as a future P5 plan.

The three Job Intelligence product flows are:

```text
A. User intent / requirements
   -> discover real jobs
   -> structure JDs
   -> filter / rank
   -> recommend jobs + reasons

B. Resume
   -> candidate profile
   -> discover real jobs
   -> structure JDs
   -> profile <-> JD matching
   -> rank
   -> recommend jobs + matching reasons

C. Resume + specific JD
   -> structure both
   -> requirement <-> candidate capability matching
   -> fit score
   -> satisfied items
   -> gaps
   -> explanation
```

Code RAG remains an independent repository-understanding capability.

---

## 3. What was implemented

### JD extraction

Implemented:

- Pydantic structured JD schema;
- DeepSeek structured extraction;
- schema validation;
- at most one bounded model repair;
- deterministic skill normalization;
- exact evidence-to-source binding;
- deterministic evidence rebind when the model returns correct evidence text with incorrect offsets;
- fail-closed behavior for missing or ambiguous evidence;
- per-job analysis failure isolation.

A key real-data lesson was that LLM-generated character offsets are not authoritative.

The resulting contract became:

```text
LLM proposes evidence text
-> application binds it back to authoritative JD text
-> application owns exact offsets
-> validation
```

The validation gate was not weakened to accept fuzzy hallucinated evidence.

### Real job discovery / China validation

Implemented bounded real-source validation for:

- Ashby public job boards;
- Nowcoder public job pages;
- Shixiseng public job pages;
- BOSS public listing probe;
- experimental BOSS browser capture connector.

Source errors were separated from legitimate `0 match` results.

Same-run snapshots were introduced so volatile recruitment pages were fetched once per validation run and reused for structural evaluation and Flow A.

### Matching / ranking baseline

Implemented:

- resume/candidate-profile boundary;
- JD matching baseline;
- ranking;
- recommendation explanation fields;
- required/preferred coverage direction;
- multi-source health/failure-isolation experiments.

These are not treated as production-quality matching until B/C E2E is closed.

---

## 4. Real-business evidence

### Nowcoder

Observed real validation:

```text
live collection                    10 / 10
real Chinese JD selected            5
structured extraction success       5 / 5
evidence binding success            5 / 5
model repair count                  0
deterministic rebind count          5
query discovery matches             5
Flow A successful analyzed jobs     5
Flow A analysis failures            0
full regression                     100% at this checkpoint
```

Interpretation:

- public source acquisition worked;
- Chinese JD structuring worked;
- evidence grounding worked;
- query -> real jobs -> JD analysis Flow A worked.

Nowcoder Flow A is a real-business PASS for this bounded source/sample.

### Shixiseng

Observed real validation:

```text
live collection                     8 / 10
candidate real JDs                  8
evaluated until target reached      6
structured successes                5
structural failures                 1
evidence binding successes          5
model repair count                  0
query discovery matches             4
Flow A successful analyzed jobs     4
Flow A analysis failures            0
```

The structural gate was corrected from the invalid rule:

```text
"the first arbitrary five internet JDs must all succeed"
```

to:

```text
evaluate bounded current real cases
-> require five successful grounded cases
-> retain real failures in the report
```

This separates a validation sample target from the unrealistic claim that every live internet input must succeed.

Shixiseng Flow A is a real-business PASS with retained failure evidence.

### BOSS direct HTTP

Observed:

```text
0 jobs collected
security challenge encountered
```

The system correctly refused to bypass the challenge.

This is a source-access limitation, not a JD-extraction failure.

### BOSS browser connector

v8 proved that an authenticated normal browser session could capture visible BOSS listing data:

```text
total jobs       18
listing jobs     18
full JD jobs      0
```

This demonstrated listing-level capture, but not reliable full-JD acquisition.

A later v9 experiment attempted automatic DOM/right-pane capture. Its installation stopped after extension files were copied because the apply script incorrectly treated local `node` as a hard validation dependency:

```text
node: command not found
```

Therefore v9 is **not validated** and must not be cited as completed capability.

The local extension tree may be partially overwritten by v9 and should be cleaned before the next code milestone/commit.

---

## 5. Resume / Flow B / Flow C failure evidence

The synthetic TechPilot resume PDF successfully passed the PDF text-reader smoke step:

```text
resume_chars = 1968
```

But real E2E stopped during candidate-profile extraction:

```text
ResumeExtractionValidationError:
resume evidence span does not bind to source text
```

and then:

```text
ResumeExtractionError:
resume extraction failed after one bounded repair
```

Therefore:

```text
Flow B real E2E = NOT CLOSED
Flow C real E2E = NOT CLOSED
```

This failure must not be hidden behind focused tests.

Likely failure class:

```text
PDF-rendered text / Unicode / punctuation / line-break normalization
vs.
model-proposed evidence text
```

The correct future fix would be deterministic canonical normalization with original-offset mapping, not relaxing the evidence gate.

Because the Job Intelligence product line is being frozen, this is recorded as an unresolved known limitation rather than immediately optimized.

---

## 6. Why BOSS changed the product decision

BOSS is strategically important for Chinese job coverage.

However, continuing the current direction would shift the project's center of gravity from applied AI engineering to recruitment-site acquisition engineering:

```text
DOM selectors
browser-extension maintenance
session state
security challenges
page changes
anti-bot behavior
```

That work can be legitimate in a dedicated data-acquisition product, but it is not the highest-value direction for TechPilot's intended AI-engineering portfolio.

A browser connector also exposed a product contradiction:

```text
if the user must already search/browse the jobs manually,
the system is helping organize discovered jobs
rather than independently finding the right jobs.
```

That can be a supporting feature, but it is too weak to justify Job Intelligence as the project's next major technical phase.

The correct decision is therefore to freeze the prototype instead of pretending source coverage is solved.

---

## 7. Final P5/Job Intelligence status

Use this status in all active project documents:

```text
JOB INTELLIGENCE PROTOTYPE:
CLOSED WITH REAL-BUSINESS EVIDENCE AND KNOWN PRODUCT LIMITATIONS

Flow A:
PASS on bounded real Nowcoder + Shixiseng validation

Flow B:
NOT CLOSED — resume E2E evidence-binding failure

Flow C:
NOT CLOSED — depends on resume extraction E2E

BOSS:
CORE SOURCE REQUIREMENT IDENTIFIED
BUT STABLE UNATTENDED FULL-JD DISCOVERY NOT SOLVED

Overall:
NOT A PRODUCT GATE PASS
NOT PRODUCTION-READY
```

Do not describe P5 as fully complete.

Do not describe BOSS as integrated full-JD production source.

Do not describe synthetic resume validation as real-candidate validation.

---

## 8. What this phase is useful for in interviews

A defensible explanation is:

> I built a real Job Intelligence prototype rather than stopping at synthetic structured-output tests. On live Chinese recruitment pages, the system collected and structured real JDs, bound model evidence back to source text, and ran intent-to-job recommendation on both Nowcoder and Shixiseng. Real data exposed several failures that unit tests did not: unreliable LLM character offsets, repeated live fetches being misclassified as no-match, one bad JD crashing a batch, and source-access limitations on BOSS. I fixed the system-level issues but stopped before turning the project into an anti-bot/scraping project. Flow B/C remained open because a synthetic-resume E2E exposed an evidence-binding failure.

This phase demonstrates:

- business validation before capability closure;
- distinction between source acquisition and model quality;
- evidence-grounded structured extraction;
- failure attribution;
- bounded repair;
- partial-failure isolation;
- willingness to stop a technically interesting path when product economics are weak.

---

## 9. Claims that must not be made

Do not claim:

- “P5 is fully complete.”
- “TechPilot supports all major Chinese recruitment sites.”
- “BOSS full-JD integration is complete.”
- “Resume recommendation E2E passed.”
- “Flow C matching was validated on a real candidate.”
- “All real JDs extract successfully.”
- “The Job Intelligence module proves skills from repository code.”
- “Synthetic/assistant-curated evaluation is human-reviewed Golden.”

---

## 10. Transition decision: AI Coding

The next project direction is AI Coding because it reuses the strongest completed TechPilot capabilities:

```text
Code RAG
RepositoryReadBoundary
symbol / module / call-relationship tools
authoritative read_file materialization
ToolRuntime / ToolRegistry
EvidencePack
Research control loop
failure handling
evaluation discipline
```

But implementation must **not** start with:

```text
add write_file
add shell
add patch tool
build a coding agent
```

The first gate is product differentiation.

The next session must answer:

> Why should a user use TechPilot AI Coding instead of Codex, Claude Code, Cursor, or another mature coding agent?

If the answer is only:

```text
"ours also searches code, plans, edits and runs tests"
```

then the product direction is not differentiated enough to justify implementation.

---

## 11. AI Coding pre-implementation gate

Before writing the AI Coding system, freeze a product thesis containing:

1. **Target user / task**
   - which coding workload is underserved?

2. **Differentiated capability**
   - what does TechPilot do materially differently from Codex/Claude Code/Cursor?

3. **Existing unfair advantage**
   - which already-built TechPilot capabilities create that difference?

4. **Evaluation**
   - which real repository tasks can prove the difference?

5. **Non-goals**
   - what will TechPilot explicitly not compete on?

Candidate directions may be explored, but none are accepted yet. Examples include:

- evidence/audit-first coding for high-trust repository changes;
- repository research + change-risk analysis before edits;
- constrained/permissioned coding harness for enterprise-style boundaries;
- evaluation-first agent development where every change has traceable evidence and failure attribution;
- specialized multi-repository or unfamiliar-codebase investigation tasks.

These are hypotheses, not the final answer.

The next session should compare them against current Codex/Claude Code/Cursor capabilities before selecting one.

---

## 12. Working-tree hygiene before next implementation

No Git write is authorized by this closeout.

Before the next implementation sprint:

```text
1. inspect git status
2. identify the partially applied BOSS v9 extension state
3. restore/remove only the incomplete v9 changes
4. preserve validated P5 prototype work
5. run git diff --check
6. run the agreed regression set
7. only then decide what should be committed
```

Do not mix the incomplete v9 experiment into the first AI Coding commit.

---

## 13. Final transition

Project narrative after this closeout:

```text
P0-P2
Document RAG / grounded answering

P3
Code RAG / repository understanding

P4
bounded Research Agent / Thick Harness

Business experiment
Job Intelligence
-> real-source validation
-> source/product limitations discovered
-> prototype frozen

Next
AI Coding
-> differentiation thesis first
-> implementation second
```

The Job Intelligence work is not discarded. It becomes evidence that TechPilot's development process includes real-business validation, failure analysis, and product-level stopping decisions rather than only benchmark optimization.
