1. Define your contract with the team first
Before implementing statistical methods, agree on the inputs and outputs with A, C, and D.
Input from A: experiment snapshot
{
  "experiment_id": "exp_123",
  "status": "running",
  "primary_metric": {
    "name": "checkout_conversion",
    "type": "binary",
    "direction": "increase",
    "minimum_detectable_effect": 0.05
  },
  "guardrail_metrics": [
    {
      "name": "refund_rate",
      "type": "binary",
      "direction": "decrease"
    }
  ],
  "variants": [
    {
      "id": "control",
      "users": 10000,
      "conversions": 1200
    },
    {
      "id": "treatment",
      "users": 10100,
      "conversions": 1323
    }
  ],
  "planned_sample_size": 30000,
  "elapsed_days": 6,
  "planned_duration_days": 14
}
Output to A, C, and D: analysis result
Use one stable, structured result:
{
  "experiment_id": "exp_123",
  "analysis_status": "valid",
  "decision": "CONTINUE",
  "confidence": 0.82,
  "primary_metric": {
    "control_value": 0.12,
    "treatment_value": 0.131,
    "absolute_lift": 0.011,
    "relative_lift": 0.0917,
    "confidence_interval": [0.0018, 0.0202],
    "p_value": 0.019,
    "statistically_significant": true,
    "practically_significant": true
  },
  "data_quality": {
    "sample_ratio_mismatch": false,
    "insufficient_sample": true,
    "missing_data": false
  },
  "guardrails": {
    "status": "healthy",
    "violations": []
  },
  "reasons": [
    "Treatment conversion is 9.17% higher than control",
    "The result is statistically significant",
    "Only 67% of the planned sample has been collected"
  ],
  "warnings": [
    "Avoid making a final decision before the minimum sample is reached"
  ]
}
This object is the central integration point:
A supplies the experiment snapshot and stores your result.
C uses your facts to generate business-friendly explanations.
D renders the numbers, warnings, and recommendation.
Your evaluation harness tests the same object.
2. Build the statistical engine in layers
Do not start with a sophisticated Bayesian or sequential framework. Build a correct, testable MVP and add sophistication only after the complete lifecycle works.
Layer 1: descriptive statistics
For each variant, calculate:
Sample size
Metric value
Absolute difference
Relative lift
Standard error
Confidence interval
For a conversion metric:
conversion rate = conversions / users

absolute lift = treatment rate − control rate

relative lift = (treatment rate − control rate) / control rate
Business users generally understand relative lift, but decisions should also consider absolute lift.
For example:
Control: 10%
Treatment: 11%
Relative lift: 10%
Absolute lift: 1 percentage point
Showing only “10% improvement” can be misleading, so return both.
Layer 2: hypothesis tests
Support two metric types for the hackathon MVP.
Binary metrics
Examples:
Conversion
Click-through rate
Retention
Churn
Error rate
Use a two-proportion z-test, with:
H₀: treatment rate = control rate
H₁: treatment rate ≠ control rate
Optionally use a one-sided test when the experiment configuration explicitly defines a directional hypothesis.
Continuous metrics
Examples:
Revenue per user
Session duration
Order value
Latency
Use Welch’s t-test because it does not assume equal variance.
The input must contain, per variant:
{
  "users": 5000,
  "mean": 42.5,
  "standard_deviation": 16.8
}
Return at least:
Effect estimate
Confidence interval
p-value
Significance flag
Test name
Assumptions and warnings
Layer 3: practical significance
A tiny improvement can become statistically significant with enough traffic but still be commercially useless.
Use the experiment’s minimum detectable or worthwhile effect:
statistically significant = p-value < alpha

practically significant =
    effect is in desired direction
    AND effect magnitude >= minimum worthwhile effect
