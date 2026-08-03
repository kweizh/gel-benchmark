# Jujutsu Benchmark

This repository contains benchmarks for [Jujutsu (jj)](https://github.com/martinvonz/jj), a next-generation version control system.

You can view the evaluation reports at [tabbyml.github.io/jj-benchmark](https://tabbyml.github.io/jj-benchmark/).

## Project Structure

- `tasks/`: Contains the benchmark tasks, each with its own instructions, bootstrap scripts, and tests.
- `jobs/`: Stores the results of benchmark runs.
- `site/`: A Next.js application to visualize benchmark results.

## Contribution

This benchmark is evaluated with the [Harbor framework](https://github.com/harbor-framework/harbor)
and the [Pochi agent](https://github.com/TabbyML/pochi).

Here is an example of running the evaluation with a built-in agent (e.g., Codex) and Daytona:

```bash
harbor run \
    --agent codex \
    --model "gpt-5.2-codex" \
    --env daytona \
    --path ./tasks \
    --n-attempts 1 \
    --max-retries 5 \
    --n-concurrent 5 \
    --retry-include RuntimeError \
    --retry-include DaytonaError \
    --retry-include AgentTimeoutError
```

### Evaluation Details

Before starting the evaluation, you should set the necessary environment variables.
For example, when using Codex, you should export `OPENAI_API_KEY` before running Harbor.
If using Pochi, you should export `POCHI_API_KEY`, etc.

Evaluation can be run locally with Docker, [Daytona.io](https://www.daytona.io/),
or other cloud services by using the `-e` or `--env` arguments with values like `docker` or `daytona` (`docker` is the default).

When running with Daytona, please note that Daytona blocks some network access for tier 1 and tier 2 users.
If you meet any network issues, please refer to
[Daytona network limits](https://www.daytona.io/docs/en/network-limits/).

People are welcome to contribute with built-in agents (e.g., supporting `claude-code`, `codex`, `gemini-cli`, etc.)
using the `--agent` or `-a` arguments, or other custom agents like the [Pochi agent](https://github.com/TabbyML/pochi).

For running the Pochi agent specifically,
you should use `--agent-import-path` to point to the path of the Pochi agent,
such as `agents.pochi:Pochi`, where `agents.pochi` is the import path and `Pochi` is the class name of the Pochi agent.
