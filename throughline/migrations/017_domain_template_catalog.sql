-- Curated catalog v1: additive seed; user templates and historical snapshots stay unchanged.
-- Stable catalog_key values distinguish built-ins from user names. IDs are resolved here,
-- never hardcoded, and all project/team references pin immutable version 1 snapshots.
DO $catalog$
DECLARE
    entry jsonb;
    body jsonb;
    ids jsonb := '{}'::jsonb;
    created_id bigint;
    child_key text;
    role_refs jsonb;
BEGIN
    FOR entry IN SELECT value FROM jsonb_array_elements($templates$
[
  {
    "key": "finance-role-0",
    "kind": "role",
    "name": "Finance data analyst",
    "description": "Source reconciliation, data dictionary and exception register.",
    "content": {
      "category": "finance",
      "instructions": "Reconcile approved ledger exports, time periods and currency assumptions; document missing accounts and unmatched totals. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Source reconciliation, data dictionary and exception register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-role-1",
    "kind": "role",
    "name": "Financial scenario modeller",
    "description": "Formula-backed model, assumption register and sensitivity analysis.",
    "content": {
      "category": "finance",
      "instructions": "Build transparent base, downside and upside scenarios with editable drivers; distinguish observed values from forecasts. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Formula-backed model, assumption register and sensitivity analysis",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-role-2",
    "kind": "role",
    "name": "Finance control reviewer",
    "description": "Recalculation evidence, control findings and finance-owner approval checklist.",
    "content": {
      "category": "finance",
      "instructions": "Independently recompute material figures and challenge assumptions. A qualified finance owner approves decisions; do not place trades, submit filings or move funds. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Recalculation evidence, control findings and finance-owner approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-team",
    "kind": "team",
    "name": "Finance planning team",
    "description": "Specialized analysis, planning and independent review for finance.",
    "role_keys": [
      "finance-role-0",
      "finance-role-1",
      "finance-role-2"
    ],
    "content": {
      "category": "finance",
      "workflow": "Finance data analyst → Financial scenario modeller → Finance control reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "finance-project-0",
    "kind": "project",
    "name": "Rolling cash-flow forecast",
    "description": "Prepare a thirteen-week liquidity view from approved cash, receivables and payables exports.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Prepare a thirteen-week liquidity view from approved cash, receivables and payables exports.",
      "stages": "Reconcile opening cash → schedule receipts and payments → stress-test timing → finance review",
      "deliverables": "Weekly cash schedule; overdue-receivables map; downside scenario; funding decision brief",
      "acceptance_criteria": "Opening cash ties to the approved statement; totals recalculate; timing assumptions have owners; finance owner signs off before action."
    }
  },
  {
    "key": "finance-project-1",
    "kind": "project",
    "name": "Budget variance review",
    "description": "Explain actual versus approved budget and identify accountable follow-up actions.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Explain actual versus approved budget and identify accountable follow-up actions.",
      "stages": "Confirm period and cost centres → reconcile actuals → isolate price and volume drivers → review actions",
      "deliverables": "Variance bridge; materiality-ranked explanations; owner/action register; management summary",
      "acceptance_criteria": "Actuals tie to the source ledger; every material variance has evidence or an explicit unknown; proposed changes are approved by the budget owner."
    }
  },
  {
    "key": "finance-project-2",
    "kind": "project",
    "name": "Unit economics assessment",
    "description": "Evaluate product or customer-segment economics without hiding allocation assumptions.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Evaluate product or customer-segment economics without hiding allocation assumptions.",
      "stages": "Define unit and cohort → reconcile revenue and costs → model contribution and break-even → challenge sensitivity",
      "deliverables": "Unit-economics workbook; allocation policy; cohort comparison; sensitivity chart",
      "acceptance_criteria": "Contribution reconciles to inputs; acquisition and retention assumptions are explicit; no unsupported investment recommendation; finance owner approves use."
    }
  },
  {
    "key": "science-role-0",
    "kind": "role",
    "name": "Research evidence curator",
    "description": "Search protocol, evidence table and exclusion log.",
    "content": {
      "category": "science",
      "instructions": "Define inclusion criteria before screening; record source identifiers, exclusions and conflicting evidence. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Search protocol, evidence table and exclusion log",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-role-1",
    "kind": "role",
    "name": "Experiment designer",
    "description": "Protocol, analysis plan, environment specification and results table.",
    "content": {
      "category": "science",
      "instructions": "Translate the research question into falsifiable hypotheses, baselines, controls and reproducible analysis steps. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Protocol, analysis plan, environment specification and results table",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-role-2",
    "kind": "role",
    "name": "Reproducibility reviewer",
    "description": "Reproduction log, uncertainty critique and limitations statement.",
    "content": {
      "category": "science",
      "instructions": "Independently reproduce key computations, inspect leakage and uncertainty, and separate exploratory from confirmatory findings. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Reproduction log, uncertainty critique and limitations statement",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-team",
    "kind": "team",
    "name": "Reproducible research team",
    "description": "Specialized analysis, planning and independent review for science.",
    "role_keys": [
      "science-role-0",
      "science-role-1",
      "science-role-2"
    ],
    "content": {
      "category": "science",
      "workflow": "Research evidence curator → Experiment designer → Reproducibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "science-project-0",
    "kind": "project",
    "name": "Reproducible experiment",
    "description": "Test a bounded hypothesis against an explicit baseline with reproducible data and code.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Test a bounded hypothesis against an explicit baseline with reproducible data and code.",
      "stages": "Specify hypothesis → freeze protocol → run pilot → execute comparisons → independent reproduction",
      "deliverables": "Preregisterable protocol; data provenance; scripts and environment; effect-size report",
      "acceptance_criteria": "Baseline and ablations use comparable budgets; seeds and exclusions are recorded; uncertainty accompanies estimates; another reviewer reproduces key outputs."
    }
  },
  {
    "key": "science-project-1",
    "kind": "project",
    "name": "Structured literature review",
    "description": "Map published evidence for a narrowly defined question with transparent selection.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Map published evidence for a narrowly defined question with transparent selection.",
      "stages": "Define question → document search → screen and extract → assess quality → synthesize",
      "deliverables": "Search strings and dates; inclusion/exclusion ledger; evidence matrix; disagreement and gap map",
      "acceptance_criteria": "Claims link to inspected sources; exclusions are auditable; study limitations are retained; synthesis does not equate study count with evidence strength."
    }
  },
  {
    "key": "science-project-2",
    "kind": "project",
    "name": "Dataset quality assessment",
    "description": "Determine whether a dataset supports its intended scientific analysis.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Determine whether a dataset supports its intended scientific analysis.",
      "stages": "Inventory provenance → profile missingness → inspect leakage and bias → test suitability → review",
      "deliverables": "Data dictionary; quality checks; leakage report; subgroup coverage; fitness-for-use memo",
      "acceptance_criteria": "Checks are rerunnable; train/test boundaries are explicit; sensitive fields are handled under approved access; suitability claims stay within measured coverage."
    }
  },
  {
    "key": "education-role-0",
    "kind": "role",
    "name": "Learning needs analyst",
    "description": "Learner assumptions, prerequisite map and outcome specification.",
    "content": {
      "category": "education",
      "instructions": "Identify learner prerequisites, context and measurable learning outcomes; use anonymized examples and avoid inferring student ability from demographics. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Learner assumptions, prerequisite map and outcome specification",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-role-1",
    "kind": "role",
    "name": "Instructional designer",
    "description": "Lesson sequence, practice tasks and assessment rubric.",
    "content": {
      "category": "education",
      "instructions": "Align explanations, worked examples, practice and feedback to the target outcomes and available teaching time. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Lesson sequence, practice tasks and assessment rubric",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-role-2",
    "kind": "role",
    "name": "Assessment accessibility reviewer",
    "description": "Alignment matrix, accessibility findings and teacher review checklist.",
    "content": {
      "category": "education",
      "instructions": "Check answer keys, rubric alignment, language clarity and accessibility; a teacher approves assessment and accommodations. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Alignment matrix, accessibility findings and teacher review checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-team",
    "kind": "team",
    "name": "Learning design team",
    "description": "Specialized analysis, planning and independent review for education.",
    "role_keys": [
      "education-role-0",
      "education-role-1",
      "education-role-2"
    ],
    "content": {
      "category": "education",
      "workflow": "Learning needs analyst → Instructional designer → Assessment accessibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "education-project-0",
    "kind": "project",
    "name": "Course module design",
    "description": "Create a teachable module with aligned outcomes, practice and assessment.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Create a teachable module with aligned outcomes, practice and assessment.",
      "stages": "Assess prerequisites → map outcomes → draft lessons → build practice → teacher review",
      "deliverables": "Module plan; worked examples; differentiated exercises; rubric; accessible handouts",
      "acceptance_criteria": "Each outcome has practice and assessment; timing fits the timetable; examples and answer keys are checked; teacher approves before classroom use."
    }
  },
  {
    "key": "education-project-1",
    "kind": "project",
    "name": "Assessment and feedback pack",
    "description": "Prepare fair assessment materials and actionable feedback for a specified course outcome.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Prepare fair assessment materials and actionable feedback for a specified course outcome.",
      "stages": "Define construct → draft items → create rubric → test ambiguity → moderate",
      "deliverables": "Assessment blueprint; question set; annotated answer key; feedback bank",
      "acceptance_criteria": "Items assess stated outcomes; marking criteria distinguish performance levels; accommodations are documented; a teacher verifies grading decisions."
    }
  },
  {
    "key": "education-project-2",
    "kind": "project",
    "name": "Learning support intervention",
    "description": "Plan a small evidence-informed intervention for an identified learning difficulty.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Plan a small evidence-informed intervention for an identified learning difficulty.",
      "stages": "Collect consented baseline → identify skill gap → design practice → define progress checks → review",
      "deliverables": "Skill-gap map; practice schedule; progress measures; teacher/learner reflection prompts",
      "acceptance_criteria": "Baseline and follow-up use comparable measures; no diagnostic claim is made; personal student data is minimized; educator approves the support plan."
    }
  },
  {
    "key": "enterprise-role-0",
    "kind": "role",
    "name": "Enterprise discovery analyst",
    "description": "Current-state map, stakeholder register and dependency inventory.",
    "content": {
      "category": "enterprise",
      "instructions": "Map systems, stakeholders, dependencies and decision rights from approved records; identify evidence gaps. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Current-state map, stakeholder register and dependency inventory",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-role-1",
    "kind": "role",
    "name": "Transformation planner",
    "description": "Options paper, phased roadmap and ownership plan.",
    "content": {
      "category": "enterprise",
      "instructions": "Compare options across rollout risk, operating cost, integration effort and organizational readiness. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Options paper, phased roadmap and ownership plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-role-2",
    "kind": "role",
    "name": "Architecture governance reviewer",
    "description": "Decision record, gate criteria and unresolved-risk register.",
    "content": {
      "category": "enterprise",
      "instructions": "Challenge integration, security, reversibility and accountability; obtain named business and architecture owners for decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Decision record, gate criteria and unresolved-risk register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-team",
    "kind": "team",
    "name": "Enterprise transformation team",
    "description": "Specialized analysis, planning and independent review for enterprise.",
    "role_keys": [
      "enterprise-role-0",
      "enterprise-role-1",
      "enterprise-role-2"
    ],
    "content": {
      "category": "enterprise",
      "workflow": "Enterprise discovery analyst → Transformation planner → Architecture governance reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "enterprise-project-0",
    "kind": "project",
    "name": "Application portfolio review",
    "description": "Create an evidence-backed retain, modernize or retire proposal for a defined application portfolio.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Create an evidence-backed retain, modernize or retire proposal for a defined application portfolio.",
      "stages": "Inventory applications → map business capability → assess cost and risk → compare options → governance review",
      "deliverables": "Application scorecards; dependency graph; disposition proposals; sequenced roadmap",
      "acceptance_criteria": "Every disposition cites evidence and an owner; shared dependencies are checked; cost estimates state scope; governance board approves any retirement."
    }
  },
  {
    "key": "enterprise-project-1",
    "kind": "project",
    "name": "Enterprise AI pilot",
    "description": "Define a bounded AI pilot with measurable value, data boundaries and a stop decision.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Define a bounded AI pilot with measurable value, data boundaries and a stop decision.",
      "stages": "Select workflow → assess data and controls → define baseline → design pilot → evaluate gate",
      "deliverables": "Pilot charter; data-flow map; evaluation dataset plan; cost envelope; go/no-go rubric",
      "acceptance_criteria": "Success and stop thresholds are agreed before evaluation; human accountability is explicit; security and process owners approve deployment."
    }
  },
  {
    "key": "enterprise-project-2",
    "kind": "project",
    "name": "Change readiness assessment",
    "description": "Assess whether a proposed enterprise change has owners, capacity and adoption support.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Assess whether a proposed enterprise change has owners, capacity and adoption support.",
      "stages": "Map impacted groups → collect readiness evidence → identify gaps → plan support → sponsor review",
      "deliverables": "Impact map; readiness scorecard; communications draft; training plan; adoption measures",
      "acceptance_criteria": "Scores cite evidence; resistance is represented fairly; actions have owners and dates; sponsor approves the rollout and communication plan."
    }
  },
  {
    "key": "mid-size-role-0",
    "kind": "role",
    "name": "Business process analyst",
    "description": "Process map, baseline metrics and bottleneck register.",
    "content": {
      "category": "mid-size",
      "instructions": "Map real work, handoffs and constraints using owner interviews and approved operational data. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process map, baseline metrics and bottleneck register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-role-1",
    "kind": "role",
    "name": "Practical improvement planner",
    "description": "Option comparison, implementation checklist and capacity plan.",
    "content": {
      "category": "mid-size",
      "instructions": "Prioritize affordable changes with named owners, estimated effort and a reversible pilot. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Option comparison, implementation checklist and capacity plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-role-2",
    "kind": "role",
    "name": "Business outcome reviewer",
    "description": "Benefit validation, rollout risks and owner acceptance checklist.",
    "content": {
      "category": "mid-size",
      "instructions": "Check savings assumptions, operational disruption and measurement quality; business owner authorizes spending and process changes. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Benefit validation, rollout risks and owner acceptance checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-team",
    "kind": "team",
    "name": "Mid-market improvement team",
    "description": "Specialized analysis, planning and independent review for mid-size business.",
    "role_keys": [
      "mid-size-role-0",
      "mid-size-role-1",
      "mid-size-role-2"
    ],
    "content": {
      "category": "mid-size",
      "workflow": "Business process analyst → Practical improvement planner → Business outcome reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "mid-size-project-0",
    "kind": "project",
    "name": "Sales-to-delivery handoff",
    "description": "Reduce lost requirements and delays between sales commitments and service delivery.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Reduce lost requirements and delays between sales commitments and service delivery.",
      "stages": "Map handoff → sample recent cases → define required brief → pilot checklist → owner review",
      "deliverables": "Handoff brief; responsibility matrix; exception path; cycle-time baseline",
      "acceptance_criteria": "Required fields map to delivery needs; pilot exceptions are recorded; no customer commitment changes without owner approval; handoff time is measured."
    }
  },
  {
    "key": "mid-size-project-1",
    "kind": "project",
    "name": "ERP selection brief",
    "description": "Prepare a comparable requirements and vendor-evaluation pack before procurement.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Prepare a comparable requirements and vendor-evaluation pack before procurement.",
      "stages": "Map critical workflows → separate needs from preferences → define scoring → plan demos → approve shortlist",
      "deliverables": "Prioritized requirements; weighted scorecard; demo scenarios; migration risk list",
      "acceptance_criteria": "Weights are agreed before scoring; total cost includes migration and support; vendor claims remain unverified until demonstrated; procurement owner approves."
    }
  },
  {
    "key": "mid-size-project-2",
    "kind": "project",
    "name": "Capacity and hiring plan",
    "description": "Compare workload, capacity and process alternatives before adding headcount.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Compare workload, capacity and process alternatives before adding headcount.",
      "stages": "Establish demand baseline → map available capacity → model scenarios → compare alternatives → management review",
      "deliverables": "Demand/capacity model; skill-gap map; workload scenarios; hiring decision brief",
      "acceptance_criteria": "Assumptions use an explicit planning period; overtime and ramp-up are visible; alternatives are compared; managers validate workload and approve staffing decisions."
    }
  },
  {
    "key": "startup-role-0",
    "kind": "role",
    "name": "Customer discovery researcher",
    "description": "Hypothesis map, interview guide and evidence ledger.",
    "content": {
      "category": "startup",
      "instructions": "Separate founder assumptions from customer evidence; design non-leading interviews and preserve contradictory observations. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Hypothesis map, interview guide and evidence ledger",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-role-1",
    "kind": "role",
    "name": "Experiment operator",
    "description": "Experiment brief, prototype specification and results log.",
    "content": {
      "category": "startup",
      "instructions": "Design a small reversible market experiment with a clear metric, resource cap and stop condition. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Experiment brief, prototype specification and results log",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-role-2",
    "kind": "role",
    "name": "Venture evidence reviewer",
    "description": "Evidence-strength assessment and continue/change/stop recommendation.",
    "content": {
      "category": "startup",
      "instructions": "Challenge sample bias, vanity metrics and causal claims; founder owns product and funding decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Evidence-strength assessment and continue/change/stop recommendation",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-team",
    "kind": "team",
    "name": "Startup validation team",
    "description": "Specialized analysis, planning and independent review for startups.",
    "role_keys": [
      "startup-role-0",
      "startup-role-1",
      "startup-role-2"
    ],
    "content": {
      "category": "startup",
      "workflow": "Customer discovery researcher → Experiment operator → Venture evidence reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "startup-project-0",
    "kind": "project",
    "name": "Problem validation sprint",
    "description": "Test whether a specific customer segment experiences a problem worth solving.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Test whether a specific customer segment experiences a problem worth solving.",
      "stages": "State assumptions → recruit target users → conduct interviews → synthesize patterns → decide next experiment",
      "deliverables": "Interview script; consent approach; anonymized findings; problem evidence scorecard",
      "acceptance_criteria": "Claims are linked to observations; disconfirming evidence is included; sample limits are explicit; no traction is fabricated."
    }
  },
  {
    "key": "startup-project-1",
    "kind": "project",
    "name": "MVP scope and launch plan",
    "description": "Define the smallest usable product that tests the riskiest value assumption.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Define the smallest usable product that tests the riskiest value assumption.",
      "stages": "Choose hypothesis → map core journey → cut scope → define instrumented pilot → review",
      "deliverables": "MVP brief; must-have backlog; excluded scope; activation metric; rollout checklist",
      "acceptance_criteria": "Every included feature supports the hypothesis or basic usability; success and stop thresholds are set; rollback and support ownership are defined."
    }
  },
  {
    "key": "startup-project-2",
    "kind": "project",
    "name": "Pricing experiment design",
    "description": "Design a limited pricing test with transparent offer terms and interpretable outcomes.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Design a limited pricing test with transparent offer terms and interpretable outcomes.",
      "stages": "Define segment → map current offer → compare test designs → set guardrails → approve experiment",
      "deliverables": "Pricing hypotheses; test matrix; metrics; customer communication draft; decision rubric",
      "acceptance_criteria": "Existing commitments are respected; sample and seasonality limits are stated; no automatic billing changes occur; founder approves the experiment."
    }
  },
  {
    "key": "engineering-role-0",
    "kind": "role",
    "name": "Systems analyst",
    "description": "Behavior map, constraints and implementation brief.",
    "content": {
      "category": "engineering",
      "instructions": "Trace the requested behavior through code, data and interfaces before proposing a change. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Behavior map, constraints and implementation brief",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-role-1",
    "kind": "role",
    "name": "Software implementer",
    "description": "Reviewable patch, test evidence and release notes.",
    "content": {
      "category": "engineering",
      "instructions": "Implement the approved scope with focused tests, migration notes and a reversible release path. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Reviewable patch, test evidence and release notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-role-2",
    "kind": "role",
    "name": "Technical verification reviewer",
    "description": "Severity-ranked findings, reproduction steps and release recommendation.",
    "content": {
      "category": "engineering",
      "instructions": "Independently reproduce failures and check correctness, compatibility and security boundaries. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Severity-ranked findings, reproduction steps and release recommendation",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-team",
    "kind": "team",
    "name": "Software delivery team",
    "description": "Specialized analysis, planning and independent review for engineering.",
    "role_keys": [
      "engineering-role-0",
      "engineering-role-1",
      "engineering-role-2"
    ],
    "content": {
      "category": "engineering",
      "workflow": "Systems analyst → Software implementer → Technical verification reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "engineering-project-0",
    "kind": "project",
    "name": "API integration delivery",
    "description": "Deliver a bounded integration with clear contracts and failure behavior.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Deliver a bounded integration with clear contracts and failure behavior.",
      "stages": "Inspect contracts → model authentication and errors → implement adapter → test failures → review",
      "deliverables": "Contract map; adapter; integration tests; retry policy; operations notes",
      "acceptance_criteria": "Timeouts and malformed responses are tested; secrets stay outside code; retries avoid duplicate effects; owner approves rollout."
    }
  },
  {
    "key": "engineering-project-1",
    "kind": "project",
    "name": "Legacy refactoring plan",
    "description": "Improve a constrained component while preserving its externally observable behavior.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Improve a constrained component while preserving its externally observable behavior.",
      "stages": "Map behavior → capture characterization checks → isolate change → refactor → compare results",
      "deliverables": "Behavior inventory; dependency map; focused patch; before/after tests",
      "acceptance_criteria": "Public behavior remains compatible or changes are documented; tests cover meaningful edge cases; rollback is defined; independent reviewer verifies scope."
    }
  },
  {
    "key": "engineering-project-2",
    "kind": "project",
    "name": "Incident analysis and prevention",
    "description": "Explain a service incident from evidence and prioritize verifiable prevention actions.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Explain a service incident from evidence and prioritize verifiable prevention actions.",
      "stages": "Build timeline → collect signals → test causal explanations → propose controls → review",
      "deliverables": "Evidence-linked timeline; contributing factors; corrective-action register; follow-up checks",
      "acceptance_criteria": "Uncertainty remains visible; people are not blamed for systemic gaps; each action has an owner and verification method; incident owner validates the report."
    }
  },
  {
    "key": "product-design-role-0",
    "kind": "role",
    "name": "Product discovery researcher",
    "description": "Journey map, research notes and prioritized user needs.",
    "content": {
      "category": "product-design",
      "instructions": "Observe the target workflow and connect friction to evidence rather than preference. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Journey map, research notes and prioritized user needs",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-role-1",
    "kind": "role",
    "name": "Interaction designer",
    "description": "Flow specification, screen proposal and component behavior notes.",
    "content": {
      "category": "product-design",
      "instructions": "Produce a focused interaction proposal covering primary, empty, loading, error and recovery states. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Flow specification, screen proposal and component behavior notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-role-2",
    "kind": "role",
    "name": "Usability accessibility reviewer",
    "description": "Browser evidence, accessibility findings and acceptance review.",
    "content": {
      "category": "product-design",
      "instructions": "Verify keyboard use, screen-reader semantics, contrast and task completion; distinguish automated checks from user research. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Browser evidence, accessibility findings and acceptance review",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-team",
    "kind": "team",
    "name": "Product discovery team",
    "description": "Specialized analysis, planning and independent review for product and design.",
    "role_keys": [
      "product-design-role-0",
      "product-design-role-1",
      "product-design-role-2"
    ],
    "content": {
      "category": "product-design",
      "workflow": "Product discovery researcher → Interaction designer → Usability accessibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "product-design-project-0",
    "kind": "project",
    "name": "Usability improvement sprint",
    "description": "Improve one important workflow and verify that users can complete it reliably.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Improve one important workflow and verify that users can complete it reliably.",
      "stages": "Choose task → observe baseline → design changes → implement prototype → evaluate",
      "deliverables": "Task script; friction map; revised flow; browser evidence; prioritized findings",
      "acceptance_criteria": "Primary and failure paths are exercised; keyboard and narrow-screen use are checked; measured results identify the test population and limitations."
    }
  },
  {
    "key": "product-design-project-1",
    "kind": "project",
    "name": "Design system consolidation",
    "description": "Reduce inconsistent interface patterns through documented reusable components.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Reduce inconsistent interface patterns through documented reusable components.",
      "stages": "Inventory screens → identify variants → define tokens → consolidate components → regression review",
      "deliverables": "Component inventory; token map; usage rules; migration list; visual comparisons",
      "acceptance_criteria": "Tokens cover light and dark states where supported; components define focus and error behavior; representative screens are visually checked."
    }
  },
  {
    "key": "product-design-project-2",
    "kind": "project",
    "name": "Onboarding activation review",
    "description": "Identify and reduce friction between first visit and a meaningful first outcome.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Identify and reduce friction between first visit and a meaningful first outcome.",
      "stages": "Define activation → map first-run journey → inspect drop-offs → redesign guidance → evaluate",
      "deliverables": "Activation definition; onboarding flow; empty-state copy; instrumentation plan",
      "acceptance_criteria": "First success is observable; optional setup can be deferred where feasible; no misleading progress or consent patterns; product owner approves metrics."
    }
  },
  {
    "key": "marketing-role-0",
    "kind": "role",
    "name": "Audience insight researcher",
    "description": "Audience needs, channel evidence and message hypotheses.",
    "content": {
      "category": "marketing",
      "instructions": "Build a bounded audience and channel brief from approved analytics and inspected sources; mark hypotheses. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Audience needs, channel evidence and message hypotheses",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-role-1",
    "kind": "role",
    "name": "Campaign content planner",
    "description": "Campaign brief, channel drafts and measurement plan.",
    "content": {
      "category": "marketing",
      "instructions": "Translate approved positioning into a coordinated content plan with consistent claims and measurable goals. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Campaign brief, channel drafts and measurement plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-role-2",
    "kind": "role",
    "name": "Claims brand reviewer",
    "description": "Claim-evidence matrix, editorial findings and publishing checklist.",
    "content": {
      "category": "marketing",
      "instructions": "Check evidence for claims, brand consistency and consent requirements; human owner approves publication and spending. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Claim-evidence matrix, editorial findings and publishing checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-team",
    "kind": "team",
    "name": "Marketing evidence team",
    "description": "Specialized analysis, planning and independent review for marketing.",
    "role_keys": [
      "marketing-role-0",
      "marketing-role-1",
      "marketing-role-2"
    ],
    "content": {
      "category": "marketing",
      "workflow": "Audience insight researcher → Campaign content planner → Claims brand reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "marketing-project-0",
    "kind": "project",
    "name": "Campaign planning kit",
    "description": "Plan a measurable campaign for a defined audience and offer.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Plan a measurable campaign for a defined audience and offer.",
      "stages": "Clarify objective → inspect audience evidence → design message and channels → allocate budget → review",
      "deliverables": "Campaign brief; content calendar; channel drafts; attribution assumptions",
      "acceptance_criteria": "Every claim has support; tracking respects approved consent practices; spend and publication require owner approval; success measures are set in advance."
    }
  },
  {
    "key": "marketing-project-1",
    "kind": "project",
    "name": "Content refresh audit",
    "description": "Prioritize existing content updates based on usefulness, accuracy and business relevance.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Prioritize existing content updates based on usefulness, accuracy and business relevance.",
      "stages": "Inventory content → inspect quality and performance → identify stale claims → draft priorities → editorial review",
      "deliverables": "Content inventory; keep/update/retire queue; revised outlines; measurement baseline",
      "acceptance_criteria": "Performance data has a date range; factual edits cite sources; redirects and ownership are considered; editor approves publication."
    }
  },
  {
    "key": "marketing-project-2",
    "kind": "project",
    "name": "Customer case study draft",
    "description": "Turn approved customer evidence into an accurate, permission-ready case study.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Turn approved customer evidence into an accurate, permission-ready case study.",
      "stages": "Collect source material → verify outcomes → structure narrative → draft → obtain approval",
      "deliverables": "Interview guide; evidence matrix; case-study draft; quote and permission checklist",
      "acceptance_criteria": "Quotes are exact or clearly paraphrased; metrics include context; customer identity and publication permission are verified before release."
    }
  },
  {
    "key": "operations-role-0",
    "kind": "role",
    "name": "Operations baseline analyst",
    "description": "Process baseline, exception map and data-quality notes.",
    "content": {
      "category": "operations",
      "instructions": "Map task sequence, queues and exceptions using approved records; distinguish observed cycle times from estimates. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process baseline, exception map and data-quality notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-role-1",
    "kind": "role",
    "name": "Process improvement designer",
    "description": "Operating procedure draft, pilot plan and owner checklist.",
    "content": {
      "category": "operations",
      "instructions": "Design practical controls, standard work and a reversible pilot around the bottleneck. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Operating procedure draft, pilot plan and owner checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-role-2",
    "kind": "role",
    "name": "Operational assurance reviewer",
    "description": "Walkthrough findings, control checks and acceptance record.",
    "content": {
      "category": "operations",
      "instructions": "Test procedure clarity, exception recovery and benefit assumptions with process owners. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Walkthrough findings, control checks and acceptance record",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-team",
    "kind": "team",
    "name": "Operational excellence team",
    "description": "Specialized analysis, planning and independent review for operations.",
    "role_keys": [
      "operations-role-0",
      "operations-role-1",
      "operations-role-2"
    ],
    "content": {
      "category": "operations",
      "workflow": "Operations baseline analyst → Process improvement designer → Operational assurance reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "operations-project-0",
    "kind": "project",
    "name": "Standard operating procedure",
    "description": "Document a repeatable process including ownership, exceptions and recovery.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Document a repeatable process including ownership, exceptions and recovery.",
      "stages": "Observe process → map decisions → draft procedure → walk through exceptions → approve",
      "deliverables": "SOP; responsibility table; exception checklist; training example",
      "acceptance_criteria": "A new operator can follow the procedure in a supervised walkthrough; escalation owners are named; safety-critical steps receive qualified review."
    }
  },
  {
    "key": "operations-project-1",
    "kind": "project",
    "name": "Supplier performance review",
    "description": "Assess supplier delivery using an agreed period, contractual measures and recorded exceptions.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Assess supplier delivery using an agreed period, contractual measures and recorded exceptions.",
      "stages": "Confirm scorecard → reconcile deliveries → classify exceptions → draft actions → procurement review",
      "deliverables": "Supplier scorecard; exception register; meeting brief; improvement actions",
      "acceptance_criteria": "Metrics tie to source records; contractual terms are checked by an authorized owner; disputed events are flagged; no supplier messages are sent automatically."
    }
  },
  {
    "key": "operations-project-2",
    "kind": "project",
    "name": "Service capacity improvement",
    "description": "Reduce queue delays by comparing demand, capacity and scheduling alternatives.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Reduce queue delays by comparing demand, capacity and scheduling alternatives.",
      "stages": "Measure arrivals and service → locate bottleneck → model options → design pilot → review",
      "deliverables": "Queue baseline; capacity scenarios; pilot schedule; service-level measurement plan",
      "acceptance_criteria": "Peak and average demand are distinguished; model assumptions are explicit; service quality is monitored alongside speed; process owner approves changes."
    }
  },
  {
    "key": "security-role-0",
    "kind": "role",
    "name": "Security scope analyst",
    "description": "Asset map, threat assumptions and authorized-scope statement.",
    "content": {
      "category": "security",
      "instructions": "Inventory approved assets, data flows and trust boundaries within an explicitly authorized scope. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Asset map, threat assumptions and authorized-scope statement",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-role-1",
    "kind": "role",
    "name": "Control assessment specialist",
    "description": "Control evidence matrix and prioritized remediation plan.",
    "content": {
      "category": "security",
      "instructions": "Evaluate defensive controls against stated threats using read-only evidence or an approved test environment. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Control evidence matrix and prioritized remediation plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-role-2",
    "kind": "role",
    "name": "Security validation reviewer",
    "description": "Validated risk register, reproduction evidence and owner review gate.",
    "content": {
      "category": "security",
      "instructions": "Independently validate findings and false positives; require authorization before intrusive tests or production changes. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Validated risk register, reproduction evidence and owner review gate",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-team",
    "kind": "team",
    "name": "Security assurance team",
    "description": "Specialized analysis, planning and independent review for security.",
    "role_keys": [
      "security-role-0",
      "security-role-1",
      "security-role-2"
    ],
    "content": {
      "category": "security",
      "workflow": "Security scope analyst → Control assessment specialist → Security validation reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "security-project-0",
    "kind": "project",
    "name": "Threat modelling workshop",
    "description": "Identify threats and controls for a bounded application or service.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Identify threats and controls for a bounded application or service.",
      "stages": "Define scope → map data and trust boundaries → enumerate threats → evaluate controls → review",
      "deliverables": "Data-flow diagram; threat register; mitigations; residual-risk decisions",
      "acceptance_criteria": "Threats reference actual boundaries; assumptions and unknowns are explicit; owners accept residual risk; no exploit is executed outside authorized scope."
    }
  },
  {
    "key": "security-project-1",
    "kind": "project",
    "name": "Access review preparation",
    "description": "Prepare an auditable review of access assignments for authorized system owners.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Prepare an auditable review of access assignments for authorized system owners.",
      "stages": "Collect approved exports → normalize identities → flag anomalies → assign reviewers → reconcile decisions",
      "deliverables": "Entitlement inventory; privileged-access flags; reviewer worksheet; decision log",
      "acceptance_criteria": "Identity joins are checked; stale accounts are evidence-backed; removals require owner approval; sensitive exports remain in approved storage."
    }
  },
  {
    "key": "security-project-2",
    "kind": "project",
    "name": "Security incident tabletop",
    "description": "Design a safe discussion exercise to test incident response coordination.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Design a safe discussion exercise to test incident response coordination.",
      "stages": "Choose scenario → define injects → map decision points → facilitate walkthrough → document gaps",
      "deliverables": "Scenario pack; facilitator script; participant roles; response gaps; action register",
      "acceptance_criteria": "Exercise is clearly labelled simulated; no live attack or alert is triggered; escalation contacts are verified; actions have accountable owners."
    }
  },
  {
    "key": "legal-compliance-role-0",
    "kind": "role",
    "name": "Compliance evidence analyst",
    "description": "Obligation register, source index and evidence gaps.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Index approved policies and obligations with jurisdiction, effective dates and source references; flag uncertainty for counsel. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Obligation register, source index and evidence gaps",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-role-1",
    "kind": "role",
    "name": "Policy process drafter",
    "description": "Policy draft, responsibility map and implementation checklist.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Draft operational policy language and evidence-collection workflows under an approved legal brief. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Policy draft, responsibility map and implementation checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-role-2",
    "kind": "role",
    "name": "Qualified review coordinator",
    "description": "Review packet, unresolved interpretations and counsel approval checklist.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Check traceability and prepare questions for qualified legal or compliance review. Do not assert legal sufficiency or submit official filings. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Review packet, unresolved interpretations and counsel approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-team",
    "kind": "team",
    "name": "Compliance evidence team",
    "description": "Specialized analysis, planning and independent review for legal and compliance.",
    "role_keys": [
      "legal-compliance-role-0",
      "legal-compliance-role-1",
      "legal-compliance-role-2"
    ],
    "content": {
      "category": "legal-compliance",
      "workflow": "Compliance evidence analyst → Policy process drafter → Qualified review coordinator → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "legal-compliance-project-0",
    "kind": "project",
    "name": "Policy gap assessment",
    "description": "Compare an internal policy against a supplied, authoritative requirements set.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Compare an internal policy against a supplied, authoritative requirements set.",
      "stages": "Confirm jurisdiction and scope → map clauses → inspect evidence → draft gaps → qualified review",
      "deliverables": "Requirement-to-policy matrix; gap register; policy edits; counsel questions",
      "acceptance_criteria": "Requirements carry source and date; uncertain interpretation is flagged; qualified counsel or compliance owner approves conclusions before reliance."
    }
  },
  {
    "key": "legal-compliance-project-1",
    "kind": "project",
    "name": "Contract review preparation",
    "description": "Organize a contract and business context for review by qualified counsel.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Organize a contract and business context for review by qualified counsel.",
      "stages": "Confirm agreement version → extract obligations → map commercial concerns → prepare questions → legal review",
      "deliverables": "Clause index; obligation calendar; deviation summary; questions for counsel",
      "acceptance_criteria": "Citations resolve to exact clauses; omissions and ambiguity are explicit; no legal advice or approval is implied; qualified counsel reviews before signature."
    }
  },
  {
    "key": "legal-compliance-project-2",
    "kind": "project",
    "name": "Audit evidence readiness",
    "description": "Prepare a traceable evidence pack for a defined internal or external audit.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Prepare a traceable evidence pack for a defined internal or external audit.",
      "stages": "Confirm audit scope → map requests → collect approved evidence → check freshness → owner review",
      "deliverables": "Evidence index; request tracker; control-owner map; missing-evidence register",
      "acceptance_criteria": "Artifacts have owner, period and source; sensitive access is limited; no evidence is fabricated; authorized compliance owner approves submission."
    }
  },
  {
    "key": "healthcare-role-0",
    "kind": "role",
    "name": "Health workflow analyst",
    "description": "Workflow map, data boundary and administrative bottleneck evidence.",
    "content": {
      "category": "healthcare",
      "instructions": "Map non-diagnostic administrative workflows using de-identified, approved inputs; flag points requiring clinical judgment. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Workflow map, data boundary and administrative bottleneck evidence",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-role-1",
    "kind": "role",
    "name": "Health service planner",
    "description": "Process proposal, escalation map and pilot measurement plan.",
    "content": {
      "category": "healthcare",
      "instructions": "Draft administrative process improvements that preserve escalation to qualified clinicians and patient safety responsibilities. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process proposal, escalation map and pilot measurement plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-role-2",
    "kind": "role",
    "name": "Clinical governance coordinator",
    "description": "Safety questions, privacy checks and qualified-human approval record.",
    "content": {
      "category": "healthcare",
      "instructions": "Prepare a review packet for qualified clinical, privacy and operational owners. Do not diagnose, recommend treatment or automate care decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Safety questions, privacy checks and qualified-human approval record",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-team",
    "kind": "team",
    "name": "Health operations review team",
    "description": "Specialized analysis, planning and independent review for healthcare.",
    "role_keys": [
      "healthcare-role-0",
      "healthcare-role-1",
      "healthcare-role-2"
    ],
    "content": {
      "category": "healthcare",
      "workflow": "Health workflow analyst → Health service planner → Clinical governance coordinator → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "healthcare-project-0",
    "kind": "project",
    "name": "Clinic administration workflow",
    "description": "Improve a bounded scheduling or referral administration process without changing clinical triage.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Improve a bounded scheduling or referral administration process without changing clinical triage.",
      "stages": "Map administrative steps → assess delays → draft process → check escalation → qualified review",
      "deliverables": "Workflow map; scheduling checklist; escalation rules; pilot metrics",
      "acceptance_criteria": "No clinical priority is inferred by the model; patient identifiers are removed from examples; qualified clinical and operational owners approve changes."
    }
  },
  {
    "key": "healthcare-project-1",
    "kind": "project",
    "name": "Patient information readability",
    "description": "Improve clarity of supplied patient information while preserving clinician-approved medical meaning.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Improve clarity of supplied patient information while preserving clinician-approved medical meaning.",
      "stages": "Confirm approved source → assess readability → draft plain-language version → compare meaning → clinical review",
      "deliverables": "Annotated draft; terminology glossary; meaning-preservation checklist; review questions",
      "acceptance_criteria": "No medical claim is added or removed without clinician approval; accessibility is checked; qualified clinician approves the final text before distribution."
    }
  },
  {
    "key": "healthcare-project-2",
    "kind": "project",
    "name": "Healthcare service quality review",
    "description": "Organize de-identified administrative service feedback into actionable quality improvements.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Organize de-identified administrative service feedback into actionable quality improvements.",
      "stages": "Define service scope → inspect de-identified feedback → classify themes → prioritize actions → governance review",
      "deliverables": "Feedback evidence table; service themes; improvement plan; measurement schedule",
      "acceptance_criteria": "Small-group reidentification risk is reviewed; anecdotes are not treated as clinical evidence; safety concerns route to qualified staff; governance owner approves actions."
    }
  },
  {
    "key": "nonprofit-role-0",
    "kind": "role",
    "name": "Community needs researcher",
    "description": "Needs assessment, stakeholder map and evidence gaps.",
    "content": {
      "category": "nonprofit",
      "instructions": "Map needs from consented community input and inspected evidence; preserve minority perspectives and uncertainty. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Needs assessment, stakeholder map and evidence gaps",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-role-1",
    "kind": "role",
    "name": "Programme planning specialist",
    "description": "Programme plan, resource budget and outcome framework.",
    "content": {
      "category": "nonprofit",
      "instructions": "Connect activities, resources and outcomes through an explicit theory of change with feasible measures. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Programme plan, resource budget and outcome framework",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-role-2",
    "kind": "role",
    "name": "Impact accountability reviewer",
    "description": "Impact review, reporting limitations and stakeholder approval checklist.",
    "content": {
      "category": "nonprofit",
      "instructions": "Challenge attribution, inclusion and reporting claims; programme owner approves commitments and public statements. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Impact review, reporting limitations and stakeholder approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-team",
    "kind": "team",
    "name": "Impact delivery team",
    "description": "Specialized analysis, planning and independent review for nonprofit and public good.",
    "role_keys": [
      "nonprofit-role-0",
      "nonprofit-role-1",
      "nonprofit-role-2"
    ],
    "content": {
      "category": "nonprofit",
      "workflow": "Community needs researcher → Programme planning specialist → Impact accountability reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "nonprofit-project-0",
    "kind": "project",
    "name": "Grant proposal preparation",
    "description": "Prepare a funder-aligned proposal grounded in a feasible programme and verified evidence.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Prepare a funder-aligned proposal grounded in a feasible programme and verified evidence.",
      "stages": "Read funding criteria → map programme fit → draft outcomes and budget → check eligibility → owner review",
      "deliverables": "Compliance checklist; proposal draft; budget narrative; evidence and attachments index",
      "acceptance_criteria": "Eligibility and deadlines are verified from funder materials; outcomes are not exaggerated; authorized owner approves submission; no application is sent automatically."
    }
  },
  {
    "key": "nonprofit-project-1",
    "kind": "project",
    "name": "Programme impact framework",
    "description": "Define how a programme will measure outcomes without overstating causality.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Define how a programme will measure outcomes without overstating causality.",
      "stages": "Map theory of change → select indicators → define collection → assess burden → stakeholder review",
      "deliverables": "Theory of change; indicator dictionary; collection plan; limitations and learning agenda",
      "acceptance_criteria": "Indicators distinguish outputs from outcomes; consent and data minimization are defined; causal claims match evaluation design; programme owner approves."
    }
  },
  {
    "key": "nonprofit-project-2",
    "kind": "project",
    "name": "Volunteer onboarding programme",
    "description": "Create an inclusive onboarding process with clear responsibilities and safeguarding escalation.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Create an inclusive onboarding process with clear responsibilities and safeguarding escalation.",
      "stages": "Map volunteer roles → identify prerequisites → draft training → test scenarios → coordinator review",
      "deliverables": "Role brief; onboarding checklist; training outline; safeguarding contact map",
      "acceptance_criteria": "Responsibilities and boundaries are explicit; required checks are confirmed by the coordinator; scenarios test escalation; accessibility needs are addressed."
    }
  }
]
$templates$::jsonb) LOOP
        -- schema.sql consumers may adopt the migration ledger afterwards.
        -- Reuse existing catalog rows; never replace a customized version or snapshot.
        SELECT id INTO created_id FROM public.pm_templates
        WHERE content->>'catalog_key' = entry->>'key' AND kind = entry->>'kind'
        ORDER BY id LIMIT 1;
        IF FOUND THEN
            ids := ids || jsonb_build_object(entry->>'key', created_id);
            CONTINUE;
        END IF;
        body := entry->'content' || jsonb_build_object('catalog_key', entry->>'key');
        IF entry ? 'role_keys' THEN
            role_refs := '[]'::jsonb;
            FOR child_key IN SELECT jsonb_array_elements_text(entry->'role_keys') LOOP
                role_refs := role_refs || jsonb_build_array(jsonb_build_object('id', (ids->>child_key)::bigint, 'version', 1));
            END LOOP;
            body := body || jsonb_build_object('role_templates', role_refs);
        END IF;
        IF entry ? 'team_key' THEN
            body := body || jsonb_build_object('team_template', jsonb_build_object('id', (ids->>(entry->>'team_key'))::bigint, 'version', 1));
        END IF;
        INSERT INTO public.pm_templates(kind, name, description, content)
        VALUES(entry->>'kind', entry->>'name', entry->>'description', body)
        RETURNING id INTO created_id;
        ids := ids || jsonb_build_object(entry->>'key', created_id);
        INSERT INTO public.pm_template_versions(template_id, version, snapshot)
        SELECT id, version, to_jsonb(t) FROM public.pm_templates t WHERE id = created_id;
    END LOOP;
END $catalog$;