Keep these two concepts separate in the API:
{
  "statistically_significant": true,
  "practically_significant": false
}
This separation will make your decision engine substantially more credible.
3. Add experiment validity and safety checks
Your engine should sometimes refuse to make a recommendation. That is an important sign of quality.
Sample ratio mismatch
If traffic was intended to be split 50/50 but the observed allocation is unexpectedly 65/35, the randomization or event pipeline may be broken.
Use a chi-square goodness-of-fit test:
Expected users:
control = total × configured control allocation
treatment = total × configured treatment allocation
If the SRM test is significant at a strict threshold, such as p < 0.001, return:
{
  "analysis_status": "invalid",
  "decision": "INVESTIGATE",
  "warnings": ["Sample ratio mismatch detected"]
}
Although the requested product decisions are Scale, Continue, Stop, or Rollback, an internal INVESTIGATE state is valuable. The UI can represent it as “Continue blocked—investigate data quality.”
Other validation checks
Add inexpensive checks for:
Zero or very small sample sizes
Missing observations
Impossible values, such as conversions greater than users
NaN or infinite values
Extreme control-treatment imbalance
Experiment duration below a minimum threshold
Planned sample size not reached
Metric definition missing
Incorrect metric direction
No data received recently
Guardrail metrics missing
Novelty and weekday effects
Warn when the experiment has not covered a complete business cycle. For many demos, seven days is a sensible default:
elapsed duration < 7 days → early-duration warning
This is a policy heuristic, not a statistical truth, so label it accordingly.
4. Handle continuous monitoring safely
A major trap is repeatedly running a normal significance test every few minutes. This inflates the false-positive rate because the experiment is being “peeked” at repeatedly.
For a hackathon, choose one of these approaches.
Recommended MVP: fixed-horizon policy
Calculate interim estimates at any time.
Allow monitoring and warnings.
Do not issue a final Scale or Stop decision until:minimum duration is reached, and
required sample size is reached.

This is easy to explain and implement correctly.
Stretch option: sequential boundaries
Introduce checkpoints such as 25%, 50%, 75%, and 100% of planned information, with stricter early stopping boundaries.
Do not claim sequential validity unless you genuinely implement an alpha-spending or equivalent procedure. A simple p < 0.05 at every checkpoint is unsafe.
For the demo, fixed-horizon analysis plus an emergency guardrail rollback is usually the strongest choice.
5. Build the decision policy as deterministic rules
The decision engine should consume the statistical result. It should not calculate statistics itself.
A useful ordering is:
1. Is the data valid?
2. Is a safety guardrail seriously violated?
3. Is there strong evidence of harm?
4. Has the experiment reached minimum duration and sample size?
5. Is the positive effect statistically and practically significant?
6. Is the result unlikely to become useful?
7. Otherwise, continue collecting data.
Suggested policy
ROLLBACK
Recommend rollback when:
Treatment significantly harms the primary metric, or
A critical guardrail is significantly worse, and
The harm exceeds a configured safety threshold.
Example:
refund rate increased by 30%
confidence interval excludes zero
maximum accepted increase is 5%
→ ROLLBACK
Guardrail harm should generally take precedence over primary-metric improvement.
SCALE
Recommend scale when:
Data quality is valid
Minimum sample is reached
Minimum duration is reached
Primary metric is statistically significant
Effect is in the desired direction
Effect is practically significant
No important guardrail is violated
STOP
Recommend stop when one of these applies:
Treatment is reliably worse but not dangerous enough to require rollback
Full planned sample is reached and there is no meaningful improvement
A futility rule indicates the probability of reaching the required effect is very low
For a simple MVP, use:
sample complete
AND confidence interval excludes the minimum worthwhile improvement
→ STOP for futility
For example, if the target is at least a 5% lift and the entire confidence interval lies below 5%, continuing is unlikely to meet the business goal.
CONTINUE
Recommend continue when:
Results are inconclusive
Sample or duration is insufficient
The effect looks positive but is not yet reliable
The result is statistically significant but minimum exposure requirements are not met
Minor warnings exist but analysis remains usable
Example pseudocode
def recommend(result, config):
    if not result.data_quality.is_valid:
        return Decision.CONTINUE, "Analysis blocked by data-quality issues"

    if result.guardrails.has_critical_harm:
        return Decision.ROLLBACK, "Critical guardrail exceeded"

    if result.primary.has_significant_harm:
        return Decision.ROLLBACK, "Treatment is causing reliable harm"

    if not result.minimum_duration_reached:
        return Decision.CONTINUE, "Minimum experiment duration not reached"

    if not result.minimum_sample_reached:
        return Decision.CONTINUE, "More observations are required"

    if (
        result.primary.statistically_significant
        and result.primary.practically_significant
        and result.primary.is_positive
        and result.guardrails.are_healthy
    ):
        return Decision.SCALE, "Reliable and meaningful improvement detected"

    if result.primary.futile or result.planned_sample_reached:
        return Decision.STOP, "Meaningful improvement is unlikely"

    return Decision.CONTINUE, "Evidence remains inconclusive"
