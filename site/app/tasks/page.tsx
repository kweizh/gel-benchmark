import { Suspense } from "react";
import tasksDataRaw from "@/zealt/tasks.json";
import pendingTasksData from "@/zealt/pending-tasks.json";
import PendingReviewCard from "@/components/pending-review-card";
import { TasksPageClient, type CompactTask, type CompactTrial } from "./components/tasks-page-client";

type RawTaskTrial = {
  task_name?: string;
  job_name?: string;
  trial_name?: string;
  trajectory_id?: string;
  agent?: string;
  model?: string;
  passed?: boolean;
  reward?: number | null;
  error?: boolean;
  latency_sec?: number | null;
  latency_breakdown?: {
    env_setup?: number | null;
    agent_setup?: number | null;
    agent_exec?: number | null;
    verifier?: number | null;
  };
};

type RawTaskRecord = {
  instruction?: string;
  trials?: RawTaskTrial[];
};

type PendingTasksValue = {
  'pending-tasks'?: number;
};

function toCompactTrial(taskName: string, trial: RawTaskTrial): CompactTrial | null {
  if (!trial.job_name || !trial.trial_name || !trial.model || !trial.agent) {
    return null;
  }

  const canonicalTaskName = trial.task_name || taskName;
  const model = trial.model.split("/").pop() || trial.model;
  const agent = trial.agent ? trial.agent.charAt(0).toUpperCase() + trial.agent.slice(1) : "Unknown";
  const latencyBreakdown = {
    env_setup: trial.latency_breakdown?.env_setup ?? null,
    agent_setup: trial.latency_breakdown?.agent_setup ?? null,
    agent_exec: trial.latency_breakdown?.agent_exec ?? null,
    verifier: trial.latency_breakdown?.verifier ?? null,
  };

  return {
    job_name: trial.job_name,
    trial_name: trial.trial_name,
    ...(trial.trajectory_id ? { trajectory_id: trial.trajectory_id } : {}),
    model,
    rawModel: trial.model,
    agent,
    passed: Boolean(trial.passed),
    reward: trial.reward ?? null,
    error: Boolean(trial.error),
    latency_sec: trial.latency_sec ?? null,
    latency_breakdown: latencyBreakdown,
    taskName: canonicalTaskName,
    exec_duration: latencyBreakdown.agent_exec ?? trial.latency_sec ?? 0,
  };
}

function buildCompactTasksData(): CompactTask[] {
  const rawEntries = Object.entries(tasksDataRaw as Record<string, unknown>);
  const compactTasks: CompactTask[] = [];

  for (const [taskName, value] of rawEntries) {
    if (typeof value !== "object" || value === null) {
      continue;
    }

    const taskRecord = value as RawTaskRecord;
    const rawTrials = Array.isArray(taskRecord.trials) ? taskRecord.trials : [];
    const trials: CompactTrial[] = rawTrials
      .map((trial) => toCompactTrial(taskName, trial))
      .filter((trial): trial is CompactTrial => trial !== null);

    compactTasks.push({
      taskName,
      instruction: taskRecord.instruction || "",
      trials,
    });
  }

  compactTasks.sort((a, b) => a.taskName.localeCompare(b.taskName));
  return compactTasks;
}

export default function TasksPage() {
  const compactTasksData = buildCompactTasksData();
  const pendingSampleCases = Math.max(
    0,
    Number((pendingTasksData as PendingTasksValue)['pending-tasks'] ?? 0),
  );

  return (
    <div className="min-h-screen bg-background text-foreground font-sans selection:bg-primary/20">
      <div className="fixed inset-0 -z-10 h-full w-full bg-background bg-[radial-gradient(#2a2a2a_1px,transparent_1px)] [background-size:16px_16px] [mask-image:radial-gradient(ellipse_50%_50%_at_50%_50%,#000_70%,transparent_100%)] opacity-20 dark:opacity-40"></div>

      {compactTasksData.length === 0 ? (
        <div className="container mx-auto px-4 sm:px-8 lg:px-12 py-8 max-w-screen-2xl h-[100dvh] flex flex-col overflow-hidden">
          <div className="mb-6 space-y-4 shrink-0">
            <div className="flex items-center gap-4">
              <a href="/" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
                &larr; Back to Leaderboard
              </a>
            </div>
            <div>
              <h1 className="text-4xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-b from-foreground to-foreground/50">
                Task
              </h1>
            </div>
          </div>

          <PendingReviewCard pendingSampleCases={pendingSampleCases} />
        </div>
      ) : (
        <Suspense fallback={<div className="container mx-auto py-8 text-center">Loading tasks...</div>}>
          <TasksPageClient tasksData={compactTasksData} />
        </Suspense>
      )}
    </div>
  );
}
