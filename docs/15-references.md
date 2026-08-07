# References

Every detector here implements a published result rather than a heuristic somebody
liked. This page lists the sources and, for each, **what it actually changed in
this codebase**. A citation that changed nothing is not on this list.

## The core four

**Kaufman, Rosset and Perlich, *Leakage in Data Mining: Formulation, Detection,
and Avoidance*** (KDD 2011 / ACM TKDD 2012).
[Link](https://dl.acm.org/doi/10.1145/2020408.2020496)

Gives the leakage detector its predicate. Leakage is defined as *illegitimate*
information about the target, found by inspecting **how a feature was
constructed**, not by noticing that it predicts too well. That is why Janus walks
the derivation chain instead of looking at feature importances, and why the finding
quotes the chain as its evidence.

**Sculley et al., *Hidden Technical Debt in Machine Learning Systems*** (NeurIPS
2015).
[Link](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf)

Names the failure the blast-radius detector looks for: **undeclared consumers**, a
table acquiring model consumers its owners never agreed to serve. The phrase is
used verbatim in the impact reports, because it is the accurate name for what the
traversal found.

**Breck, Polyzotis, Roy, Whang and Zinkevich, *Data Validation for Machine
Learning*** (MLSys 2019).
[Link](https://mlsys.org/Conferences/2019/doc/2019/167.pdf)

The training-serving skew section is what the drift detector implements: a schema
**fixed at training time**, against which serving data is continuously validated.
It is why drift is a snapshot comparison rather than a reconstructed timeline.

**OWASP Top 10 for LLM Applications** (2025).

The threat model, specifically LLM01 (prompt injection), LLM05 (improper output
handling) and LLM06 (excessive agency). It is why catalog text is wrapped as
delimited untrusted data with its delimiters escaped, why the write surface is a
closed set of parameterized functions, and why writes are gated on a human. See
[10-security.md](10-security.md).

## The reporting surfaces

**Mitchell et al., *Model Cards for Model Reporting*** (FAT* 2019).
[Link](https://arxiv.org/abs/1810.03993)

The section schema `janus model-card` produces: intended use, factors, metrics,
training data. The paper's argument that a hand-maintained card is accurate until
the model next changes is why this one is regenerated from the graph and empty
where the catalog is.

**Pushkarna, Zaldivar and Kjartansson, *Data Cards*** (FAccT 2022).
[Link](https://arxiv.org/abs/2204.01075)

`janus feature-card` is a Data Card for a single feature. Two things it changed
here: the card renders what it could **not** establish as a section of its own
rather than omitting it, which is the paper's known-limitations field applied to
missing metadata rather than to data; and it settled the freshness question, since
a provenance claim has to say when it was true, so the card states its freshness is
measured now and not at training time.

**Regulation (EU) 2024/1689 (the EU AI Act), Articles 10 and 12.**

`janus evidence-pack` maps the graph to these two articles **by paragraph number**,
so a reader can check the mapping rather than trust it. It is why the pack's first
heading is "This is not a compliance certification".

**NIST AI Risk Management Framework 1.0 and its Playbook.**

`janus crosswalk` maps each detector to the subcategory its output is evidence for,
with the subcategory text quoted rather than paraphrased. The table is generated
from the detector registry, so a check cannot be added without appearing in it.

## What shaped the evaluation

**Rabanser, Gunnemann and Lipton, *Failing Loudly: Detecting Dataset Shift***
(NeurIPS 2019).
[Link](https://arxiv.org/abs/1810.11953)

The experimental protocol, perturbation type by magnitude by fraction affected, is
where the freshness sweep comes from. Walking a lag across the SLA boundary rather
than planting one obvious failure is that grid applied to freshness, and it is the
only reason a `>` to `>=` off-by-one is caught.

**Schelter, Rukat and Biessmann, *Jenga: Impact of Data Errors on ML
Predictions*** (EDBT 2021).
[Link](https://openproceedings.org/2021/conf/edbt/p134.pdf)

Its corruption taxonomy shaped how injected failures are thought about. The
library itself is not used: the injections here are metadata-level, and Jenga
corrupts data.

**Grafberger, Guha, Stoyanovich and Schelter, *mlinspect*** (SIGMOD 2021).
[Link](https://stefan-grafberger.com/mlinspect-demo.pdf)

The closest ancestor of this approach: statically reasoning over a pipeline DAG to
find distribution bugs, without instrumenting the pipeline. Janus does the same
over a metadata lineage DAG rather than over a Python program.

## What shaped the governance detectors

**Barocas and Selbst, *Big Data's Disparate Impact*** (California Law Review,
2016).

Proxy variables as the mechanism: a feature that carries a protected attribute's
information without being it. That is why the proxy-candidate detector looks for a
**shared ancestor** (a fork) rather than a derivation (a chain), why it is capped at
medium severity, and why it contributes nothing to the trust score. It raises a
question for a human, and the paper is clear that the question is not one a rule
settles.

## Context and framing

- **Sambasivan et al., *Data Cascades in High-Stakes AI*** (CHI 2021). Field
  evidence that upstream data issues compound and stay invisible until downstream.
  The argument for catching them at the boundary rather than at the model.
- **Shankar et al., *Operationalizing Machine Learning: An Interview Study***
  (2022). [Link](https://arxiv.org/abs/2209.09125) The "Three Vs" (velocity,
  validation, versioning) and the reported pain of not knowing something broke
  until users complain.
- **Polyzotis et al., *Data Lifecycle Challenges in Production ML*** (SIGMOD
  Record 2018). The taxonomy of production data issues, which is the checklist the
  detector suite was tested for completeness against.
- **Amershi et al., *Software Engineering for Machine Learning*** (ICSE-SEIP
  2019). The nine-stage ML workflow, which is what positions Janus at the
  validation stage.
- **Kleppmann, *Designing Data-Intensive Applications*** (chapters 11 and 12).
  At-least-once delivery plus idempotent writes equals effectively-once, which is
  the property the write-back layer is built for.
- **Chen et al., *Reliable Machine Learning*** (O'Reilly). The SRE framing:
  impact reports as blameless postmortems, and a stated detection-time target.
- **Anthropic, *Building Effective Agents*.** The argument for a deterministic
  core with a gated model and a human in the loop, rather than an agent given
  broad autonomy.

## DataHub's own material

The problem statement Janus addresses is DataHub's own. Its June 2026 writing on
data lineage for machine learning names silent data failures, target leakage as
the case that "needs column-level lineage", and lineage graphs that stop at the
warehouse boundary.

Product surfaces used: the incidents API (`raiseIncident`, `updateIncidentStatus`),
structured properties, the Open Assertions Spec, the ML metadata model
(`mlModel`, `mlFeature`, `mlFeatureTable`, `dataProcessInstance`,
`mlModelDeployment`), column-level lineage, the MetadataChangeLog, and the MCP
server.