Keep thresholds in configuration rather than hard-coded throughout the code.
6. Make every decision explainable
Return machine-readable reason codes as well as display text:
{
  "decision": "CONTINUE",
  "reason_codes": [
    "POSITIVE_TREND",
    "INSUFFICIENT_SAMPLE",
    "NO_GUARDRAIL_VIOLATION"
  ],
  "evidence": {
    "relative_lift": 0.084,
    "p_value": 0.071,
    "sample_progress": 0.62
  }
}
This gives C safe facts for the LLM prompt and gives D consistent UI labels.
Avoid generating a single opaque “confidence score” unless you define exactly what it means. Statistical confidence, probability of being best, model confidence, and policy confidence are different concepts.
If the UI demands one confidence indicator, derive a labelled decision-confidence tier:
Low: insufficient or conflicting evidence
Medium: promising evidence but incomplete sample
High: final thresholds met without validity warnings
7. Build the evaluation scenario generator
This is the second major part of your responsibility. You need known scenarios with expected decisions so the team can measure recommendation accuracy.
Create deterministic fixtures using fixed random seeds.
Essential scenarios
Scenario	Data pattern	Expected action
Clear winner	Large positive, meaningful lift	Scale
Clear loser	Significant negative lift	Rollback or Stop
Inconclusive early	Small sample, uncertain result	Continue
No effect at completion	Full sample, effect near zero	Stop
Tiny but significant win	Huge sample, effect below business threshold	Stop
Promising but immature	Positive result, insufficient sample/duration	Continue
Guardrail failure	Conversion improves but refund/error rate worsens	Rollback
Sample ratio mismatch	Observed traffic differs sharply from allocation	Investigate/Continue blocked
High-variance metric	Attractive mean, very wide interval	Continue
Delayed harm	Early positive trend followed by guardrail decline	Rollback
Missing data	Invalid variant statistics	Continue blocked
Multiple variants	One winner among several treatments	Scale winner after correction

For each scenario, store:
{
  "scenario": "clear_winner",
  "expected_decision": "SCALE",
  "expected_significance": true,
  "expected_warnings": [],
  "input": {}
}
Two kinds of evaluation
Deterministic correctness tests
Use hand-calculated or trusted-library reference values to verify:
Conversion rates
Lift
Confidence intervals
p-values
SRM detection
Policy decisions
Monte Carlo simulation
Run each underlying truth many times:
True control conversion = 10%
True treatment conversion = 11%
Sample size = 10,000 per variant
Repeat = 1,000 simulated experiments
Then calculate:
How often true winners are scaled
How often harmful variants are rolled back
False-positive rate
False-negative rate
Correct significance-detection rate
Decision accuracy
Average sample/time before decision, if early stopping exists
This is much more convincing than demonstrating five manually selected experiments.
8. Map your work to the required hackathon evaluations
Your component directly owns three important evaluation areas.
Recommendation accuracy
accuracy =
correct policy decisions / total evaluation scenarios
Also report a confusion matrix because mistakes have different severity:
Actual \ Predicted	Scale	Continue	Stop	Rollback
Scale				
Continue				
Stop				
Rollback				

