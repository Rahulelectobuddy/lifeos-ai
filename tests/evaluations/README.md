# Evaluation datasets

planning.json and content.json contain scenario, expected behavior, and checks.
Use `uv run python -m tests.evaluations.validate` to validate dataset structure.
The deterministic pytest cases cover the corresponding safety/behavior assertions.
These are acceptance scenarios, not a claim that a real model has passed quality
assessment. To evaluate your configured DGX, submit each request through the bot
in a test account, inspect proposals before approval, and record the model ID,
latency, schema failures, factual errors, and human review outcomes. Do not run
paid media generation or approve changes in a production account as a quality test.
