import { useMemo, useState } from "react";
import type { DAGTask, MissionDAGPlan } from "@/types/mission";
import styles from "./DAGView.module.css";

interface DAGViewProps {
  plan: MissionDAGPlan;
  activeTaskId?: string;
  onSelectTask?: (task: DAGTask) => void;
}

export function DAGView({ plan, activeTaskId, onSelectTask }: DAGViewProps) {
  const [search, setSearch] = useState("");
  const [selectedRole, setSelectedRole] = useState("all");
  const [selectedPriority, setSelectedPriority] = useState("all");

  const roles = useMemo(() => {
    const set = new Set<string>();
    plan.tasks.forEach((t) => set.add(t.assigned_role));
    return Array.from(set).sort();
  }, [plan.tasks]);

  const uniqueRequirements = useMemo(() => {
    const set = new Set<string>();
    plan.tasks.forEach((t) => t.requirement_refs?.forEach((r) => set.add(r)));
    return Array.from(set).sort();
  }, [plan.tasks]);

  const filteredTasks = useMemo(() => {
    return plan.tasks.filter((t) => {
      const matchSearch =
        !search ||
        t.name.toLowerCase().includes(search.toLowerCase()) ||
        t.task_id.toLowerCase().includes(search.toLowerCase()) ||
        t.requirement_refs?.some((r) => r.toLowerCase().includes(search.toLowerCase()));

      const matchRole = selectedRole === "all" || t.assigned_role === selectedRole;
      const matchPriority = selectedPriority === "all" || t.priority === selectedPriority;

      return matchSearch && matchRole && matchPriority;
    });
  }, [plan.tasks, search, selectedRole, selectedPriority]);

  return (
    <div className={styles.container} aria-label="Mission Task DAG">
      {/* Summary and Filters Toolbar */}
      <div className={styles.toolbar}>
        <div className={styles.stats}>
          <div className={styles.statItem}>
            <span>Tasks:</span>
            <strong>{plan.tasks.length}</strong>
          </div>
          <div className={styles.statItem}>
            <span>Parallelism:</span>
            <strong>{plan.max_parallel_tasks}</strong>
          </div>
          <div className={styles.statItem}>
            <span>Requirements:</span>
            <strong>{uniqueRequirements.length}</strong>
          </div>
        </div>

        <div className={styles.filters}>
          <input
            type="search"
            placeholder="Search tasks or requirements..."
            aria-label="Filter tasks"
            className={styles.searchInput}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          <select
            className={styles.selectFilter}
            aria-label="Filter by assigned role"
            value={selectedRole}
            onChange={(e) => setSelectedRole(e.target.value)}
          >
            <option value="all">All Roles</option>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>

          <select
            className={styles.selectFilter}
            aria-label="Filter by priority"
            value={selectedPriority}
            onChange={(e) => setSelectedPriority(e.target.value)}
          >
            <option value="all">All Priorities</option>
            <option value="P0">P0 (Critical)</option>
            <option value="P1">P1 (High)</option>
            <option value="P2">P2 (Medium)</option>
            <option value="P3">P3 (Low)</option>
          </select>
        </div>
      </div>

      {/* Grid of Task Nodes */}
      {filteredTasks.length === 0 ? (
        <div style={{ padding: "var(--space-6)", textAlign: "center", color: "var(--color-text-muted)" }}>
          No tasks match the filter criteria.
        </div>
      ) : (
        <div className={styles.dagGrid}>
          {filteredTasks.map((task) => {
            const isSelected = activeTaskId === task.task_id;
            const priorityClass =
              task.priority === "P0"
                ? styles.p0Badge
                : task.priority === "P1"
                ? styles.p1Badge
                : styles.p2Badge;

            return (
              <div
                key={task.task_id}
                className={`${styles.taskCard} ${isSelected ? styles.selectedCard : ""}`}
                onClick={() => onSelectTask?.(task)}
                tabIndex={0}
                role="button"
                aria-label={`Task ${task.name}`}
              >
                <div className={styles.taskCardHeader}>
                  <div>
                    <h3 className={styles.taskTitle}>{task.name}</h3>
                    <div className={styles.taskId}>{task.task_id}</div>
                  </div>
                </div>

                <div className={styles.badgeRow}>
                  <span className={styles.roleBadge}>
                    <span>🤖</span>
                    <span>{task.assigned_role}</span>
                  </span>

                  <span className={styles.adapterBadge}>
                    <span>{task.adapter_type === "web" ? "🌐 Web" : "⚡ API"}</span>
                  </span>

                  <span className={`${styles.priorityBadge} ${priorityClass}`}>
                    {task.priority}
                  </span>

                  {task.is_smoke && (
                    <span className={styles.smokeBadge}>
                      <span>🔥 Smoke</span>
                    </span>
                  )}

                  {task.is_regression && (
                    <span className={styles.regressionBadge}>
                      <span>🔄 Regression</span>
                    </span>
                  )}
                </div>

                <div className={styles.metaSection}>
                  <div className={styles.metaRow}>
                    <span className={styles.metaLabel}>Depends on:</span>
                    <div className={styles.tagList}>
                      {task.depends_on && task.depends_on.length > 0 ? (
                        task.depends_on.map((dep) => (
                          <span key={dep} className={styles.depTag}>
                            {dep}
                          </span>
                        ))
                      ) : (
                        <span className={styles.noDeps}>Root Task (None)</span>
                      )}
                    </div>
                  </div>

                  <div className={styles.metaRow}>
                    <span className={styles.metaLabel}>Requirements:</span>
                    <div className={styles.tagList}>
                      {task.requirement_refs && task.requirement_refs.length > 0 ? (
                        task.requirement_refs.map((req) => (
                          <span key={req} className={styles.reqTag}>
                            {req}
                          </span>
                        ))
                      ) : (
                        <span style={{ color: "var(--color-text-muted)" }}>None</span>
                      )}
                    </div>
                  </div>

                  <div className={styles.metaRow}>
                    <span className={styles.metaLabel}>Steps / Timeout:</span>
                    <span>
                      {task.steps?.length ?? 0} step(s) · {task.timeout_seconds}s
                    </span>
                  </div>

                  {task.exports && Object.keys(task.exports).length > 0 && (
                    <div className={styles.metaRow}>
                      <span className={styles.metaLabel}>Exports:</span>
                      <div className={styles.tagList}>
                        {Object.entries(task.exports).map(([k, exp]) => (
                          <span key={k} className={styles.reqTag} title={`JSONPath: ${exp.json_path}`}>
                            ${`{${k}}`}{exp.is_secret ? " (secret)" : ""}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