Scaling a harmful treatment is far worse than continuing a winning one, so consider a weighted score.
Statistical-significance detection accuracy
Across simulated experiments:
significance accuracy =
correct significance classifications / all simulations
Also expose:
false-positive rate =
no-effect experiments marked significant / no-effect experiments

power =
true-effect experiments marked significant / true-effect experiments
A strong demonstration is:
Under the null effect, observed false-positive rate is close to configured alpha.
Under the target effect, detection power increases with sample size.
Analysis-time reduction
Measure:
manual baseline analysis time − copilot analysis time
Your engine can log:
Analysis request timestamp
Analysis completion timestamp
Decision generation duration
The broader manual baseline may come from a small expert/user study organized by the team.
9. Recommended module structure
A clean separation might look like:
stats_engine/
├── models.py
├── descriptive.py
├── binary_metrics.py
├── continuous_metrics.py
├── confidence_intervals.py
├── sample_size.py
├── data_quality.py
├── guardrails.py
├── policy.py
├── reason_codes.py
└── service.py

evaluation/
├── scenarios/
│   ├── clear_winner.json
│   ├── guardrail_failure.json
│   └── sample_ratio_mismatch.json
├── simulator.py
├── metrics.py
├── run_evaluations.py
└── expected_results.json

tests/
├── test_binary_metrics.py
├── test_continuous_metrics.py
├── test_data_quality.py
├── test_policy.py
└── test_scenarios.py
Useful Python libraries include:
scipy for hypothesis tests and distributions
statsmodels for proportions, power, and sample-size calculations
numpy for simulation
pandas for evaluation reports
pydantic for input/output contracts
pytest for correctness tests
10. Suggested implementation sequence
Phase 1: integration contract
Deliver:
Input schema
Output schema
Decision enum
Reason-code enum
Three sample payloads
This unblocks every teammate.
Phase 2: statistical MVP
Implement:
Binary metric analysis
Continuous metric analysis
Lift and confidence intervals
Significance flags
Practical-significance checks
Phase 3: validity checks
Implement:
Sample ratio mismatch
Minimum sample and duration
Invalid or missing values
Guardrail evaluation
Phase 4: deterministic policy
Implement and unit-test:
Scale
Continue
Stop
Rollback
Analysis-blocked behavior
Phase 5: evaluation suite
Create:
At least 10 named scenarios
Expected decisions
Statistical reference tests
Confusion matrix
Significance-detection metrics
Phase 6: integration and demo
Connect your engine to A’s API, give C your structured evidence, and supply D with UI-friendly fields.
11. Demo story you should aim for
A compelling demo has state changes rather than a single final result:
Experiment begins with insufficient data → CONTINUE.
Treatment develops a positive trend → still CONTINUE.
It reaches sufficient sample and meaningful significance → SCALE.
A second experiment improves conversion but damages a critical guardrail → ROLLBACK.
A third experiment has a broken 50/50 allocation → analysis is blocked by SRM detection.
The evaluation screen shows policy accuracy and significance-detection accuracy across simulated scenarios.
This demonstrates that the system is not merely searching for winners. It understands uncertainty, business value, safety, and data quality.
12. What to prioritize under hackathon constraints
Your must-have scope should be:
Two-proportion z-test
Welch’s t-test
Confidence intervals
Absolute and relative lift
Minimum sample/duration rules
Practical-significance threshold
Guardrails
Sample ratio mismatch
Deterministic decision policy
Ten or more evaluation scenarios
Machine-readable reasons
Stretch features include:
Bayesian probability of being best
Sequential testing
Multiple-comparison corrections
Multi-armed experiments
CUPED variance reduction
Heterogeneous treatment effects
Automated power and sample-size planning
It is much better to have a correct, thoroughly evaluated fixed-horizon engine than an ambitious sequential or Bayesian engine whose guarantees cannot be demonstrated.
Your final deliverable should make this statement true:
Given a validated experiment snapshot, the engine returns reproducible statistical evidence, detects unsafe or invalid conditions, and produces an explainable decision that can be tested against expert-labelled scenarios.
