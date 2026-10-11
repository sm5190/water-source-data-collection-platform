# ER Model

The central entity is `SupplyRecord`.

A SupplyRecord means:

> one city receives water from one hydrologic water source through one utility.

Core domain:
- Country
- Jurisdiction
- City
- Utility
- WaterSource
- WaterSystem
- SupplyRecord
- Reservoir / SupplyReservoir
- TransferSystem / SupplyTransfer
- InfrastructureType / SupplyInfrastructure
- PointOfDiversion
- population / withdrawal / quality observations
- TreatmentPlant

Evidence and provenance:
- SourceDocument
- DocumentArtifact
- EvidenceSpan
- FieldDefinition
- FieldClaim
- ClaimEvidence
- ClaimDependency
- FieldResolution
- HumanReview

Execution and memory (implemented: research_runs, run_events, context_bundles, trajectories,
distillation_jobs, procedures, procedure_versions, procedure_origins, procedure_usages):
- ResearchRun (`research_runs`) and its trusted events (`run_events`, replaces ResearchStep)
- ContextBundle (`context_bundles`)
- Trajectory (`trajectories`): one (run, field type), built from run events
- DistillationJob (`distillation_jobs`): idempotency key = hash(trajectory, distiller version)
- Procedure (`procedures`): logical procedure; key = compatibility metadata + capability plan
- ProcedureVersion (`procedure_versions`): immutable recipe + lifecycle status + usage stats;
  one ACTIVE version per procedure (partial unique index)
- ProcedureOrigin (`procedure_origins`): provenance — which runs/trajectories created or
  support a version (unique per distillation key)
- ProcedureUsage (`procedure_usages`): retrieved → selected → applied → outcome, per
  (version, run, field)
