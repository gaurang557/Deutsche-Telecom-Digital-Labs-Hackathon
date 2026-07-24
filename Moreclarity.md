Yes—this problem is a great fit for a 4-person team, and one member will almost certainly use NumPy/Pandas (or SciPy/Statsmodels). This isn't just an LLM project; the judges specifically mention statistical significance detection accuracy, so you'll need actual statistical calculations rather than asking an LLM to decide winners.

Team Split
👤 Member 1 — AI Copilot & LLM Engineer
Responsibility
Build the AI assistant that creates and validates experiments.

Features

Convert business goal → experiment hypothesis
Suggest Feature Flags
Recommend target audience
Suggest KPIs
Recommend traffic split
Generate experiment summary
Explain experiment results
Recommend next action
Tech

OpenAI API
LangChain/LlamaIndex (optional)
Prompt engineering
RAG (optional)
👤 Member 2 — Backend & Experiment Engine
Responsibility
Everything related to experiment management.

Features

Create experiment API
Store experiments
Feature Flag management
Detect overlapping experiments
Traffic allocation
CRUD APIs
Historical experiment storage
Tech

FastAPI / Flask / Express
PostgreSQL / MongoDB
REST APIs
👤 Member 3 — Data Science / Statistics (NumPy Guy)
This is where NumPy, Pandas, SciPy, and Statsmodels come in.

Their responsibilities include:

1. Experiment monitoring
Read experiment events

Variant A
Visitors = 2200
Conversions = 210

Variant B
Visitors = 2250
Conversions = 265

Compute

Conversion Rate
Lift %
Confidence

2. Statistical significance
This is literally in the judging criteria.

Implement

Two-proportion z-test
Chi-square test
Bayesian probability (optional)
Confidence intervals
p-value
Libraries

numpy
pandas
scipy.stats
statsmodels.stats.proportion

Example

from statsmodels.stats.proportion import proportions_ztest

3. Detect winner
If p < 0.05

Winner = Variant B
Confidence = 97%

Recommendation = Scale

4. Continuous monitoring
Simulate incoming data

Every few seconds

Read new CSV

↓

Recalculate

↓

Update dashboard

5. Recommendation logic
Example

Lift = 1%

Confidence = 40%

↓

Continue

Lift = 12%

Confidence = 99%

↓

Scale

Negative lift

↓

Rollback

👤 Member 4 — Frontend & Dashboard
Responsible for the user interface.

Pages

Dashboard
Create Experiment
AI Copilot
Experiment Analytics
Monitoring
Chat
Charts

Conversion Rate
Confidence
Traffic Split
Timeline
Winner
Tech

React
Next.js
Tailwind
Recharts
Overall Architecture
                 User

                  │
                  ▼

          React Dashboard

                  │

        ┌─────────┴─────────┐
        │                   │

 AI Copilot API        Experiment API

        │                   │

     OpenAI            PostgreSQL

        │                   │

        └─────────┬─────────┘

                  ▼

        Analytics Engine

      Pandas + NumPy + SciPy

                  ▼

      Scale / Continue /
      Stop / Rollback

Where NumPy/Pandas are actually used
They aren't for AI—they're for the evaluation metrics the judges care about.

Example calculations
Conversion rate
CR = conversions / visitors

Lift
Lift = (B - A)/A ×100

Confidence Interval
Using Statsmodels

p-value
Using SciPy

Rolling averages
NumPy/Pandas

Time-series monitoring
Pandas

Detect anomalies
Z-score

Moving averages

CUSUM (optional)

Hackathon-friendly MVP
You don't need a sophisticated experimentation platform. A practical MVP could include:

Phase 1 (core flow)
AI generates an experiment plan from a business goal.
User reviews and edits the suggested configuration.
Save the experiment.
Phase 2 (analysis)
Load or simulate experiment results (CSV or generated data).
Compute conversion rates, lift, confidence intervals, and p-values.
Decide whether there is a statistically significant winner.
Phase 3 (AI insights)
Feed the computed statistics to the LLM.
Generate an explanation in plain business language.
Recommend Scale, Continue, Stop, or Rollback, with reasons tied to the statistics.
Phase 4 (dashboard)
Show live or simulated updates.
Display charts and the AI-generated recommendations.
One important design tip
Don't let the LLM perform the statistics itself. Instead:

Backend/statistics engine computes objective metrics (conversion rates, p-values, confidence intervals, lift).
LLM receives those computed values as structured input and explains them in business-friendly language, validates configurations, and suggests next actions.
This separation is more reliable, aligns with the evaluation criteria (especially statistical significance accuracy), and gives judges confidence that your recommendations are grounded in real calculations rather than generated guesses.


